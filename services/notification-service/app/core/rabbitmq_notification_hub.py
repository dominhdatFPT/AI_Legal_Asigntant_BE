"""Notification Hub: biến domain event trên RabbitMQ thành thông báo cho người dùng, luật sư và admin (Feature 03 & Feature 10).

Nghe `legal.events.topic` qua queue bền vững `q.notification.events` (có hàng đợi chết), lưu vào
`notification_store`. API GET /api/v1/notifications đọc lại cho icon chuông.
Hỗ trợ cả:
- Thông báo cá nhân (document.*, contract.*, procedure.*, lawyer.*, user.blocked, report.accepted, report.rejected, report.resolved)
- Thông báo kép khi có report mới (`report.created`):
    + Thông báo nhóm gửi cho toàn bộ Admin (`audience='admin'`)
    + Thông báo xác nhận gửi cho người tạo report (`reporter_id`)
- Thông báo toàn hệ thống (`notification.broadcast.requested` / `broadcast.published`)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from common_auth.events import consume_forever

from app.core import notification_store
from app.core.realtime_broker import realtime_broker
from app.models.reports import REJECT_REASON_CATALOG

logger = logging.getLogger("NotificationHub")

QUEUE_NOTIFICATION_INBOX = "q.notification.events"

# "#" khớp nhiều từ: bao gồm cả report.# và notification.broadcast.#
SUBSCRIPTION_PATTERNS = [
    "document.#",
    "contract.#",
    "procedure.#",
    "lawyer.#",
    "user.blocked",
    "report.#",
    "notification.broadcast.#",
]


def sanitize_action_url(raw_url: Optional[str], default_path: str = "/") -> str:
    """Validate & Sanitize URL nhằm ngăn chặn Open Redirect và Phishing Attack (Vuln 2).
    
    Chỉ cho phép đường dẫn tương đối an toàn (bắt đầu bằng '/', không chứa host/scheme/protocol-relative).
    """
    if not raw_url or not isinstance(raw_url, str):
        return default_path
    clean_url = raw_url.strip()
    if clean_url.startswith("//") or clean_url.startswith("\\"):
        return default_path
    parsed = urlparse(clean_url)
    if clean_url.startswith("/") and not parsed.netloc and not parsed.scheme:
        return clean_url
    return default_path


def format_notification(event_type: str, payload: Dict[str, Any], event_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Chuyển event kỹ thuật thành thông báo cá nhân; None nếu không cần báo."""
    # Hỗ trợ cả user_id và reporter_id (cho các event report.*)
    user_id = payload.get("user_id") or payload.get("reporter_id")
    if not user_id:
        return None

    category = "system"
    priority = str(payload.get("priority") or "normal")
    source_type: Optional[str] = None
    source_id: Optional[str] = None
    id_suffix = ""

    if event_type == "document.ocr.completed":
        title = "Tài liệu đã xử lý xong"
        message = f"Tài liệu #{str(payload.get('document_id'))[:8]} đã được quét an toàn và trích xuất dữ liệu."
        icon = "file-text"
        category = "document"
        action_url = sanitize_action_url(f"/documents/{payload.get('document_id')}", "/documents")

    elif event_type == "document.processing.failed":
        title = "Không xử lý được tài liệu"
        message = f"Tài liệu #{str(payload.get('document_id'))[:8]} không xử lý được (mã lỗi {payload.get('error_code')})."
        icon = "file-x"
        category = "document"
        action_url = sanitize_action_url(f"/documents/{payload.get('document_id')}", "/documents")

    elif event_type == "contract.generated":
        title = "Hợp đồng đã xuất file xong"
        message = f"Hợp đồng đã được kết xuất định dạng {str(payload.get('format')).upper()}."
        icon = "file-check"
        category = "contract"
        action_url = sanitize_action_url(f"/contracts/drafts/{payload.get('draft_id')}", "/contracts")

    elif event_type == "contract.generation.failed":
        title = "Xuất hợp đồng thất bại"
        message = f"Không xuất được file hợp đồng (mã lỗi {payload.get('error_code')}). Vui lòng thử lại."
        icon = "alert-circle"
        category = "contract"
        action_url = sanitize_action_url(f"/contracts/drafts/{payload.get('draft_id')}", "/contracts")

    elif event_type == "procedure.application.submitted":
        title = "Nộp hồ sơ thủ tục thành công"
        message = f"Hồ sơ thủ tục [{payload.get('domain')}] #{str(payload.get('record_id'))[:8]} đã được tiếp nhận."
        icon = "clipboard-check"
        category = "procedure"
        action_url = sanitize_action_url(f"/procedures/records/{payload.get('record_id')}", "/procedures")

    elif event_type == "procedure.form_template.deleted":
        title = "Đã xóa mẫu đơn"
        message = f"Mẫu đơn #{str(payload.get('template_id'))[:8]} đã được xóa khỏi danh mục (RabbitMQ event processed)."
        icon = "trash"
        category = "procedure"
        user_id = payload.get("admin_id") or user_id
        action_url = "/landforms"

    elif event_type == "lawyer.approved":
        title = "Chúc mừng! Hồ sơ Luật sư đã duyệt"
        message = "Hồ sơ luật sư của bạn đã được duyệt."
        icon = "award"
        category = "lawyer"
        action_url = "/lawyer/dashboard"

    elif event_type == "user.blocked":
        title = "Tài khoản bị tạm khóa"
        message = f"Tài khoản của bạn đã bị khóa: {payload.get('reason') or 'Vi phạm điều khoản dịch vụ'}."
        icon = "alert-triangle"
        priority = "high"
        action_url = "/support"

    elif event_type == "report.created":
        code = payload.get("code") or str(payload.get("report_id", ""))[:8]
        title = f"Đã tiếp nhận báo cáo #{code}"
        message = f"Báo cáo \"{payload.get('title') or code}\" đã được gửi tới Quản trị viên và đang chờ xử lý."
        icon = "flag"
        category = "report"
        source_type = "report"
        source_id = str(payload.get("report_id") or "")
        id_suffix = "-reporter"
        action_url = sanitize_action_url(f"/support?report_id={payload.get('report_id')}", "/support")

    elif event_type == "report.accepted":
        code = payload.get("code") or str(payload.get("report_id", ""))[:8]
        note = payload.get("decision_note")
        title = f"Báo cáo #{code} đã được chấp nhận"
        message = f"Quản trị viên đã chấp nhận báo cáo của bạn và đang tiến hành xử lý." + (
            f" Ghi chú: {note}" if note else ""
        )
        icon = "check-circle"
        category = "report"
        source_type = "report"
        source_id = str(payload.get("report_id") or "")
        action_url = sanitize_action_url(f"/support?report_id={payload.get('report_id')}", "/support")

    elif event_type == "report.rejected":
        code = payload.get("code") or str(payload.get("report_id", ""))[:8]
        reason_code = str(payload.get("reject_reason_code") or "")
        reason_label = REJECT_REASON_CATALOG.get(reason_code, reason_code)
        note = payload.get("decision_note")
        title = f"Báo cáo #{code} bị từ chối"
        detail = f"{reason_label} - {note}" if (reason_label and note) else (note or reason_label or "Không hợp lệ")
        message = f"Báo cáo #{code} không được chấp nhận. Lý do: {detail}."
        icon = "x-circle"
        category = "report"
        source_type = "report"
        source_id = str(payload.get("report_id") or "")
        action_url = sanitize_action_url(f"/support?report_id={payload.get('report_id')}", "/support")

    elif event_type == "report.resolved":
        code = payload.get("code") or str(payload.get("report_id", ""))[:8]
        note = payload.get("decision_note")
        title = f"Sự cố #{code} đã được khắc phục"
        message = f"Vấn đề trong báo cáo #{code} đã được đội ngũ kỹ thuật khắc phục hoàn tất." + (
            f" Chi tiết: {note}" if note else ""
        )
        icon = "shield-check"
        category = "report"
        source_type = "report"
        source_id = str(payload.get("report_id") or "")
        action_url = sanitize_action_url(f"/support?report_id={payload.get('report_id')}", "/support")

    else:
        return None

    notif_id = f"notif-{event_id}{id_suffix}" if event_id else f"notif-{uuid.uuid4().hex}"
    return {
        "id": notif_id,
        "user_id": str(user_id),
        "event_type": event_type,
        "category": category,
        "priority": priority,
        "title": title,
        "message": message,
        "icon": icon,
        "action_url": action_url,
        "source_type": source_type,
        "source_id": source_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "is_read": False,
    }


