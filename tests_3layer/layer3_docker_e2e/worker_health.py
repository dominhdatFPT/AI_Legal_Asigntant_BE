"""Check that the Linux worker has an active subscription in the test broker."""
import asyncio
import os
import sys

import aio_pika


async def check(queue_name):
    connection = await aio_pika.connect(os.environ["RABBITMQ_URL"], timeout=2)
    async with connection:
        channel = await connection.channel()
        queue = await channel.declare_queue(queue_name, passive=True)
        if not queue.declaration_result.consumer_count:
            raise SystemExit(f"No consumer on {queue_name}")


if __name__ == "__main__":
    asyncio.run(check(sys.argv[1]))
