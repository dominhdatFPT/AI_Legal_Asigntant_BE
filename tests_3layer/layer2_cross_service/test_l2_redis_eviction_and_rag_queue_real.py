import asyncio
import json
import time
from uuid import uuid4

import aio_pika
import pytest
import redis

from common_auth.events import declare_work_queue
from tests_3layer.support import L2_AMQP, L2_REDIS, service_module


def test_volatile_lru_preserves_corpus_version_under_memory_pressure():
    prefix = f"3layer:eviction:{uuid4().hex}:"
    with redis.Redis.from_url(L2_REDIS, socket_timeout=2, decode_responses=True) as client:
        baseline = client.info("stats")["evicted_keys"]
        assert client.config_get("maxmemory-policy") == {"maxmemory-policy": "volatile-lru"}
        assert client.config_get("maxmemory") == {"maxmemory": str(24 * 1024**2)}
        client.set("legal:corpus_version", "777")
        try:
            payload = "x" * 128 * 1024
            for start in range(0, 320, 20):
                with client.pipeline(transaction=False) as pipe:
                    for index in range(start, start + 20):
                        pipe.setex(f"{prefix}{index}", 60, payload)
                    assert all(pipe.execute())
            assert client.info("stats")["evicted_keys"] > baseline
            assert client.get("legal:corpus_version") == "777"
            assert client.ttl("legal:corpus_version") == -1
        finally:
            keys = list(client.scan_iter(f"{prefix}*"))
            if keys:
                client.delete(*keys)
            client.delete("legal:corpus_version")


def test_rag_command_requires_a_live_consumer():
    with service_module("rag", "app.rag_indexing_queue") as publisher:
        job = {"action": "approve", "document_id": str(uuid4()), "actor_user_id": str(uuid4()), "actor_role": "admin"}
        started = time.monotonic()
        assert publisher.publish_indexing_job(job) is False
        assert time.monotonic() - started < 3

        async def scenario():
            connection = await aio_pika.connect(L2_AMQP, timeout=3)
            async with connection:
                channel = await connection.channel()
                queue = await declare_work_queue(channel, publisher.QUEUE)
                assert queue.declaration_result.message_count == 0
                received = asyncio.Queue()

                async def consume(message):
                    async with message.process():
                        received.put_nowait(json.loads(message.body))

                tag = await queue.consume(consume)
                try:
                    assert await asyncio.wait_for(asyncio.to_thread(publisher.publish_indexing_job, job), 5)
                    assert await asyncio.wait_for(received.get(), 3) == job
                    with pytest.raises(asyncio.TimeoutError):
                        await asyncio.wait_for(received.get(), 0.25)
                finally:
                    await queue.cancel(tag)

        asyncio.run(scenario())
