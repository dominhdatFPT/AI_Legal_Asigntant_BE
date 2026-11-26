import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_consume_forever_poison_and_retries():
    from common_auth import events

    captured_on_message = None

    class FakeQueue:
        async def bind(self, exchange, routing_key): pass
        async def consume(self, callback):
            nonlocal captured_on_message
            captured_on_message = callback

    fake_channel = MagicMock()
    fake_channel.default_exchange = MagicMock()
    fake_channel.default_exchange.publish = AsyncMock()
    fake_channel.set_qos = AsyncMock()
    fake_channel.declare_exchange = AsyncMock()
    fake_channel.declare_queue = AsyncMock()

    class FakeConnection:
        async def channel(self, **kwargs): return fake_channel
        async def closed(self): pass
        async def __aenter__(self): return self
        async def __aexit__(self, exc_type, exc, tb): pass

    async def fake_handler(key, data):
        if data.get("fail"):
            raise RuntimeError("Database disconnected")

    with patch("common_auth.events.rabbitmq_url", return_value="amqp://fake"), \
         patch("common_auth.events.declare_work_queue", return_value=FakeQueue()), \
         patch("aio_pika.connect", return_value=FakeConnection()):

        task = asyncio.create_task(events.consume_forever("q.test", ["test.*"], fake_handler))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert captured_on_message is not None

    # Test 1: Poison message (invalid JSON) -> DLQ (reject requeue=False)
    msg1 = MagicMock()
    msg1.body = b"invalid json"
    msg1.headers = {}
    msg1.message_id = "msg-poison"
    msg1.routing_key = "test.key"
    msg1.ack = AsyncMock()
    msg1.reject = AsyncMock()

    await captured_on_message(msg1)
    msg1.reject.assert_awaited_once_with(requeue=False)
    msg1.ack.assert_not_called()

    # Test 2: Transient failure with count=0 -> publishes retry.1 and ACKs original
    msg2 = MagicMock()
    msg2.body = json.dumps({"fail": True}).encode()
    msg2.headers = {"x-retry-count": 0}
    msg2.message_id = "msg-transient"
    msg2.content_type = "application/json"
    msg2.correlation_id = "corr-1"
    msg2.routing_key = "test.key"
    msg2.ack = AsyncMock()
    msg2.reject = AsyncMock()

    await captured_on_message(msg2)
    fake_channel.default_exchange.publish.assert_awaited_once()
    _, kwargs = fake_channel.default_exchange.publish.call_args
    assert kwargs["routing_key"] == "q.test.retry.1"
    msg2.ack.assert_awaited_once()
    msg2.reject.assert_not_called()

    # Test 3: Exceeded retries (count=3) -> DLQ (reject requeue=False)
    fake_channel.default_exchange.publish.reset_mock()
    msg3 = MagicMock()
    msg3.body = json.dumps({"fail": True}).encode()
    msg3.headers = {"x-retry-count": 3}
    msg3.message_id = "msg-exceeded"
    msg3.content_type = "application/json"
    msg3.correlation_id = "corr-2"
    msg3.routing_key = "test.key"
    msg3.ack = AsyncMock()
    msg3.reject = AsyncMock()

    await captured_on_message(msg3)
    fake_channel.default_exchange.publish.assert_not_called()
    msg3.reject.assert_awaited_once_with(requeue=False)
    msg3.ack.assert_not_called()
