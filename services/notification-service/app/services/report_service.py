"""Business Logic xử lý Report (Ticket & Quick Error) cho Người dùng (citizen), Luật sư (lawyer) và Admin.

Tích hợp RabbitMQ theo kiến trúc Event-Driven:
- Lưu Report đồng bộ vào DB để đảm bảo tính toàn vẹn & trả về mã `RPT-YYYY-NNNNNN` ngay lập tức.
- Phát sự kiện RabbitMQ (`report.created`, `report.accepted`, `report.rejected`, `report.resolved`)
  để Notification Hub tạo thông báo cho Admin và phản hồi kết quả cho người báo cáo.
- Cơ chế Graceful Degradation: nếu RabbitMQ chưa bật hoặc mất kết nối (`publish_envelope` trả về `False`),
  service tự động gọi thẳng `handle_event` nội bộ để không bao giờ mất thông báo của người dùng/admin.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import math
import os
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common_auth.events import make_event, publisher
from fastapi.responses import FileResponse
from sqlalchemy import and_, case, desc, func, or_, select, text, true
from sqlalchemy.exc import IntegrityError

from app.core.notification_store import (
    ReportAttachmentRecord,
    ReportCategoryRecord,
    ReportReasonRecord,
    ReportRecord,
    ReportStatusHistoryRecord,
    _parse_dt,
    execute_readonly,
    get_engine,
    get_session,
)
from app.core.rabbitmq_notification_hub import handle_event
from app.models.reports import REJECT_REASON_CATALOG
from app.schemas.reports import (
    AdminAcceptResolveRequest,
    AdminClaimReleaseRequest,
    AdminRejectRequest,
    QuickErrorCreateRequest,
    TicketCreateRequest,
)

logger = logging.getLogger("ReportService")

ALLOWED_TRANSITIONS: Dict[str, set[str]] = {
    "pending": {"in_review", "accepted", "rejected", "cancelled"},
    "in_review": {"accepted", "rejected", "pending"},
    "accepted": {"resolved"},
    "rejected": set(),
    "resolved": set(),
    "cancelled": set(),
}

ALLOWED_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp"}
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024  # 5MB
_CATALOG_TTL_SECONDS = 300.0
_catalog_lock = threading.Lock()
_catalog_cache: Dict[Tuple[int, str, Optional[str]], Tuple[float, Dict[str, Any]]] = {}
_ICT_TZ = timezone(timedelta(hours=7))


class ReportDomainError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _detect_image_mime(content: bytes) -> Optional[str]:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


_REPORT_CODE_LOCK_NS = 7102026
_QUICK_ERROR_DEDUP_LOCK_NS = 7102027
_MAX_CODE_ALLOCATION_ATTEMPTS = 5


def _acquire_pg_advisory_xact_lock(session, namespace: int, lock_key: int) -> None:
    """Khóa transaction-scoped trên PostgreSQL (an toàn với PgBouncer transaction-pooling, tự nhả khi commit/rollback)."""
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :key)"),
            {"ns": int(namespace), "key": int(lock_key)},
        )


def _fingerprint_lock_key(fingerprint: str) -> int:
    """Chuyển 4 byte đầu của SHA-256 hex sang int32 có dấu để dùng với pg_advisory_xact_lock(int4, int4)."""
    return int.from_bytes(bytes.fromhex(fingerprint[:8]), "big", signed=True)


def _is_report_code_unique_error(exc: IntegrityError) -> bool:
    orig = getattr(exc, "orig", None)
    if getattr(orig, "pgcode", None) == "23505" or getattr(orig, "sqlstate", None) == "23505":
        detail = str(orig).lower()
        return "code" in detail or "reports" in detail
    msg = str(exc).lower()
    return "unique" in msg and ("reports.code" in msg or "ix_reports_code" in msg or "code" in msg)


def _generate_report_code(session) -> str:
    year = datetime.now(timezone.utc).year
    _acquire_pg_advisory_xact_lock(session, _REPORT_CODE_LOCK_NS, year)
    prefix = f"RPT-{year}-"
    max_code = (
        session.query(func.max(ReportRecord.code))
        .filter(ReportRecord.code.like(f"{prefix}%"))
        .scalar()
    )
    last_seq = 0
    if isinstance(max_code, str) and max_code.startswith(prefix):
        try:
            last_seq = int(max_code[len(prefix):])
        except ValueError:
            last_seq = 0
    return f"{prefix}{last_seq + 1:06d}"


def _compute_quick_error_fingerprint(
    reporter_id: str,
    reason_code: str,
    feature_code: Optional[str],
    context: Dict[str, Any],
) -> str:
    route = str(context.get("route") or context.get("page") or "").split("?")[0]
    err_code = str(context.get("error_code") or context.get("http_status") or "")
    raw = f"{reporter_id}|{reason_code}|{feature_code or ''}|{err_code}|{route}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _emit_report_event(routing_key: str, payload: Dict[str, Any]) -> None:
    """Phát sự kiện lên RabbitMQ; nếu broker tắt hoặc lỗi thì fallback xử lý trực tiếp để đảm bảo luôn có thông báo."""
    event = make_event(routing_key, payload, event_id=f"evt-rpt-{uuid.uuid4().hex}")
    if await publisher.publish_envelope(routing_key, event) is False:
        await handle_event(routing_key, event)


def get_report_catalog(user_role: str, kind: Optional[str] = None) -> Dict[str, Any]:
    """Lấy danh sách nhóm vấn đề (categories) và nguyên nhân (reasons) theo role của người gọi."""
    engine_key = id(get_engine())
    normalized_kind = kind if kind in ("ticket", "quick_error") else None
    cache_key = (engine_key, str(user_role), normalized_kind)
    now_mono = time.monotonic()

    with _catalog_lock:
        cached = _catalog_cache.get(cache_key)
        if cached and cached[0] > now_mono:
            return copy.deepcopy(cached[1])

    role_filter = "citizen" if user_role not in ("citizen", "lawyer") else user_role
    C = ReportCategoryRecord.__table__
    R = ReportReasonRecord.__table__

    def _query(conn):
        cats = conn.execute(
            select(C).where(C.c.is_active.is_(True)).order_by(C.c.sort_order, C.c.code)
        ).mappings().all()
        rsn_stmt = select(R).where(R.c.is_active.is_(True))
        if normalized_kind:
            rsn_stmt = rsn_stmt.where(R.c.kind == normalized_kind)
        rsns = conn.execute(rsn_stmt.order_by(R.c.sort_order, R.c.code)).mappings().all()
        return cats, rsns

    cat_rows, rsn_rows = execute_readonly(_query)

    reasons: List[Dict[str, Any]] = []
    reasons_by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for r in rsn_rows:
        allowed_roles = [role.strip() for role in (r["allowed_roles"] or "").split(",") if role.strip()]
        if user_role != "admin" and role_filter not in allowed_roles:
            continue
        r_dict = {
            "code": r["code"],
            "category_code": r["category_code"],
            "kind": r["kind"],
            "name_vi": r["name_vi"],
            "allowed_roles": allowed_roles,
            "default_priority": r["default_priority"],
            "requires_target": bool(r["requires_target"]),
            "requires_note": bool(r["requires_note"]),
            "sort_order": int(r["sort_order"] or 0),
            "is_active": bool(r["is_active"]),
        }
        reasons.append(r_dict)
        if r_dict["kind"] == "ticket" and r_dict["category_code"]:
            reasons_by_cat.setdefault(r_dict["category_code"], []).append(r_dict)

    categories: List[Dict[str, Any]] = []
    for c in cat_rows:
        allowed_roles = [role.strip() for role in (c["allowed_roles"] or "").split(",") if role.strip()]
        if user_role != "admin" and role_filter not in allowed_roles:
            continue
        cat_code = c["code"]
        categories.append(
            {
                "code": cat_code,
                "name_vi": c["name_vi"],
                "description": c["description"],
                "allowed_roles": allowed_roles,
                "sort_order": int(c["sort_order"] or 0),
                "is_active": bool(c["is_active"]),
                "reasons": reasons_by_cat.get(cat_code, []),
            }
        )

    ticket_reasons = [r for r in reasons if r["kind"] == "ticket"]
    quick_error_reasons = [r for r in reasons if r["kind"] == "quick_error"]
    catalog_payload: Dict[str, Any] = {
        "categories": categories,
        "reasons": quick_error_reasons if normalized_kind == "quick_error" else (ticket_reasons if normalized_kind == "ticket" else reasons),
        "ticket_reasons": ticket_reasons,
        "quick_error_reasons": quick_error_reasons,
        "reject_reasons": [{"code": k, "name_vi": v} for k, v in REJECT_REASON_CATALOG.items()],
    }

    with _catalog_lock:
        if len(_catalog_cache) > 64:
            _catalog_cache.clear()
        _catalog_cache[cache_key] = (now_mono + _CATALOG_TTL_SECONDS, catalog_payload)

    return copy.deepcopy(catalog_payload)


def attachments_dir() -> Path:
    return Path(os.getenv("ATTACHMENTS_DIR", "data/report_attachments"))


def get_attachment_response(attachment_id: str, viewer_id: str, viewer_role: str) -> FileResponse:
    A = ReportAttachmentRecord.__table__
    T = ReportRecord.__table__

    def _query(conn):
        stmt = (
            select(
                A.c.uploader_id,
                A.c.storage_key,
                A.c.mime_type,
                A.c.file_name,
                T.c.reporter_id.label("report_reporter_id"),
            )
            .select_from(A.outerjoin(T, T.c.id == A.c.report_id))
            .where(A.c.id == str(attachment_id))
        )
        return conn.execute(stmt).mappings().first()

    attachment = execute_readonly(_query)
    if not attachment or (
        viewer_role != "admin"
        and attachment["uploader_id"] != str(viewer_id)
        and attachment["report_reporter_id"] != str(viewer_id)
    ):
        raise ReportDomainError(404, "ATTACHMENT_NOT_FOUND", "Không tìm thấy tệp đính kèm.")
    root = attachments_dir().resolve()
    path = Path(attachment["storage_key"]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ReportDomainError(404, "ATTACHMENT_NOT_FOUND", "Không tìm thấy tệp đính kèm.")
    return FileResponse(
        path,
        media_type=attachment["mime_type"],
        filename=attachment["file_name"],
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


def save_attachment(
    uploader_id: str,
    file_name: str,
    mime_type: str,
    content: bytes,
) -> Dict[str, Any]:
    if len(content) == 0:
        raise ReportDomainError(
            422,
            "INVALID_FILE_SIZE",
            "Tệp ảnh rỗng hoặc không hợp lệ.",
        )
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise ReportDomainError(
            413,
            "FILE_TOO_LARGE",
            "Kích thước ảnh không được vượt quá 5MB.",
        )

    clean_mime = (mime_type or "").split(";")[0].strip().lower()
    if clean_mime == "image/jpg":
        clean_mime = "image/jpeg"
    detected_mime = _detect_image_mime(content)
    if clean_mime not in ALLOWED_IMAGE_MIMES or detected_mime != clean_mime:
        raise ReportDomainError(
            415,
            "UNSUPPORTED_MEDIA_TYPE",
            "Nội dung tệp không phải ảnh PNG, JPEG hoặc WEBP hợp lệ.",
        )

    att_id = f"att-{uuid.uuid4().hex}"
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[clean_mime]
    upload_dir = attachments_dir()
    upload_dir.mkdir(parents=True, exist_ok=True)
    storage_path = upload_dir / f"{att_id}{ext}"
    storage_path.write_bytes(content)

    now = datetime.now(timezone.utc)
    with get_session() as session:
        rec = ReportAttachmentRecord(
            id=att_id,
            report_id=None,
            uploader_id=str(uploader_id),
            storage_key=str(storage_path),
            file_name=file_name[:255] or f"screenshot{ext}",
            mime_type=clean_mime,
            size_bytes=len(content),
            created_at=now,
        )
        session.add(rec)
        session.commit()
        return {**rec.to_dict(), "attachment_id": rec.id, "url": f"/api/reports/attachments/{rec.id}"}


def _bind_attachments(session, report_id: str, uploader_id: str, attachment_ids: List[str]) -> None:
    if not attachment_ids:
        return
    rows = (
        session.query(ReportAttachmentRecord)
        .filter(
            ReportAttachmentRecord.id.in_(attachment_ids),
            ReportAttachmentRecord.uploader_id == str(uploader_id),
            ReportAttachmentRecord.report_id.is_(None),
        )
        .all()
    )
    for row in rows:
        row.report_id = report_id


def _create_ticket_report_sync(
    reporter_id: str,
    reporter_role: str,
    req: TicketCreateRequest,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    for attempt in range(_MAX_CODE_ALLOCATION_ATTEMPTS):
        with get_session() as session:
            category = session.get(ReportCategoryRecord, req.category_code)
            if not category or not category.is_active:
                raise ReportDomainError(422, "INVALID_CATEGORY", f"Nhóm vấn đề '{req.category_code}' không hợp lệ.")
            cat_roles = [r.strip() for r in (category.allowed_roles or "").split(",") if r.strip()]
            if reporter_role not in cat_roles:
                raise ReportDomainError(403, "CATEGORY_ROLE_MISMATCH", "Nhóm vấn đề này không áp dụng cho vai trò của bạn.")

            reason = session.get(ReportReasonRecord, req.reason_code)
            if not reason or not reason.is_active or reason.kind != "ticket":
                raise ReportDomainError(422, "INVALID_REASON", f"Nguyên nhân '{req.reason_code}' không hợp lệ cho ticket.")
            if reason.category_code != req.category_code:
                raise ReportDomainError(
                    422,
                    "REASON_CATEGORY_MISMATCH",
                    f"Nguyên nhân '{req.reason_code}' không thuộc nhóm '{req.category_code}'.",
                )
            rsn_roles = [r.strip() for r in (reason.allowed_roles or "").split(",") if r.strip()]
            if reporter_role not in rsn_roles:
                raise ReportDomainError(403, "REASON_ROLE_MISMATCH", "Nguyên nhân này không áp dụng cho vai trò của bạn.")

            if reason.requires_target and not (req.target_id and req.target_id.strip()):
                raise ReportDomainError(
                    422,
                    "TARGET_REQUIRED",
                    "Nguyên nhân này bắt buộc phải chọn đối tượng liên quan (target_id).",
                )
            if reason.requires_note and len(req.description.strip()) < 30:
                raise ReportDomainError(
                    422,
                    "DESCRIPTION_TOO_SHORT",
                    "Vui lòng mô tả chi tiết tối thiểu 30 ký tự đối với nguyên nhân 'Vấn đề khác'.",
                )

            priority = "critical" if req.category_code == "SECURITY" else (reason.default_priority or "normal")
            report_id = str(uuid.uuid4())
            code = _generate_report_code(session)

            record = ReportRecord(
                id=report_id,
                code=code,
                kind="ticket",
                reporter_id=str(reporter_id),
                reporter_role=reporter_role,
                category_code=req.category_code,
                reason_code=req.reason_code,
                title=req.title.strip(),
                description=req.description.strip(),
                priority=priority,
                status="pending",
                feature_code=req.feature_code,
                target_type=req.target_type,
                target_id=req.target_id,
                context_json="{}",
                occurrence_count=1,
                version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            _bind_attachments(session, report_id, reporter_id, req.attachment_ids)
            session.add(
                ReportStatusHistoryRecord(
                    report_id=report_id,
                    from_status=None,
                    to_status="pending",
                    actor_id=str(reporter_id),
                    actor_role=reporter_role,
                    note="Tạo ticket báo cáo mới",
                    created_at=now,
                )
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                if _is_report_code_unique_error(exc) and attempt < _MAX_CODE_ALLOCATION_ATTEMPTS - 1:
                    logger.warning("Trùng mã report %s khi tạo ticket (lần %d), thử cấp lại mã mới", code, attempt + 1)
                    continue
                raise
            return record.to_dict()
    raise ReportDomainError(409, "REPORT_CODE_CONFLICT", "Hệ thống đang bận cấp mã báo cáo, vui lòng thử lại.")


async def create_ticket_report(
    reporter_id: str,
    reporter_role: str,
    req: TicketCreateRequest,
) -> Dict[str, Any]:
    """Tạo Ticket Report cho một vấn đề trong hệ thống (dành cho citizen và lawyer)."""
    if reporter_role not in ("citizen", "lawyer"):
        raise ReportDomainError(
            403,
            "FORBIDDEN_ROLE",
            "Chỉ người dùng (citizen) và luật sư (lawyer) mới tạo ticket báo cáo.",
        )

    result = await asyncio.to_thread(_create_ticket_report_sync, reporter_id, reporter_role, req)
    await _emit_report_event(
        "report.created",
        {
            "report_id": result["id"],
            "code": result["code"],
            "kind": "ticket",
            "reporter_id": str(reporter_id),
            "reporter_role": reporter_role,
            "category_code": req.category_code,
            "reason_code": req.reason_code,
            "title": result["title"],
            "priority": result["priority"],
            "feature_code": req.feature_code,
        },
    )
    return result


def _create_quick_error_report_sync(
    reporter_id: str,
    reporter_role: str,
    req: QuickErrorCreateRequest,
) -> Tuple[Dict[str, Any], bool, Optional[str]]:
    now = datetime.now(timezone.utc)
    ctx_base = dict(req.context or {})
    feature_code = req.feature_code or ctx_base.get("feature_code") or "OTHER"
    target_type = req.target_type or ctx_base.get("target_type")
    target_id = req.target_id or ctx_base.get("target_id")

    for attempt in range(_MAX_CODE_ALLOCATION_ATTEMPTS):
        ctx = dict(ctx_base)
        with get_session() as session:
            reason = session.get(ReportReasonRecord, req.reason_code)
            if not reason or not reason.is_active or reason.kind != "quick_error":
                raise ReportDomainError(
                    422,
                    "INVALID_QUICK_ERROR_REASON",
                    f"Mã nguyên nhân báo lỗi nhanh '{req.reason_code}' không hợp lệ.",
                )
            if reason.requires_note and not (req.note and len(req.note.strip()) >= 5):
                raise ReportDomainError(
                    422,
                    "NOTE_REQUIRED",
                    "Vui lòng nhập ghi chú mô tả ngắn cho lựa chọn 'Lỗi khác'.",
                )

            fingerprint = _compute_quick_error_fingerprint(
                reporter_id=str(reporter_id),
                reason_code=req.reason_code,
                feature_code=str(feature_code),
                context=ctx,
            )
            # Khóa theo fingerprint trước khi kiểm tra trùng (thứ tự khóa cố định: DEDUP -> REPORT_CODE, không deadlock).
            _acquire_pg_advisory_xact_lock(session, _QUICK_ERROR_DEDUP_LOCK_NS, _fingerprint_lock_key(fingerprint))
            dedup_cutoff = now - timedelta(minutes=10)
            existing_list = (
                session.query(ReportRecord)
                .filter(
                    ReportRecord.fingerprint == fingerprint,
                    ReportRecord.status.in_(["pending", "in_review"]),
                )
                .order_by(desc(ReportRecord.created_at))
                .all()
            )
            for existing in existing_list:
                created_dt = (
                    existing.created_at
                    if existing.created_at.tzinfo
                    else existing.created_at.replace(tzinfo=timezone.utc)
                )
                if created_dt >= dedup_cutoff:
                    existing.occurrence_count = (existing.occurrence_count or 1) + 1
                    existing.updated_at = now
                    if req.note and req.note.strip() and not existing.description:
                        existing.description = req.note.strip()
                    session.commit()
                    res = existing.to_dict()
                    res["duplicated"] = True
                    return res, True, None

            priority = reason.default_priority or "normal"
            http_status = ctx.get("http_status")
            if isinstance(http_status, int) and http_status >= 500 and priority in ("low", "normal"):
                priority = "high"

            if "diagnostic_summary" not in ctx:
                ctx["diagnostic_summary"] = (
                    f"Feature={str(feature_code) if feature_code else 'N/A'} | "
                    f"HTTP={ctx.get('http_status', 'N/A')} | "
                    f"Error={ctx.get('error_code', 'N/A')} | "
                    f"ReqID={ctx.get('request_id', 'N/A')}"
                )

            title = f"[Báo lỗi] {reason.name_vi} - {feature_code}"
            report_id = str(uuid.uuid4())
            code = _generate_report_code(session)

            record = ReportRecord(
                id=report_id,
                code=code,
                kind="quick_error",
                reporter_id=str(reporter_id),
                reporter_role=reporter_role,
                category_code=None,
                reason_code=req.reason_code,
                title=title[:200],
                description=(req.note or "").strip() or ctx.get("error_message"),
                priority=priority,
                status="pending",
                feature_code=str(feature_code)[:40],
                target_type=str(target_type)[:40] if target_type else None,
                target_id=str(target_id)[:64] if target_id else None,
                context_json=json.dumps(ctx, ensure_ascii=False, default=str),
                fingerprint=fingerprint,
                occurrence_count=1,
                version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            _bind_attachments(session, report_id, reporter_id, req.attachment_ids)
            session.add(
                ReportStatusHistoryRecord(
                    report_id=report_id,
                    from_status=None,
                    to_status="pending",
                    actor_id=str(reporter_id),
                    actor_role=reporter_role,
                    note="Gửi báo lỗi nhanh từ giao diện thao tác",
                    created_at=now,
                )
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                if _is_report_code_unique_error(exc) and attempt < _MAX_CODE_ALLOCATION_ATTEMPTS - 1:
                    logger.warning("Trùng mã report %s khi tạo quick error (lần %d), thử cấp lại mã mới", code, attempt + 1)
                    continue
                raise
            result = record.to_dict()
            result["duplicated"] = False
            return result, False, str(feature_code)
    raise ReportDomainError(409, "REPORT_CODE_CONFLICT", "Hệ thống đang bận cấp mã báo cáo, vui lòng thử lại.")


async def create_quick_error_report(
    reporter_id: str,
    reporter_role: str,
    req: QuickErrorCreateRequest,
) -> Tuple[Dict[str, Any], bool]:
    """Gửi báo lỗi nhanh khi người dùng/luật sư bấm vào chữ 'Báo lỗi' ngay lúc gặp sự cố.

    Trả về `(report_dict, duplicated)`. Nếu cùng lỗi lặp lại trong vòng 10 phút và report còn mở,
    hệ thống cộng dồn `occurrence_count` thay vì tạo report trùng.
    """
    if reporter_role not in ("citizen", "lawyer"):
        raise ReportDomainError(
            403,
            "FORBIDDEN_ROLE",
            "Chỉ người dùng (citizen) và luật sư (lawyer) mới gửi báo lỗi.",
        )

    result, duplicated, emitted_feature = await asyncio.to_thread(
        _create_quick_error_report_sync,
        reporter_id,
        reporter_role,
        req,
    )
    if duplicated:
        return result, True

    await _emit_report_event(
        "report.created",
        {
            "report_id": result["id"],
            "code": result["code"],
            "kind": "quick_error",
            "reporter_id": str(reporter_id),
            "reporter_role": reporter_role,
            "category_code": None,
            "reason_code": req.reason_code,
            "title": result["title"],
            "priority": result["priority"],
            "feature_code": emitted_feature,
        },
    )
    return result, False


def _iso_or_none(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, str):
        return val
    return _parse_dt(val).astimezone(timezone.utc).isoformat()


def _report_row_to_dict(row: Any, *, include_context: bool = False) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "id": row["id"],
        "code": row["code"],
        "kind": row["kind"],
        "reporter_id": row["reporter_id"],
        "reporter_role": row["reporter_role"],
        "category_code": row["category_code"],
        "reason_code": row["reason_code"],
        "title": row["title"],
        "description": row["description"],
        "priority": row["priority"],
        "status": row["status"],
        "feature_code": row["feature_code"],
        "target_type": row["target_type"],
        "target_id": row["target_id"],
        "occurrence_count": int(row["occurrence_count"] or 1),
        "duplicate_of": row["duplicate_of"],
        "assignee_id": row["assignee_id"],
        "reject_reason_code": row["reject_reason_code"],
        "decision_note": row["decision_note"],
        "decided_by": row["decided_by"],
        "decided_at": _iso_or_none(row["decided_at"]),
        "resolved_at": _iso_or_none(row["resolved_at"]),
        "version": int(row["version"] or 1),
        "created_at": _iso_or_none(row["created_at"]),
        "updated_at": _iso_or_none(row["updated_at"]),
    }
    if include_context:
        try:
            raw_ctx = row["context_json"]
            ctx = json.loads(raw_ctx) if raw_ctx else {}
        except Exception:
            ctx = {}
        data["context"] = ctx
    return data


def _report_list_columns(T, *, include_context: bool = False):
    cols = [
        T.c.id,
        T.c.code,
        T.c.kind,
        T.c.reporter_id,
        T.c.reporter_role,
        T.c.category_code,
        T.c.reason_code,
        T.c.title,
        T.c.description,
        T.c.priority,
        T.c.status,
        T.c.feature_code,
        T.c.target_type,
        T.c.target_id,
        T.c.occurrence_count,
        T.c.duplicate_of,
        T.c.assignee_id,
        T.c.reject_reason_code,
        T.c.decision_note,
        T.c.decided_by,
        T.c.decided_at,
        T.c.resolved_at,
        T.c.version,
        T.c.created_at,
        T.c.updated_at,
    ]
    if include_context:
        cols.append(T.c.context_json)
    return cols


def _run_paged_report_query(conditions: List[Any], order_cols: Tuple[Any, ...], limit: int, offset: int) -> Dict[str, Any]:
    T = ReportRecord.__table__
    counts_stmt = select(func.count(T.c.id).label("total_count")).select_from(T)
    page_stmt = select(*_report_list_columns(T, include_context=False)).select_from(T)
    for cond in conditions:
        counts_stmt = counts_stmt.where(cond)
        page_stmt = page_stmt.where(cond)

    counts_cte = counts_stmt.cte("counts")
    page_cte = page_stmt.order_by(*order_cols).limit(limit).offset(offset).cte("page")
    stmt = (
        select(counts_cte.c.total_count, page_cte)
        .select_from(counts_cte.outerjoin(page_cte, true()))
        .order_by(*(getattr(page_cte.c, col.element.name).desc() if hasattr(col, "element") else col for col in order_cols))
    )
    rows = execute_readonly(lambda conn: conn.execute(stmt).mappings().all())
    total = int(rows[0]["total_count"] or 0) if rows else 0
    items = [_report_row_to_dict(r, include_context=False) for r in rows if r["id"] is not None]
    page = (offset // limit) + 1
    total_pages = max(1, math.ceil(total / limit))
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "pagination": {
            "page": page,
            "page_size": limit,
            "total": total,
            "total_pages": total_pages,
        },
    }


def list_my_reports(
    reporter_id: str,
    status: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    T = ReportRecord.__table__
    conditions: List[Any] = [T.c.reporter_id == str(reporter_id)]
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if statuses:
            conditions.append(T.c.status.in_(statuses))
    if kind in ("ticket", "quick_error"):
        conditions.append(T.c.kind == kind)
    return _run_paged_report_query(conditions, (desc(T.c.created_at), desc(T.c.id)), limit, offset)


def get_report_detail(report_id: str, viewer_id: str, viewer_role: str) -> Dict[str, Any]:
    T = ReportRecord.__table__
    A = ReportAttachmentRecord.__table__
    H = ReportStatusHistoryRecord.__table__
    rid = str(report_id)
    prefix = "/api/admin/reports/attachments" if viewer_role == "admin" else "/api/reports/attachments"

    def _query(conn):
        if conn.dialect.name == "postgresql":
            row = conn.execute(
                text(
                    "SELECT r.*, "
                    "COALESCE(("
                    "  SELECT json_agg(json_build_object("
                    "    'id', a.id, 'report_id', a.report_id, 'uploader_id', a.uploader_id, "
                    "    'file_name', a.file_name, 'mime_type', a.mime_type, 'size_bytes', a.size_bytes, "
                    "    'created_at', to_char(a.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"')"
                    "  ) ORDER BY a.created_at, a.id) "
                    "  FROM public.report_attachments a WHERE a.report_id = r.id"
                    "), '[]'::json) AS attachments_agg, "
                    "COALESCE(("
                    "  SELECT json_agg(json_build_object("
                    "    'id', h.id, 'report_id', h.report_id, 'from_status', h.from_status, "
                    "    'to_status', h.to_status, 'actor_id', h.actor_id, 'actor_role', h.actor_role, "
                    "    'note', h.note, "
                    "    'created_at', to_char(h.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"')"
                    "  ) ORDER BY h.created_at, h.id) "
                    "  FROM public.report_status_history h WHERE h.report_id = r.id"
                    "), '[]'::json) AS history_agg "
                    "FROM public.reports r WHERE r.id = :rid"
                ),
                {"rid": rid},
            ).mappings().first()
            return row, None, None

        row = conn.execute(select(T).where(T.c.id == rid)).mappings().first()
        if not row:
            return None, [], []
        atts = conn.execute(select(A).where(A.c.report_id == rid).order_by(A.c.created_at, A.c.id)).mappings().all()
        hist = conn.execute(select(H).where(H.c.report_id == rid).order_by(H.c.created_at, H.c.id)).mappings().all()
        return row, atts, hist

    row, att_rows, hist_rows = execute_readonly(_query)
    if not row:
        raise ReportDomainError(404, "REPORT_NOT_FOUND", "Không tìm thấy báo cáo.")
    if viewer_role != "admin" and row["reporter_id"] != str(viewer_id):
        raise ReportDomainError(403, "FORBIDDEN", "Bạn không có quyền xem báo cáo này.")

    data = _report_row_to_dict(row, include_context=True)
    if att_rows is None:
        raw_atts = row["attachments_agg"] or []
        if isinstance(raw_atts, str):
            raw_atts = json.loads(raw_atts)
        data["attachments"] = [{**a, "attachment_id": a["id"], "url": f"{prefix}/{a['id']}"} for a in raw_atts]
        raw_hist = row["history_agg"] or []
        if isinstance(raw_hist, str):
            raw_hist = json.loads(raw_hist)
        data["history"] = raw_hist
    else:
        data["attachments"] = [
            {
                "id": a["id"],
                "report_id": a["report_id"],
                "uploader_id": a["uploader_id"],
                "file_name": a["file_name"],
                "mime_type": a["mime_type"],
                "size_bytes": int(a["size_bytes"]),
                "created_at": _iso_or_none(a["created_at"]),
                "attachment_id": a["id"],
                "url": f"{prefix}/{a['id']}",
            }
            for a in att_rows
        ]
        data["history"] = [
            {
                "id": int(h["id"]),
                "report_id": h["report_id"],
                "from_status": h["from_status"],
                "to_status": h["to_status"],
                "actor_id": h["actor_id"],
                "actor_role": h["actor_role"],
                "note": h["note"],
                "created_at": _iso_or_none(h["created_at"]),
            }
            for h in hist_rows
        ]
    return data


def cancel_my_report(report_id: str, reporter_id: str, reporter_role: str) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    with get_session() as session:
        record = session.get(ReportRecord, str(report_id))
        if not record or record.reporter_id != str(reporter_id):
            raise ReportDomainError(404, "REPORT_NOT_FOUND", "Không tìm thấy báo cáo của bạn.")
        if record.status != "pending":
            raise ReportDomainError(
                422,
                "INVALID_TRANSITION",
                f"Chỉ có thể hủy báo cáo khi đang ở trạng thái 'pending' (hiện tại: '{record.status}').",
            )
        old_status = record.status
        record.status = "cancelled"
        record.version += 1
        record.updated_at = now
        session.add(
            ReportStatusHistoryRecord(
                report_id=record.id,
                from_status=old_status,
                to_status="cancelled",
                actor_id=str(reporter_id),
                actor_role=reporter_role,
                note="Người tạo tự hủy báo cáo",
                created_at=now,
            )
        )
        session.commit()
        return record.to_dict()


def _parse_date_bound(raw: str, *, end_of_day: bool = False) -> Tuple[datetime, bool]:
    val = raw.strip()
    if len(val) == 10 and val[4] == "-" and val[7] == "-":
        try:
            d = date.fromisoformat(val)
        except ValueError as exc:
            raise ReportDomainError(422, "INVALID_DATE_RANGE", "Định dạng ngày lọc không hợp lệ (YYYY-MM-DD).") from exc
        start_ict = datetime(d.year, d.month, d.day, tzinfo=_ICT_TZ)
        bound = (start_ict + timedelta(days=1)) if end_of_day else start_ict
        return bound.astimezone(timezone.utc), True

    try:
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReportDomainError(422, "INVALID_DATE_RANGE", "Định dạng ngày lọc không hợp lệ.") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc), False


def list_admin_reports(
    status: Optional[str] = None,
    kind: Optional[str] = None,
    category_code: Optional[str] = None,
    priority: Optional[str] = None,
    reporter_role: Optional[str] = None,
    assignee_id: Optional[str] = None,
    q_text: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sort: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    T = ReportRecord.__table__
    conditions: List[Any] = []
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if statuses:
            conditions.append(T.c.status.in_(statuses))
    if kind in ("ticket", "quick_error"):
        conditions.append(T.c.kind == kind)
    if category_code:
        conditions.append(T.c.category_code == category_code)
    if priority:
        priorities = [p.strip() for p in priority.split(",") if p.strip()]
        if priorities:
            conditions.append(T.c.priority.in_(priorities))
    if reporter_role in ("citizen", "lawyer"):
        conditions.append(T.c.reporter_role == reporter_role)
    if assignee_id:
        conditions.append(T.c.assignee_id == assignee_id)
    if q_text and q_text.strip():
        kw = f"%{q_text.strip()}%"
        conditions.append(
            or_(
                T.c.code.ilike(kw),
                T.c.title.ilike(kw),
                T.c.description.ilike(kw),
                T.c.reporter_id.ilike(kw),
            )
        )

    from_dt = None
    to_dt = None
    to_exclusive = False
    if date_from and date_from.strip():
        from_dt, _ = _parse_date_bound(date_from, end_of_day=False)
    if date_to and date_to.strip():
        to_dt, to_exclusive = _parse_date_bound(date_to, end_of_day=True)
    if from_dt is not None and to_dt is not None:
        if (to_exclusive and from_dt >= to_dt) or (not to_exclusive and from_dt > to_dt):
            raise ReportDomainError(422, "INVALID_DATE_RANGE", "Ngày bắt đầu không được lớn hơn ngày kết thúc.")
    if from_dt is not None:
        conditions.append(T.c.created_at >= from_dt)
    if to_dt is not None:
        conditions.append(T.c.created_at < to_dt if to_exclusive else T.c.created_at <= to_dt)

    if sort == "priority":
        priority_rank = case(
            (T.c.priority == "critical", 0),
            (T.c.priority == "high", 1),
            (T.c.priority == "normal", 2),
            else_=3,
        )
        counts_stmt = select(func.count(T.c.id).label("total_count")).select_from(T)
        page_stmt = select(*_report_list_columns(T, include_context=False), priority_rank.label("priority_rank")).select_from(T)
        for cond in conditions:
            counts_stmt = counts_stmt.where(cond)
            page_stmt = page_stmt.where(cond)
        counts_cte = counts_stmt.cte("counts")
        page_cte = page_stmt.order_by(priority_rank, desc(T.c.created_at), desc(T.c.id)).limit(limit).offset(offset).cte("page")
        stmt = (
            select(counts_cte.c.total_count, page_cte)
            .select_from(counts_cte.outerjoin(page_cte, true()))
            .order_by(page_cte.c.priority_rank, desc(page_cte.c.created_at), desc(page_cte.c.id))
        )
        rows = execute_readonly(lambda conn: conn.execute(stmt).mappings().all())
        total = int(rows[0]["total_count"] or 0) if rows else 0
        items = [_report_row_to_dict(r, include_context=False) for r in rows if r["id"] is not None]
        page = (offset // limit) + 1
        total_pages = max(1, math.ceil(total / limit))
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "pagination": {"page": page, "page_size": limit, "total": total, "total_pages": total_pages},
        }

    return _run_paged_report_query(conditions, (desc(T.c.created_at), desc(T.c.id)), limit, offset)


def get_admin_reports_summary() -> Dict[str, Any]:
    T = ReportRecord.__table__
    stmt = select(
        func.coalesce(func.sum(case((T.c.status == "pending", 1), else_=0)), 0).label("pending"),
        func.coalesce(func.sum(case((T.c.status == "in_review", 1), else_=0)), 0).label("in_review"),
        func.coalesce(func.sum(case((T.c.status == "accepted", 1), else_=0)), 0).label("accepted"),
        func.coalesce(func.sum(case((T.c.status == "rejected", 1), else_=0)), 0).label("rejected"),
        func.coalesce(func.sum(case((T.c.status == "resolved", 1), else_=0)), 0).label("resolved"),
        func.coalesce(func.sum(case((T.c.status == "cancelled", 1), else_=0)), 0).label("cancelled"),
        func.coalesce(
            func.sum(case((and_(T.c.status.in_(["pending", "in_review"]), T.c.priority == "critical"), 1), else_=0)),
            0,
        ).label("critical_open"),
        func.coalesce(
            func.sum(case((and_(T.c.status.in_(["pending", "in_review"]), T.c.priority == "high"), 1), else_=0)),
            0,
        ).label("high_open"),
    )
    row = execute_readonly(lambda conn: conn.execute(stmt).mappings().first())
    if not row:
        return {k: 0 for k in ("pending", "in_review", "accepted", "rejected", "resolved", "cancelled", "critical_open", "high_open")}
    return {
        "pending": int(row["pending"] or 0),
        "in_review": int(row["in_review"] or 0),
        "accepted": int(row["accepted"] or 0),
        "rejected": int(row["rejected"] or 0),
        "resolved": int(row["resolved"] or 0),
        "cancelled": int(row["cancelled"] or 0),
        "critical_open": int(row["critical_open"] or 0),
        "high_open": int(row["high_open"] or 0),
    }


def _load_and_lock_for_admin(
    session,
    report_id: str,
    admin_id: str,
    target_status: str,
    expected_version: int,
) -> Tuple[ReportRecord, str]:
    record = session.query(ReportRecord).filter(ReportRecord.id == str(report_id)).with_for_update().one_or_none()
    if not record:
        raise ReportDomainError(404, "REPORT_NOT_FOUND", "Không tìm thấy báo cáo.")
    if record.reporter_id == str(admin_id):
        raise ReportDomainError(403, "SELF_DECISION_FORBIDDEN", "Không thể tự duyệt báo cáo do chính mình tạo.")
    if record.version != expected_version:
        raise ReportDomainError(
            409,
            "REPORT_STATE_CONFLICT",
            f"Báo cáo đã được cập nhật bởi quản trị viên khác (version hiện tại: {record.version}). Vui lòng tải lại.",
        )
    allowed = ALLOWED_TRANSITIONS.get(record.status, set())
    if target_status not in allowed:
        raise ReportDomainError(
            422,
            "INVALID_TRANSITION",
            f"Không thể chuyển trạng thái từ '{record.status}' sang '{target_status}'.",
        )
    return record, record.status


def claim_report(report_id: str, admin_id: str, req: AdminClaimReleaseRequest) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    with get_session() as session:
        record, old_status = _load_and_lock_for_admin(
            session, report_id, admin_id, "in_review", req.version
        )
        record.status = "in_review"
        record.assignee_id = str(admin_id)
        record.version += 1
        record.updated_at = now
        session.add(
            ReportStatusHistoryRecord(
                report_id=record.id,
                from_status=old_status,
                to_status="in_review",
                actor_id=str(admin_id),
                actor_role="admin",
                note=req.note or "Nhận xử lý báo cáo",
                created_at=now,
            )
        )
        session.commit()
        return record.to_dict()


def release_report(report_id: str, admin_id: str, req: AdminClaimReleaseRequest) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    with get_session() as session:
        record, old_status = _load_and_lock_for_admin(
            session, report_id, admin_id, "pending", req.version
        )
        record.status = "pending"
        record.assignee_id = None
        record.version += 1
        record.updated_at = now
        session.add(
            ReportStatusHistoryRecord(
                report_id=record.id,
                from_status=old_status,
                to_status="pending",
                actor_id=str(admin_id),
                actor_role="admin",
                note=req.note or "Trả lại hàng đợi chờ xử lý",
                created_at=now,
            )
        )
        session.commit()
        return record.to_dict()


def _accept_report_sync(report_id: str, admin_id: str, req: AdminAcceptResolveRequest) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    with get_session() as session:
        record, old_status = _load_and_lock_for_admin(
            session, report_id, admin_id, "accepted", req.version
        )
        record.status = "accepted"
        record.assignee_id = record.assignee_id or str(admin_id)
        record.decided_by = str(admin_id)
        record.decided_at = now
        record.decision_note = (req.note or "").strip() or None
        record.version += 1
        record.updated_at = now
        session.add(
            ReportStatusHistoryRecord(
                report_id=record.id,
                from_status=old_status,
                to_status="accepted",
                actor_id=str(admin_id),
                actor_role="admin",
                note=record.decision_note or "Đã chấp nhận báo cáo",
                created_at=now,
            )
        )
        session.commit()
        return record.to_dict()


async def accept_report(
    report_id: str,
    admin_id: str,
    req: AdminAcceptResolveRequest,
) -> Dict[str, Any]:
    """Admin chấp nhận báo cáo -> chuyển trạng thái sang 'accepted' và gửi thông báo về cho người dùng/luật sư."""
    result = await asyncio.to_thread(_accept_report_sync, report_id, admin_id, req)
    await _emit_report_event(
        "report.accepted",
        {
            "report_id": result["id"],
            "code": result["code"],
            "reporter_id": result["reporter_id"],
            "decided_by": str(admin_id),
            "decision_note": result["decision_note"],
            "priority": result["priority"],
        },
    )
    return result


def _reject_report_sync(report_id: str, admin_id: str, req: AdminRejectRequest) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    with get_session() as session:
        record, old_status = _load_and_lock_for_admin(
            session, report_id, admin_id, "rejected", req.version
        )
        if req.duplicate_of_code:
            orig = (
                session.query(ReportRecord)
                .filter(ReportRecord.code == req.duplicate_of_code.strip())
                .first()
            )
            if orig:
                record.duplicate_of = orig.id

        record.status = "rejected"
        record.assignee_id = record.assignee_id or str(admin_id)
        record.reject_reason_code = req.reject_reason_code
        record.decision_note = req.note.strip()
        record.decided_by = str(admin_id)
        record.decided_at = now
        record.version += 1
        record.updated_at = now
        session.add(
            ReportStatusHistoryRecord(
                report_id=record.id,
                from_status=old_status,
                to_status="rejected",
                actor_id=str(admin_id),
                actor_role="admin",
                note=f"[{req.reject_reason_code}] {record.decision_note}",
                created_at=now,
            )
        )
        session.commit()
        return record.to_dict()


async def reject_report(
    report_id: str,
    admin_id: str,
    req: AdminRejectRequest,
) -> Dict[str, Any]:
    """Admin từ chối báo cáo (bắt buộc mã lý do + ghi chú) -> gửi thông báo về cho người dùng/luật sư."""
    if req.reject_reason_code not in REJECT_REASON_CATALOG:
        raise ReportDomainError(
            422,
            "INVALID_REJECT_REASON",
            f"Mã lý do từ chối '{req.reject_reason_code}' không hợp lệ.",
        )

    result = await asyncio.to_thread(_reject_report_sync, report_id, admin_id, req)
    await _emit_report_event(
        "report.rejected",
        {
            "report_id": result["id"],
            "code": result["code"],
            "reporter_id": result["reporter_id"],
            "decided_by": str(admin_id),
            "reject_reason_code": req.reject_reason_code,
            "decision_note": result["decision_note"],
            "priority": result["priority"],
        },
    )
    return result


def _resolve_report_sync(report_id: str, admin_id: str, req: AdminAcceptResolveRequest) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    with get_session() as session:
        record, old_status = _load_and_lock_for_admin(
            session, report_id, admin_id, "resolved", req.version
        )
        record.status = "resolved"
        if req.note and req.note.strip():
            record.decision_note = req.note.strip()
        record.resolved_at = now
        record.version += 1
        record.updated_at = now
        session.add(
            ReportStatusHistoryRecord(
                report_id=record.id,
                from_status=old_status,
                to_status="resolved",
                actor_id=str(admin_id),
                actor_role="admin",
                note=(req.note or "").strip() or "Đã khắc phục hoàn tất",
                created_at=now,
            )
        )
        session.commit()
        return record.to_dict()


async def resolve_report(
    report_id: str,
    admin_id: str,
    req: AdminAcceptResolveRequest,
) -> Dict[str, Any]:
    """Admin đánh dấu sự cố đã được khắc phục hoàn tất ('resolved') -> gửi thông báo về cho người báo cáo."""
    result = await asyncio.to_thread(_resolve_report_sync, report_id, admin_id, req)
    await _emit_report_event(
        "report.resolved",
        {
            "report_id": result["id"],
            "code": result["code"],
            "reporter_id": result["reporter_id"],
            "decided_by": str(admin_id),
            "decision_note": result["decision_note"],
            "priority": result["priority"],
        },
    )
    return result
