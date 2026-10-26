"""Domain event trên RabbitMQ (`legal.events.topic`), dùng chung cho mọi service.

- Chỉ đọc kết nối từ RABBITMQ_URL, không có tài khoản/mật khẩu mặc định.
- Publisher không bao giờ ném lỗi ra API. Kết nối cũ hỏng thì thử lại ngay một lần với kết nối mới;
  chỉ khi không kết nối được broker mới nghỉ RETRY_AFTER giây để request không phải chờ timeout.
- Consumer: handler lỗi -> message bị reject sang `q.dead_letter`, không bị ack âm thầm.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable

logger = logging.getLogger("common_auth.events")

EXCHANGE = "legal.events.topic"
DEAD_LETTER_EXCHANGE = "legal.events.dlx"
DEAD_LETTER_QUEUE = "q.dead_letter"
RETRY_AFTER = 10.0

Handler = Callable[[str, dict], Awaitable[None]]


def rabbitmq_url() -> str | None:
    """URL broker, hoặc None khi chưa cấu hình / bị tắt bằng RABBITMQ_ENABLED=false."""
    if os.getenv("RABBITMQ_ENABLED", "true").strip().lower() in ("false", "0", "no", "disabled"):
        return None
    return os.getenv("RABBITMQ_URL") or None


def masked(url: str) -> str:
    """Bỏ phần user:password trước khi ghi log."""
    return url.rsplit("@", 1)[-1]


def make_event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": f"evt-{uuid.uuid4().hex}",
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }


class EventPublisher:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self._connection = None
        self._channel = None
        self._exchange = None
        self._retry_at = 0.0

    async def _get_exchange(self, url: str):
        import aio_pika

        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            # Kết nối thuộc về event loop đã tạo ra nó (TestClient, asyncio.run tạo loop mới).
            self._loop, self._lock = loop, asyncio.Lock()
            self._connection = self._channel = self._exchange = None
        async with self._lock:
            if self._connection is None or self._connection.is_closed or self._channel.is_closed:
                await self.close()
                self._connection = await aio_pika.connect(url, timeout=2)
                self._channel = await self._connection.channel()
                self._exchange = await self._channel.declare_exchange(
                    EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
                )
            return self._exchange

    async def publish(self, routing_key: str, payload: dict[str, Any]) -> bool:
        """Trả về True khi broker đã xác nhận (publisher confirm), False nếu bỏ qua hoặc lỗi."""
        url = rabbitmq_url()
        if not url or time.monotonic() < self._retry_at:
            return False
        import aio_pika

        event = make_event(routing_key, payload)
        body = json.dumps(event, default=str).encode("utf-8")
        # Đang có kết nối mà publish lỗi (kết nối chết ngầm, mạng chập) thì thử lại 1 lần với kết nối mới.
        attempts = 2 if self._connection is not None else 1
        for attempt in range(1, attempts + 1):
            try:
                exchange = await asyncio.wait_for(self._get_exchange(url), timeout=3)
                message = aio_pika.Message(
                    body=body,
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    message_id=event["event_id"],
                )
                await asyncio.wait_for(exchange.publish(message, routing_key=routing_key), timeout=3)
                return True
            except Exception as exc:
                await self.close()
                if attempt == attempts:
                    logger.warning("Không phát được event %s lên %s: %s", routing_key, masked(url), exc)
                    self._retry_at = time.monotonic() + RETRY_AFTER
        return False

    async def close(self) -> None:
        connection = self._connection
        self._connection = self._channel = self._exchange = None
        if connection is not None and not connection.is_closed:
            try:
                await asyncio.wait_for(connection.close(), timeout=2)
            except Exception:
                pass


publisher = EventPublisher()


async def publish_event(routing_key: str, payload: dict[str, Any]) -> bool:
    return await publisher.publish(routing_key, payload)


def publish_event_sync(routing_key: str, payload: dict[str, Any]) -> bool:
    """Cho worker đồng bộ (không có event loop): mở kết nối, phát, đóng."""

    async def once() -> bool:
        one_shot = EventPublisher()
        try:
            return await one_shot.publish(routing_key, payload)
        finally:
            await one_shot.close()

    return asyncio.run(once())


async def declare_work_queue(channel, name: str):
    """Queue bền vững có hàng đợi chết và giới hạn độ dài, để broker không phình vô hạn."""
    import aio_pika

    dlx = await channel.declare_exchange(DEAD_LETTER_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True)
    dlq = await channel.declare_queue(DEAD_LETTER_QUEUE, durable=True, arguments={"x-max-length": 10_000})
    await dlq.bind(dlx)
    return await channel.declare_queue(
        name,
        durable=True,
        arguments={"x-dead-letter-exchange": DEAD_LETTER_EXCHANGE, "x-max-length": 100_000},
    )


async def consume_forever(
    queue_name: str | None,
    routing_keys: Iterable[str],
    handler: Handler,
    prefetch: int = 10,
) -> None:
    """Nghe `legal.events.topic`, tự kết nối lại khi rớt mạng.

    queue_name=None: queue tạm, riêng cho process này (mỗi process đều nhận đủ event).
    """
    import aio_pika

    url = rabbitmq_url()
    if not url:
        logger.warning("Chưa cấu hình RABBITMQ_URL (hoặc RABBITMQ_ENABLED=false): consumer %s không chạy.", queue_name)
        return
    routing_keys = list(routing_keys)

    async def on_message(message) -> None:
        try:
            await handler(message.routing_key or "", json.loads(message.body))
        except Exception:
            logger.exception("Xử lý message %s thất bại, chuyển sang %s.", message.message_id, DEAD_LETTER_QUEUE)
            await message.reject(requeue=False)
        else:
            await message.ack()

    while True:
        try:
            connection = await aio_pika.connect(url, timeout=5)
            async with connection:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=prefetch)
                exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
                if queue_name:
                    queue = await declare_work_queue(channel, queue_name)
                else:
                    queue = await channel.declare_queue(exclusive=True)
                for key in routing_keys:
                    await queue.bind(exchange, routing_key=key)
                await queue.consume(on_message)
                logger.info("Consumer %s đang nghe %s tại %s.", queue_name or "(tạm)", routing_keys, masked(url))
                await connection.closed()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Consumer %s mất kết nối tới %s (%s), thử lại sau 5 giây.", queue_name, masked(url), exc)
        await asyncio.sleep(5)
