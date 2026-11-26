"""Realtime Notification Dispatcher (SSE & RabbitMQ Exclusive Queue).

Cho phép đẩy thông báo tức thì (Realtime) tới trình duyệt người dùng, luật sư và admin
qua chuẩn Server-Sent Events (`GET /api/v1/notifications/stream`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("RealtimeBroker")
CHANNEL = "legal:notifications:realtime"
INSTANCE_ID = uuid.uuid4().hex


class RealtimeBroker:
    def __init__(self) -> None:
        # Lưu tập hợp (user_id, role, asyncio.Queue)
        self._subscribers: Set[Tuple[str, str, asyncio.Queue]] = set()
        self._redis_lock = threading.Lock()
        self._redis_url: Optional[str] = None
        self._redis_client: Any = None

    def subscribe(self, user_id: str, role: str) -> Tuple[str, str, asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        sub = (str(user_id), str(role), queue)
        self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Tuple[str, str, asyncio.Queue]) -> None:
        self._subscribers.discard(sub)

    def _deliver_local_user(self, user_id: str, notification: Dict[str, Any]) -> None:
        target_uid = str(user_id)
        for uid, _role, q in list(self._subscribers):
            if uid == target_uid:
                try:
                    q.put_nowait(notification)
                except asyncio.QueueFull:
                    pass

    def _deliver_local_audience(self, audience: str, notification: Dict[str, Any]) -> None:
        target_aud = str(audience)
        for _uid, role, q in list(self._subscribers):
            if target_aud == "all" or role == target_aud:
                try:
                    q.put_nowait(notification)
                except asyncio.QueueFull:
                    pass

    def push_to_user(self, user_id: str, notification: Dict[str, Any], *, relay: bool = True) -> None:
        if relay:
            self._publish("user", user_id, notification)
        self._deliver_local_user(user_id, notification)

    def push_to_users(self, items: List[Tuple[str, Dict[str, Any]]], *, relay: bool = True) -> None:
        if not items:
            return
        if len(items) == 1:
            uid, notification = items[0]
            self.push_to_user(uid, notification, relay=relay)
            return
        if relay:
            self._publish_many([("user", uid, notif) for uid, notif in items])
        for uid, notif in items:
            self._deliver_local_user(uid, notif)

    def push_to_audience(self, audience: str, notification: Dict[str, Any], *, relay: bool = True) -> None:
        if relay:
            self._publish("audience", audience, notification)
        self._deliver_local_audience(audience, notification)

    def _get_redis_client(self, url: str):
        import redis

        with self._redis_lock:
            if self._redis_client is None or self._redis_url != url:
                if self._redis_client is not None:
                    try:
                        self._redis_client.close()
                    except Exception:
                        pass
                self._redis_url = url
                self._redis_client = redis.Redis.from_url(
                    url,
                    socket_connect_timeout=0.5,
                    socket_timeout=0.5,
                    health_check_interval=30,
                )
            return self._redis_client

    def _reset_redis_client(self, failed_client: Any) -> None:
        with self._redis_lock:
            if self._redis_client is failed_client:
                self._redis_client = None
                self._redis_url = None
                try:
                    failed_client.close()
                except Exception:
                    pass

    def _publish(self, kind: str, target: str, notification: Dict[str, Any]) -> None:
        self._publish_many([(kind, target, notification)])

    def _publish_many(self, events: List[Tuple[str, str, Dict[str, Any]]]) -> None:
        url = os.getenv("REDIS_URL")
        if not url or not events:
            return
        client = None
        try:
            client = self._get_redis_client(url)
            if len(events) == 1:
                kind, target, notification = events[0]
                client.publish(
                    CHANNEL,
                    json.dumps(
                        {
                            "origin": INSTANCE_ID,
                            "kind": kind,
                            "target": str(target),
                            "notification": notification,
                        },
                        default=str,
                    ),
                )
            else:
                pipe = client.pipeline(transaction=False)
                for kind, target, notification in events:
                    pipe.publish(
                        CHANNEL,
                        json.dumps(
                            {
                                "origin": INSTANCE_ID,
                                "kind": kind,
                                "target": str(target),
                                "notification": notification,
                            },
                            default=str,
                        ),
                    )
                pipe.execute()
        except Exception as exc:
            logger.warning("Realtime Redis publish failed: %s", exc)
            if client is not None:
                self._reset_redis_client(client)

    def close(self) -> None:
        for _, _, q in list(self._subscribers):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass
        with self._redis_lock:
            client = self._redis_client
            self._redis_client = None
            self._redis_url = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


realtime_broker = RealtimeBroker()


async def start_redis_listener(redis_url: str) -> None:
    import redis.asyncio as redis
    while True:
        client = redis.Redis.from_url(redis_url, socket_connect_timeout=2)
        pubsub = client.pubsub()
        try:
            await pubsub.subscribe(CHANNEL)
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    event = json.loads(message["data"])
                    if event.get("origin") == INSTANCE_ID:
                        continue
                    if event["kind"] == "user":
                        realtime_broker.push_to_user(event["target"], event["notification"], relay=False)
                    elif event["kind"] == "audience":
                        realtime_broker.push_to_audience(event["target"], event["notification"], relay=False)
                except (ValueError, KeyError, TypeError):
                    logger.warning("Invalid realtime Redis message")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Realtime Redis listener disconnected: %s", exc)
        finally:
            await pubsub.aclose()
            await client.aclose()
        await asyncio.sleep(5)
