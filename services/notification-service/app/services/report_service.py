"""Business Logic xử lý Report (Ticket & Quick Error) cho Người dùng (citizen), Luật sư (lawyer) và Admin.

Tích hợp RabbitMQ theo kiến trúc Event-Driven:
- Lưu Report đồng bộ vào DB để đảm bảo tính toàn vẹn & trả về mã `RPT-YYYY-NNNNNN` ngay lập tức.
- Phát sự kiện RabbitMQ (`report.created`, `report.accepted`, `report.rejected`, `report.resolved`)
  để Notification Hub tạo thông báo cho Admin và phản hồi kết quả cho người báo cáo.
- Cơ chế Graceful Degradation: nếu RabbitMQ chưa bật hoặc mất kết nối (`publish_event` trả về `False`),
  service tự động gọi thẳng `handle_event` nội bộ để không bao giờ mất thông báo của người dùng/admin.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common_auth.events import publish_event
from sqlalchemy import desc, func

from app.core.notification_store import (
    ReportAttachmentRecord,
    ReportCategoryRecord,
    ReportReasonRecord,
    ReportRecord,
    ReportStatusHistoryRecord,
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


class ReportDomainError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _generate_report_code(session) -> str:
    year = datetime.now(timezone.utc).year
    prefix = f"RPT-{year}-"
    count = session.query(func.count(ReportRecord.id)).scalar() or 0
    seq = count + 1
    while True:
        code = f"{prefix}{seq:06d}"
        exists = session.query(ReportRecord.id).filter(ReportRecord.code == code).first()
        if not exists:
            return code
        seq += 1


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
    event_id = f"evt-rpt-{uuid.uuid4().hex}"
    published = await publish_event(routing_key, payload)
    if not published:
        await handle_event(
            routing_key,
            {
                "event_id": event_id,
                "event_type": routing_key,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": payload,
            },
        )


def get_report_catalog(user_role: str, kind: Optional[str] = None) -> Dict[str, Any]:
    """Lấy danh sách nhóm vấn đề (categories) và nguyên nhân (reasons) theo role của người gọi."""
    role_filter = "citizen" if user_role not in ("citizen", "lawyer") else user_role
    with get_session() as session:
        cat_rows = (
            session.query(ReportCategoryRecord)
            .filter(ReportCategoryRecord.is_active.is_(True))
            .order_by(ReportCategoryRecord.sort_order)
            .all()
        )
        rsn_query = session.query(ReportReasonRecord).filter(ReportReasonRecord.is_active.is_(True))
        if kind in ("ticket", "quick_error"):
            rsn_query = rsn_query.filter(ReportReasonRecord.kind == kind)
        rsn_rows = rsn_query.order_by(ReportReasonRecord.sort_order).all()

        categories = []
        for c in cat_rows:
            c_dict = c.to_dict()
            if user_role == "admin" or role_filter in c_dict["allowed_roles"]:
                categories.append(c_dict)

        reasons = []
        for r in rsn_rows:
            r_dict = r.to_dict()
            if user_role == "admin" or role_filter in r_dict["allowed_roles"]:
                reasons.append(r_dict)

        return {
            "categories": categories,
            "ticket_reasons": [r for r in reasons if r["kind"] == "ticket"],
            "quick_error_reasons": [r for r in reasons if r["kind"] == "quick_error"],
            "reject_reasons": [
                {"code": k, "name_vi": v} for k, v in REJECT_REASON_CATALOG.items()
            ],
        }


def save_attachment(
    uploader_id: str,
    file_name: str,
    mime_type: str,
    content: bytes,
) -> Dict[str, Any]:
    if mime_type not in ALLOWED_IMAGE_MIMES:
        raise ReportDomainError(
            422,
            "UNSUPPORTED_MEDIA_TYPE",
            "Chỉ chấp nhận ảnh định dạng PNG, JPEG hoặc WEBP.",
        )
    if len(content) > MAX_ATTACHMENT_BYTES or len(content) == 0:
        raise ReportDomainError(
            422,
            "INVALID_FILE_SIZE",
            "Kích thước ảnh phải lớn hơn 0 và không vượt quá 5MB.",
        )

    att_id = f"att-{uuid.uuid4().hex}"
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[mime_type]
    upload_dir = Path("data/report_attachments")
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
            mime_type=mime_type,
            size_bytes=len(content),
            created_at=now,
        )
        session.add(rec)
        session.commit()
        return rec.to_dict()


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

    now = datetime.now(timezone.utc)
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
        session.commit()
        result = record.to_dict()

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

    now = datetime.now(timezone.utc)
    ctx = req.context or {}
    feature_code = req.feature_code or ctx.get("feature_code") or "OTHER"
    target_type = req.target_type or ctx.get("target_type")
    target_id = req.target_id or ctx.get("target_id")

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
                return res, True

        priority = reason.default_priority or "normal"
        http_status = ctx.get("http_status")
        if isinstance(http_status, int) and http_status >= 500 and priority in ("low", "normal"):
            priority = "high"

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
        session.commit()
        result = record.to_dict()
        result["duplicated"] = False

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
            "feature_code": str(feature_code),
        },
    )
    return result, False


def list_my_reports(
    reporter_id: str,
    status: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    with get_session() as session:
        q = session.query(ReportRecord).filter(ReportRecord.reporter_id == str(reporter_id))
        if status:
            statuses = [s.strip() for s in status.split(",") if s.strip()]
            if statuses:
                q = q.filter(ReportRecord.status.in_(statuses))
        if kind in ("ticket", "quick_error"):
            q = q.filter(ReportRecord.kind == kind)
        total = q.count()
        rows = q.order_by(desc(ReportRecord.created_at)).offset(offset).limit(limit).all()
        return {
            "items": [r.to_dict(include_context=False) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }


def get_report_detail(report_id: str, viewer_id: str, viewer_role: str) -> Dict[str, Any]:
    with get_session() as session:
        record = session.get(ReportRecord, str(report_id))
        if not record:
            raise ReportDomainError(404, "REPORT_NOT_FOUND", "Không tìm thấy báo cáo.")
        if viewer_role != "admin" and record.reporter_id != str(viewer_id):
            raise ReportDomainError(403, "FORBIDDEN", "Bạn không có quyền xem báo cáo này.")

        data = record.to_dict(include_context=True)
        attachments = (
            session.query(ReportAttachmentRecord)
            .filter(ReportAttachmentRecord.report_id == record.id)
            .all()
        )
        history = (
            session.query(ReportStatusHistoryRecord)
            .filter(ReportStatusHistoryRecord.report_id == record.id)
            .order_by(ReportStatusHistoryRecord.created_at)
            .all()
        )
        data["attachments"] = [a.to_dict() for a in attachments]
        data["history"] = [h.to_dict() for h in history]
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


def list_admin_reports(
    status: Optional[str] = None,
    kind: Optional[str] = None,
    category_code: Optional[str] = None,
    priority: Optional[str] = None,
    reporter_role: Optional[str] = None,
    assignee_id: Optional[str] = None,
    q_text: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    with get_session() as session:
        q = session.query(ReportRecord)
        if status:
            statuses = [s.strip() for s in status.split(",") if s.strip()]
            if statuses:
                q = q.filter(ReportRecord.status.in_(statuses))
        if kind in ("ticket", "quick_error"):
            q = q.filter(ReportRecord.kind == kind)
        if category_code:
            q = q.filter(ReportRecord.category_code == category_code)
        if priority:
            priorities = [p.strip() for p in priority.split(",") if p.strip()]
            if priorities:
                q = q.filter(ReportRecord.priority.in_(priorities))
        if reporter_role in ("citizen", "lawyer"):
            q = q.filter(ReportRecord.reporter_role == reporter_role)
        if assignee_id:
            q = q.filter(ReportRecord.assignee_id == assignee_id)
        if q_text and q_text.strip():
            kw = f"%{q_text.strip()}%"
            q = q.filter((ReportRecord.code.ilike(kw)) | (ReportRecord.title.ilike(kw)))

        total = q.count()
        rows = q.order_by(desc(ReportRecord.created_at)).offset(offset).limit(limit).all()
        return {
            "items": [r.to_dict(include_context=False) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }


def get_admin_reports_summary() -> Dict[str, Any]:
    with get_session() as session:
        rows = (
            session.query(ReportRecord.status, func.count(ReportRecord.id))
            .group_by(ReportRecord.status)
            .all()
        )
        by_status = {st: cnt for st, cnt in rows}
        critical_open = (
            session.query(func.count(ReportRecord.id))
            .filter(
                ReportRecord.status.in_(["pending", "in_review"]),
                ReportRecord.priority == "critical",
            )
            .scalar()
            or 0
        )
        high_open = (
            session.query(func.count(ReportRecord.id))
            .filter(
                ReportRecord.status.in_(["pending", "in_review"]),
                ReportRecord.priority == "high",
            )
            .scalar()
            or 0
        )
        return {
            "pending": by_status.get("pending", 0),
            "in_review": by_status.get("in_review", 0),
            "accepted": by_status.get("accepted", 0),
            "rejected": by_status.get("rejected", 0),
            "resolved": by_status.get("resolved", 0),
            "cancelled": by_status.get("cancelled", 0),
            "critical_open": critical_open,
            "high_open": high_open,
        }


def _load_and_lock_for_admin(
    session,
    report_id: str,
    admin_id: str,
    target_status: str,
    expected_version: int,
) -> Tuple[ReportRecord, str]:
    record = session.get(ReportRecord, str(report_id))
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


async def accept_report(
    report_id: str,
    admin_id: str,
    req: AdminAcceptResolveRequest,
) -> Dict[str, Any]:
    """Admin chấp nhận báo cáo -> chuyển trạng thái sang 'accepted' và gửi thông báo về cho người dùng/luật sư."""
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
        result = record.to_dict()

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
        result = record.to_dict()

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


async def resolve_report(
    report_id: str,
    admin_id: str,
    req: AdminAcceptResolveRequest,
) -> Dict[str, Any]:
    """Admin đánh dấu sự cố đã được khắc phục hoàn tất ('resolved') -> gửi thông báo về cho người báo cáo."""
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
        result = record.to_dict()

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
