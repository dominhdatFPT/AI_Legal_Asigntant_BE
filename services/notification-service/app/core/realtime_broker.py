"""Realtime Notification Dispatcher (SSE & RabbitMQ Exclusive Queue).

Cho phép đẩy thông báo tức thì (Realtime) tới trình duyệt người dùng, luật sư và admin
qua chuẩn Server-Sent Events (`GET /api/v1/notifications/stream`).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Set, Tuple

logger = logging.getLogger("RealtimeBroker")


class RealtimeBroker:
    def __init__(self) -> None:
        # Lưu tập hợp (user_id, role, asyncio.Queue)
        self._subscribers: Set[Tuple[str, str, asyncio.Queue]] = set()

    def subscribe(self, user_id: str, role: str) -> Tuple[str, str, asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        sub = (str(user_id), str(role), queue)
        self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Tuple[str, str, asyncio.Queue]) -> None:
        self._subscribers.discard(sub)

    def push_to_user(self, user_id: str, notification: Dict[str, Any]) -> None:
        target_uid = str(user_id)
        for uid, _role, q in list(self._subscribers):
            if uid == target_uid:
                try:
                    q.put_nowait(notification)
                except asyncio.QueueFull:
                    pass

    def push_to_audience(self, audience: str, notification: Dict[str, Any]) -> None:
        target_aud = str(audience)
        for _uid, role, q in list(self._subscribers):
            if target_aud == "all" or role == target_aud:
                try:
                    q.put_nowait(notification)
                except asyncio.QueueFull:
                    pass


realtime_broker = RealtimeBroker()