def format_admin_report_notification(payload: Dict[str, Any], event_id: Optional[str] = None) -> Dict[str, Any]:
    """Tạo thông báo nhóm (audience='admin') khi có người dùng hoặc luật sư gửi report mới."""
    code = payload.get("code") or str(payload.get("report_id", ""))[:8]
    kind = payload.get("kind") or "ticket"
    kind_label = "Báo lỗi nhanh" if kind == "quick_error" else "Ticket hỗ trợ"
    role_label = "Luật sư" if payload.get("reporter_role") == "lawyer" else "Người dùng"
    priority = str(payload.get("priority") or "normal")
    report_title = payload.get("title") or code

    return {
        "id": f"grp-{event_id}-admin" if event_id else f"grp-rpt-{payload.get('report_id')}",
        "audience": "admin",
        "event_type": "report.created",
        "category": "report",
        "priority": priority,
        "title": f"[{kind_label}] Báo cáo mới #{code} từ {role_label}",
        "message": f"{report_title} (Mức ưu tiên: {priority.upper()}). Nhấn để kiểm tra và xử lý.",
        "icon": "alert-octagon" if priority in ("high", "critical") else "flag",
        "action_url": sanitize_action_url(f"/admin/reports/{payload.get('report_id')}", "/admin/reports"),
        "source_type": "report",
        "source_id": str(payload.get("report_id") or ""),
        "created_by": str(payload.get("reporter_id") or ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def save_notification(notif: Dict[str, Any]) -> None:
    notification_store.save_notification(notif)


def save_group_notification(notif: Dict[str, Any]) -> None:
    notification_store.save_group_notification(notif)


def get_user_notifications(
    user_id: str,
    limit: int = 50,
    user_role: Optional[str] = None,
    unread_only: bool = False,
) -> List[Dict[str, Any]]:
    return notification_store.get_user_notifications(
        str(user_id), limit=limit, user_role=user_role, unread_only=unread_only
    )


async def handle_event(routing_key: str, event: Dict[str, Any]) -> None:
    """Lỗi (ví dụ DB sập) được ném ra để message chuyển sang q.dead_letter thay vì mất."""
    event_type = event.get("event_type") or routing_key
    payload = event.get("payload") or {}
    event_id = event.get("event_id")

    # 1. Nếu là report.created -> lưu thêm thông báo nhóm cho seluruh Admin (audience='admin')
    if event_type == "report.created":
        admin_group_notif = format_admin_report_notification(payload, event_id=event_id)
        await asyncio.to_thread(save_group_notification, admin_group_notif)
        realtime_broker.push_to_audience("admin", admin_group_notif)

    # 2. Nếu là broadcast toàn hệ thống / theo nhóm
    if event_type in ("notification.broadcast.requested", "broadcast.published"):
        grp_id = payload.get("group_notification_id") or (f"grp-{event_id}" if event_id else f"grp-{uuid.uuid4().hex}")
        grp_notif = {
            "id": str(grp_id),
            "audience": str(payload.get("audience") or "all"),
            "event_type": "broadcast.published",
            "category": "broadcast",
            "priority": str(payload.get("priority") or "normal"),
            "title": str(payload.get("title") or "Thông báo hệ thống"),
            "message": str(payload.get("content") or payload.get("message") or ""),
            "icon": "megaphone",
            "action_url": sanitize_action_url(payload.get("action_url"), "/"),
            "source_type": "broadcast",
            "source_id": str(payload.get("broadcast_id") or ""),
            "created_by": str(payload.get("created_by") or ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": payload.get("expires_at"),
        }
        await asyncio.to_thread(save_group_notification, grp_notif)
        realtime_broker.push_to_audience(grp_notif["audience"], grp_notif)
        logger.info(
            "[EVENT-HUB] Đã phát thông báo nhóm '%s' tới audience=%s",
            grp_notif["title"],
            grp_notif["audience"],
        )
        return

    # 3. Thông báo cá nhân (bao gồm xác nhận report.created và kết quả report.accepted/rejected/resolved)
    notif = format_notification(event_type, payload, event_id)
    if notif:
        await asyncio.to_thread(save_notification, notif)
        realtime_broker.push_to_user(notif["user_id"], notif)
        logger.info(
            "[EVENT-HUB] Đã tạo thông báo '%s' cho user=%s từ event=%s",
            notif["title"],
            notif["user_id"],
            event_type,
        )


async def start_notification_hub() -> None:
    await consume_forever(QUEUE_NOTIFICATION_INBOX, SUBSCRIPTION_PATTERNS, handle_event, prefetch=20)
