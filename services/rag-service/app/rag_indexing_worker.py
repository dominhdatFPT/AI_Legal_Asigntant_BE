"""Run with python -m app.rag_indexing_worker."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from common_auth.events import declare_work_queue, rabbitmq_url
from app.rag_indexing_queue import QUEUE
from app.services.document_approval import approve_document_background
from app.services.document_replace import replace_document_background

logger = logging.getLogger(__name__)


async def handle(message: Any, channel: Any) -> None:
    import aio_pika

    try:
        job = json.loads(message.body)
        if not isinstance(job, dict) or job.get("action") not in ("approve", "replace"):
            raise ValueError("Invalid indexing action")
        document_id = uuid.UUID(job["document_id"])
        actor = {"actor_user_id": job.get("actor_user_id"), "actor_role": job.get("actor_role")}
        if job["action"] == "replace":
            if not isinstance(job.get("new_content"), str) or not job["new_content"].strip():
                raise ValueError("Invalid replacement content")
            await asyncio.to_thread(replace_document_background, document_id, job["new_content"], **actor)
        else:
            await asyncio.to_thread(approve_document_background, document_id, **actor)
    except (ValueError, TypeError, KeyError):
        logger.exception("Invalid indexing message: routing to DLQ")
        await message.reject(requeue=False)
    except Exception:
        logger.exception("Indexing worker failed")
        headers = dict(message.headers or {})
        try:
            count = int(headers.get("x-retry-count", 0))
        except (TypeError, ValueError):
            count = 0
        if count >= 3:
            logger.error("Max retries exceeded for indexing message %s, routing to DLQ", message.message_id)
            await message.reject(requeue=False)
            return
        headers["x-retry-count"] = count + 1
        try:
            retry_msg = aio_pika.Message(
                body=message.body,
                headers=headers,
                content_type=message.content_type,
                message_id=message.message_id,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            )
            await channel.default_exchange.publish(
                retry_msg, routing_key=f"{QUEUE}.retry.{count + 1}", mandatory=True
            )
            await message.ack()
        except Exception:
            logger.exception("Retry publish failed; closing channel to restore original delivery")
            await channel.close()
    else:
        await message.ack()


async def main() -> None:
    import aio_pika

    url = rabbitmq_url()
    if not url:
        raise RuntimeError("RABBITMQ_URL is required for the indexing worker")
    while True:
        try:
            connection = await aio_pika.connect(url, timeout=5)
            async with connection:
                channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
                await channel.set_qos(prefetch_count=1)
                queue = await declare_work_queue(channel, QUEUE)
                for attempt, delay in enumerate((10000, 60000, 300000), 1):
                    await channel.declare_queue(
                        f"{QUEUE}.retry.{attempt}",
                        durable=True,
                        arguments={
                            "x-message-ttl": delay,
                            "x-dead-letter-exchange": "",
                            "x-dead-letter-routing-key": QUEUE,
                        },
                    )
                await queue.consume(lambda msg: handle(msg, channel))
                await connection.closed()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Indexing consumer disconnected")
        await asyncio.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
