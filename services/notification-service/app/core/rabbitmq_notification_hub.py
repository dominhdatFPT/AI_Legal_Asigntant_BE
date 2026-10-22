"""Notification Hub: biến domain event trên RabbitMQ thành thông báo cho người dùng (Feature 03).

Nghe `legal.events.topic` qua queue bền vững `q.notification.events` (có hàng đợi chết), lưu vào
`notification_store`. API GET /api/v1/notifications đọc lại cho icon chuông.
Event không có trong `format_notification` (ví dụ user.logged_out) thì bỏ qua.
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

logger = logging.getLogger("NotificationHub")

QUEUE_NOTIFICATION_INBOX = "q.notification.events"

# "#" khớp nhiều từ: "document.*" KHÔNG khớp "document.ocr.completed" (3 từ).
SUBSCRIPTION_PATTERNS = ["document.#", "contract.#", "procedure.#", "lawyer.#", "user.blocked"]


def sanitize_action_url(raw_url: Optional[str], default_path: str = "/") -> str:
    """Validate & Sanitize URL nhằm ngăn chặn Open Redirect và Phishing Attack (Vuln 2).
    
    Chỉ cho phép đường dẫn tương đối an toàn (bắt đầu bằng '/', không chứa host/scheme/protocol-relative).
    """
    if not raw_url or not isinstance(raw_url, str):
        return default_path
    clean_url = raw_url.strip()
    # Chặn URL dạng protocol-relative (//evil.com) hoặc javascript: / data:
    if clean_url.startswith("//") or clean_url.startswith("\\"):
        return default_path
    parsed = urlparse(clean_url)
    # Không cho phép có netloc (domain) hoặc scheme (http, https, javascript, v.v.)
    if clean_url.startswith("/") and not parsed.netloc and not parsed.scheme:
        return clean_url
    return default_path


def format_notification(event_type: str, payload: Dict[str, Any], event_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Chuyển event kỹ thuật thành thông báo; None nếu không cần báo."""
    user_id = payload.get("user_id")
    if not user_id:
        return None

    if event_type == "document.ocr.completed":
        title = "Tài liệu đã xử lý xong"
        message = f"Tài liệu #{str(payload.get('document_id'))[:8]} đã được quét an toàn và trích xuất dữ liệu."
        icon = "file-text"
        action_url = sanitize_action_url(f"/documents/{payload.get('document_id')}", "/documents")

    elif event_type == "document.processing.failed":
        title = "Không xử lý được tài liệu"
        message = f"Tài liệu #{str(payload.get('document_id'))[:8]} không xử lý được (mã lỗi {payload.get('error_code')})."
        icon = "file-x"
        action_url = sanitize_action_url(f"/documents/{payload.get('document_id')}", "/documents")

    elif event_type == "contract.generated":
        title = "Hợp đồng đã xuất file xong"
        message = f"Hợp đồng đã được kết xuất định dạng {str(payload.get('format')).upper()}."
        icon = "file-check"
        action_url = sanitize_action_url(f"/contracts/drafts/{payload.get('draft_id')}", "/contracts")

    elif event_type == "contract.generation.failed":
        title = "Xuất hợp đồng thất bại"
        message = f"Không xuất được file hợp đồng (mã lỗi {payload.get('error_code')}). Vui lòng thử lại."
        icon = "alert-circle"
        action_url = sanitize_action_url(f"/contracts/drafts/{payload.get('draft_id')}", "/contracts")

    elif event_type == "procedure.application.submitted":
        title = "Nộp hồ sơ thủ tục thành công"
        message = f"Hồ sơ thủ tục [{payload.get('domain')}] #{str(payload.get('record_id'))[:8]} đã được tiếp nhận."
        icon = "clipboard-check"
        action_url = sanitize_action_url(f"/procedures/records/{payload.get('record_id')}", "/procedures")

    elif event_type == "lawyer.approved":
        title = "Chúc mừng! Hồ sơ Luật sư đã duyệt"
        message = "Hồ sơ luật sư của bạn đã được duyệt."
        icon = "award"
        action_url = "/lawyer/dashboard"

    elif event_type == "user.blocked":
        title = "Tài khoản bị tạm khóa"
        message = f"Tài khoản của bạn đã bị khóa: {payload.get('reason') or 'Vi phạm điều khoản dịch vụ'}."
        icon = "alert-triangle"
        action_url = "/support"

    else:
        return None

    return {
        # id suy ra từ event_id: broker giao lại cùng event thì không sinh thông báo trùng.
        "id": f"notif-{event_id}" if event_id else f"notif-{uuid.uuid4().hex}",
        "user_id": str(user_id),
        "event_type": event_type,
        "title": title,
        "message": message,
        "icon": icon,
        "action_url": action_url,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "is_read": False,
    }


def save_notification(notif: Dict[str, Any]) -> None:
    notification_store.save_notification(notif)


def get_user_notifications(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    return notification_store.get_user_notifications(str(user_id), limit=limit)


async def handle_event(routing_key: str, event: Dict[str, Any]) -> None:
    """Lỗi (ví dụ DB sập) được ném ra để message chuyển sang q.dead_letter thay vì mất."""
    event_type = event.get("event_type") or routing_key
    notif = format_notification(event_type, event.get("payload") or {}, event.get("event_id"))
    if notif:
        await asyncio.to_thread(save_notification, notif)
        logger.info("[EVENT-HUB] Đã tạo thông báo '%s' cho user=%s từ event=%s", notif["title"], notif["user_id"], event_type)


async def start_notification_hub() -> None:
    await consume_forever(QUEUE_NOTIFICATION_INBOX, SUBSCRIPTION_PATTERNS, handle_event, prefetch=20)
