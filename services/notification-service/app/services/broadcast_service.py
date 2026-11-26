"""Business Logic xử lý thông báo toàn hệ thống (Admin Broadcast).

Đặc điểm:
- Cho phép Admin gửi thông báo tới:
  + `audience = 'all'`: Toàn bộ người dùng (citizen), luật sư (lawyer) và cả chính admin đó.
  + `audience = 'citizen'` | `'lawyer'` | `'admin'`: Từng nhóm vai trò cụ thể.
  + Nhóm 1 hoặc nhiều người dùng cụ thể (`recipient_ids` / `recipient_id`).
- Sử dụng kiến trúc **Fan-out on Read** kết hợp **RabbitMQ**:
  + In-app notification cho nhóm chung chỉ cần tạo 1 bản ghi trong `group_notifications` (O(1) write),
    tránh nghẽn DB khi có hàng trăm nghìn người dùng.
  + Với người nhận cụ thể: tạo bản ghi `notifications` riêng biệt cho từng người và đẩy qua WebSocket/SSE realtime.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import case, desc, func, select, true

from app.core.notification_store import (
    BroadcastRecord,
    GroupNotificationReadRecord,
    GroupNotificationRecord,
    NotificationOutboxRecord,
    NotificationRecord,
    _parse_dt,
    execute_readonly,
    get_session,
)
from app.core.rabbitmq_notification_hub import sanitize_action_url
from app.core.realtime_broker import realtime_broker
from app.services.report_service import ReportDomainError

logger = logging.getLogger("BroadcastService")


def _persist_broadcast_sync(
    admin_id: Any,
    req: Any,
    recipients: List[str],
    recipient_labels: Optional[List[str]] = None,
) -> Tuple[Dict[str, Any], List[Tuple[str, Dict[str, Any]]], Optional[Dict[str, Any]]]:
    now = datetime.now(timezone.utc)
    broadcast_id = str(uuid.uuid4())
    action_url = sanitize_action_url(req.action_url, "")

    common = {
        "event_type": "broadcast.published",
        "category": "broadcast",
        "priority": req.priority,
        "title": req.title,
        "message": req.content,
        "icon": "megaphone",
        "action_url": action_url,
        "source_type": "broadcast",
        "source_id": broadcast_id,
        "created_at": now,
    }

    with get_session() as session:
        if recipients:
            notifications = []
            for idx, r_id in enumerate(recipients):
                notif = NotificationRecord(
                    id=f"notif-bc-{broadcast_id}-{idx}" if len(recipients) > 1 else f"notif-bc-{broadcast_id}",
                    user_id=r_id,
                    is_read=False,
                    **common,
                )
                session.add(notif)
                notifications.append(notif)

            personal_id = notifications[0].id if len(notifications) == 1 else None
            group_id = None
            routing_key = "notification.realtime.user"
            primary_recipient = recipients[0] if len(recipients) == 1 else f"{len(recipients)} người nhận"
        else:
            notification = GroupNotificationRecord(
                id=f"grp-bc-{broadcast_id}",
                audience=req.audience,
                created_by=str(admin_id),
                is_deleted=False,
                **common,
            )
            session.add(notification)
            notifications = [notification]
            personal_id, group_id = None, notification.id
            routing_key = "notification.realtime.group"
            primary_recipient = None

        session.flush()

        stats: Dict[str, Any] = {"in_app": True}
        if recipients:
            stats["recipient_count"] = len(recipients)
            stats["recipient_ids"] = recipients
            if recipient_labels:
                stats["recipient_labels"] = [lbl for lbl in recipient_labels if lbl]

        record = BroadcastRecord(
            id=broadcast_id,
            title=req.title,
            content=req.content,
            audience=req.audience if not recipients else "all",
            recipient_id=primary_recipient,
            personal_notification_id=personal_id,
            group_notification_id=group_id,
            priority=req.priority,
            channels="in_app",
            action_url=action_url,
            status="sent",
            sent_at=now,
            stats_json=json.dumps(stats, ensure_ascii=False),
            created_by=str(admin_id),
            created_at=now,
        )
        session.add(record)

        user_push_items: List[Tuple[str, Dict[str, Any]]] = []
        group_push_item: Optional[Dict[str, Any]] = None
        if recipients:
            for notif in notifications:
                item = notif.to_dict()
                user_push_items.append((notif.user_id, item))
                payload = {"notification": item, "user_id": notif.user_id}
                session.add(
                    NotificationOutboxRecord(
                        id=f"rt-{notif.id}",
                        routing_key=routing_key,
                        payload_json=json.dumps(payload, ensure_ascii=False),
                        created_at=now,
                        available_at=now,
                    )
                )
        else:
            item = notifications[0].to_dict()
            group_push_item = item
            payload = {"notification": item, "audience": req.audience}
            session.add(
                NotificationOutboxRecord(
                    id=f"rt-{notifications[0].id}",
                    routing_key=routing_key,
                    payload_json=json.dumps(payload, ensure_ascii=False),
                    created_at=now,
                    available_at=now,
                )
            )

        session.commit()
        return record.to_dict(), user_push_items, group_push_item


async def create_broadcast(
    admin_id,
    req,
    recipient_labels: Optional[List[str]] = None,
):
    recipients: List[str] = []
    if getattr(req, "recipient_ids", None):
        recipients = list(dict.fromkeys(str(r).strip() for r in req.recipient_ids if r and str(r).strip()))
    elif getattr(req, "recipient_id", None) and str(req.recipient_id).strip():
        recipients = [str(req.recipient_id).strip()]

    result, user_push_items, group_push_item = await asyncio.to_thread(
        _persist_broadcast_sync,
        admin_id,
        req,
        recipients,
        recipient_labels,
    )

    if user_push_items:
        realtime_broker.push_to_users(user_push_items)
    elif group_push_item is not None:
        realtime_broker.push_to_audience(req.audience, group_push_item)

    return result


def _broadcast_row_to_dict(row: Any) -> Dict[str, Any]:
    try:
        raw_stats = json.loads(row["stats_json"]) if row["stats_json"] else {}
    except Exception:
        raw_stats = {}
    stats = {k: v for k, v in raw_stats.items() if v is not None} if isinstance(raw_stats, dict) else {}
    return {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"],
        "audience": row["audience"],
        "priority": row["priority"],
        "channels": [c.strip() for c in (row["channels"] or "").split(",") if c.strip()],
        "action_url": row["action_url"] or "",
        "status": row["status"],
        "sent_at": _parse_dt(row["sent_at"]).astimezone(timezone.utc).isoformat() if row["sent_at"] else None,
        "recipient_id": row["recipient_id"],
        "personal_notification_id": row["personal_notification_id"],
        "group_notification_id": row["group_notification_id"],
        "stats": stats,
        "created_by": row["created_by"],
        "created_at": _parse_dt(row["created_at"]).astimezone(timezone.utc).isoformat() if row["created_at"] else None,
        "read_count": int(row["read_count"] or 0),
    }


def list_broadcasts(status: Optional[str] = None, limit: int = 20, offset: int = 0) -> Dict[str, Any]:
    B = BroadcastRecord.__table__
    GR = GroupNotificationReadRecord.__table__
    N = NotificationRecord.__table__

    total_expr = (
        func.coalesce(func.sum(case((B.c.status == status, 1), else_=0)), 0)
        if status
        else func.count(B.c.id)
    ).label("total_count")
    sent_expr = func.coalesce(func.sum(case((B.c.status == "sent", 1), else_=0)), 0).label("sent_count")
    counts_cte = select(total_expr, sent_expr).cte("counts")

    page_stmt = select(
        B.c.id,
        B.c.title,
        B.c.content,
        B.c.audience,
        B.c.priority,
        B.c.channels,
        B.c.action_url,
        B.c.status,
        B.c.sent_at,
        B.c.recipient_id,
        B.c.personal_notification_id,
        B.c.group_notification_id,
        B.c.stats_json,
        B.c.created_by,
        B.c.created_at,
    )
    if status:
        page_stmt = page_stmt.where(B.c.status == status)
    page_cte = page_stmt.order_by(desc(B.c.created_at), desc(B.c.id)).limit(limit).offset(offset).cte("page")

    group_reads_sub = (
        select(
            GR.c.group_notification_id.label("grp_id"),
            func.count(GR.c.user_id).label("cnt"),
        )
        .where(GR.c.is_read.is_(True))
        .group_by(GR.c.group_notification_id)
        .subquery("gr")
    )
    personal_reads_sub = (
        select(
            N.c.source_id.label("bc_id"),
            func.count(N.c.id).label("cnt"),
        )
        .where(
            N.c.is_read.is_(True),
            N.c.deleted_at.is_(None),
            N.c.source_type == "broadcast",
            N.c.source_id.is_not(None),
        )
        .group_by(N.c.source_id)
        .subquery("nr")
    )

    page_joined = page_cte.outerjoin(
        group_reads_sub, group_reads_sub.c.grp_id == page_cte.c.group_notification_id
    ).outerjoin(
        personal_reads_sub, personal_reads_sub.c.bc_id == page_cte.c.id
    )
    read_count_col = case(
        (page_cte.c.group_notification_id.is_not(None), func.coalesce(group_reads_sub.c.cnt, 0)),
        else_=func.coalesce(personal_reads_sub.c.cnt, 0),
    ).label("read_count")
    stmt = (
        select(
            counts_cte.c.total_count,
            counts_cte.c.sent_count,
            page_cte,
            read_count_col,
        )
        .select_from(counts_cte.outerjoin(page_joined, true()))
        .order_by(desc(page_cte.c.created_at), desc(page_cte.c.id))
    )

    rows = execute_readonly(lambda conn: conn.execute(stmt).mappings().all())
    total = int(rows[0]["total_count"] or 0) if rows else 0
    sent_count = int(rows[0]["sent_count"] or 0) if rows else 0
    items = [_broadcast_row_to_dict(r) for r in rows if r["id"] is not None]
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
        "summary": {"sent": sent_count},
    }


def get_broadcast_detail(broadcast_id: str) -> Dict[str, Any]:
    B = BroadcastRecord.__table__
    GR = GroupNotificationReadRecord.__table__
    N = NotificationRecord.__table__
    bid = str(broadcast_id)
    grp_cnt = (
        select(func.count(GR.c.user_id))
        .where(
            GR.c.group_notification_id == B.c.group_notification_id,
            GR.c.is_read.is_(True),
        )
        .correlate(B)
        .scalar_subquery()
    )
    personal_cnt = (
        select(func.count(N.c.id))
        .where(
            N.c.source_id == B.c.id,
            N.c.source_type == "broadcast",
            N.c.deleted_at.is_(None),
            N.c.is_read.is_(True),
        )
        .correlate(B)
        .scalar_subquery()
    )
    read_count_col = case(
        (B.c.group_notification_id.is_not(None), func.coalesce(grp_cnt, 0)),
        else_=func.coalesce(personal_cnt, 0),
    ).label("read_count")
    stmt = select(B, read_count_col).where(B.c.id == bid)
    row = execute_readonly(lambda conn: conn.execute(stmt).mappings().first())
    if not row:
        raise ReportDomainError(404, "BROADCAST_NOT_FOUND", "Không tìm thấy thông báo hệ thống.")
    return _broadcast_row_to_dict(row)
