"""Business Logic xử lý thông báo toàn hệ thống (Admin Broadcast).

Đặc điểm:
- Cho phép Admin gửi thông báo tới:
  + `audience = 'all'`: Toàn bộ người dùng (citizen), luật sư (lawyer) và cả chính admin đó.
  + `audience = 'citizen'` | `'lawyer'` | `'admin'`: Từng nhóm vai trò cụ thể.
- Sử dụng kiến trúc **Fan-out on Read** kết hợp **RabbitMQ**:
  + In-app notification chỉ cần tạo 1 bản ghi trong `group_notifications` (O(1) write),
    tránh nghẽn DB khi có hàng trăm nghìn người dùng.
  + Phát sự kiện `notification.broadcast.requested` lên RabbitMQ để đồng bộ realtime
    và xử lý gửi email/push ngầm.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from common_auth.events import publish_event
from sqlalchemy import desc, func

from app.core.notification_store import (
    BroadcastRecord,
    GroupNotificationReadRecord,
    GroupNotificationRecord,
    get_session,
    save_group_notification,
)
from app.core.rabbitmq_notification_hub import sanitize_action_url
from app.core.realtime_broker import realtime_broker
from app.schemas.reports import BroadcastCreateRequest
from app.services.report_service import ReportDomainError

logger = logging.getLogger("BroadcastService")


async def create_broadcast(admin_id: str, req: BroadcastCreateRequest) -> Dict[str, Any]:
    """Tạo và phát thông báo toàn hệ thống (hoặc theo nhóm role)."""
    now = datetime.now(timezone.utc)
    broadcast_id = str(uuid.uuid4())
    action_url = sanitize_action_url(req.action_url, "/")

    is_scheduled = False
    if req.scheduled_at:
        sched_dt = (
            req.scheduled_at
            if req.scheduled_at.tzinfo
            else req.scheduled_at.replace(tzinfo=timezone.utc)
        )
        if sched_dt > now:
            is_scheduled = True
    else:
        sched_dt = None

    expires_dt = None
    if req.expires_at:
        expires_dt = (
            req.expires_at
            if req.expires_at.tzinfo
            else req.expires_at.replace(tzinfo=timezone.utc)
        )

    grp_notif_id = f"grp-bc-{broadcast_id}"

    if not is_scheduled:
        # Lưu ngay vào group_notifications để mọi user (và cả chính admin đó nếu audience in ('all', 'admin'))
        # đều thấy ngay lập tức trên chuông thông báo
        grp_notif = {
            "id": grp_notif_id,
            "audience": req.audience,
            "event_type": "broadcast.published",
            "category": "broadcast",
            "priority": req.priority,
            "title": req.title.strip(),
            "message": req.content.strip(),
            "icon": "megaphone",
            "action_url": action_url,
            "source_type": "broadcast",
            "source_id": broadcast_id,
            "created_by": str(admin_id),
            "created_at": now.isoformat(),
            "expires_at": expires_dt.isoformat() if expires_dt else None,
        }
        save_group_notification(grp_notif)
        realtime_broker.push_to_audience(req.audience, grp_notif)

    with get_session() as session:
        record = BroadcastRecord(
            id=broadcast_id,
            title=req.title.strip(),
            content=req.content.strip(),
            audience=req.audience,
            priority=req.priority,
            channels=",".join(req.channels or ["in_app"]),
            action_url=action_url,
            status="scheduled" if is_scheduled else "sent",
            scheduled_at=sched_dt,
            sent_at=None if is_scheduled else now,
            expires_at=expires_dt,
            group_notification_id=None if is_scheduled else grp_notif_id,
            stats_json=json.dumps({"in_app": not is_scheduled}, ensure_ascii=False),
            created_by=str(admin_id),
            created_at=now,
        )
        session.add(record)
        session.commit()
        result = record.to_dict()

    if not is_scheduled:
        # Phát sự kiện lên RabbitMQ để các worker email/push hoặc các pod API khác đồng bộ
        await publish_event(
            "notification.broadcast.requested",
            {
                "broadcast_id": broadcast_id,
                "group_notification_id": grp_notif_id,
                "title": result["title"],
                "content": result["content"],
                "audience": result["audience"],
                "priority": result["priority"],
                "channels": result["channels"],
                "action_url": action_url,
                "created_by": str(admin_id),
                "expires_at": result["expires_at"],
            },
        )

    return result


def list_broadcasts(status: Optional[str] = None, limit: int = 20, offset: int = 0) -> Dict[str, Any]:
    with get_session() as session:
        q = session.query(BroadcastRecord)
        if status:
            q = q.filter(BroadcastRecord.status == status)
        total = q.count()
        rows = q.order_by(desc(BroadcastRecord.created_at)).offset(offset).limit(limit).all()

        items = []
        for r in rows:
            item = r.to_dict()
            if r.group_notification_id:
                read_count = (
                    session.query(func.count(GroupNotificationReadRecord.user_id))
                    .filter(
                        GroupNotificationReadRecord.group_notification_id == r.group_notification_id,
                        GroupNotificationReadRecord.is_read.is_(True),
                    )
                    .scalar()
                    or 0
                )
                item["read_count"] = read_count
            else:
                item["read_count"] = 0
            items.append(item)

        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        }


def get_broadcast_detail(broadcast_id: str) -> Dict[str, Any]:
    with get_session() as session:
        record = session.get(BroadcastRecord, str(broadcast_id))
        if not record:
            raise ReportDomainError(404, "BROADCAST_NOT_FOUND", "Không tìm thấy thông báo hệ thống.")
        item = record.to_dict()
        if record.group_notification_id:
            read_count = (
                session.query(func.count(GroupNotificationReadRecord.user_id))
                .filter(
                    GroupNotificationReadRecord.group_notification_id == record.group_notification_id,
                    GroupNotificationReadRecord.is_read.is_(True),
                )
                .scalar()
                or 0
            )
            item["read_count"] = read_count
        else:
            item["read_count"] = 0
        return item


def cancel_broadcast(broadcast_id: str) -> Dict[str, Any]:
    with get_session() as session:
        record = session.get(BroadcastRecord, str(broadcast_id))
        if not record:
            raise ReportDomainError(404, "BROADCAST_NOT_FOUND", "Không tìm thấy thông báo hệ thống.")
        if record.status != "scheduled":
            raise ReportDomainError(
                422,
                "INVALID_BROADCAST_STATUS",
                "Chỉ có thể hủy thông báo đang ở trạng thái hẹn giờ ('scheduled').",
            )
        record.status = "cancelled"
        if record.group_notification_id:
            grp = session.get(GroupNotificationRecord, record.group_notification_id)
            if grp:
                grp.is_deleted = True
        session.commit()
        return record.to_dict()
