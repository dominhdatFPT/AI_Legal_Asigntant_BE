"""Notification Event Hub Worker Process (Feature 03).

Khởi động tiến trình lắng nghe toàn bộ sự kiện từ RabbitMQ Topic Exchange `legal.events.topic`.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from app.core.rabbitmq_notification_hub import start_notification_hub
from app.report_enrichment_worker import start_enrichment_worker

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [NotifHub] %(message)s",
)
logger = logging.getLogger("NotifHub")


async def _run() -> None:
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    handlers = []

    def request_stop(_signum, _frame):
        loop.call_soon_threadsafe(stop_event.set)

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous = signal.getsignal(signum)
            try:
                loop.add_signal_handler(signum, stop_event.set)
            except NotImplementedError:
                signal.signal(signum, request_stop)
                handlers.append((signum, previous, False))
            else:
                handlers.append((signum, previous, True))

        hub = asyncio.create_task(start_notification_hub())
        enrichment = asyncio.create_task(start_enrichment_worker())
        workers = asyncio.gather(hub, enrichment)
        stop = asyncio.create_task(stop_event.wait())
        try:
            done, _ = await asyncio.wait((workers, stop), return_when=asyncio.FIRST_COMPLETED)
            if workers in done:
                workers.result()
        finally:
            for task in (hub, enrichment, stop):
                task.cancel()
            await asyncio.gather(hub, enrichment, stop, workers, return_exceptions=True)
    finally:
        for signum, previous, uses_loop in reversed(handlers):
            if uses_loop:
                loop.remove_signal_handler(signum)
            if previous is not None:
                signal.signal(signum, previous)


if __name__ == "__main__":
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("Đã dừng Notification Event Hub.")
        sys.exit(0)
