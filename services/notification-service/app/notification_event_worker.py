"""Notification Event Hub Worker Process (Feature 03).

Khởi động tiến trình lắng nghe toàn bộ sự kiện từ RabbitMQ Topic Exchange `legal.events.topic`.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from app.core.rabbitmq_notification_hub import start_notification_hub

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [NotifHub] %(message)s",
)
logger = logging.getLogger("NotifHub")

if __name__ == "__main__":
    try:
        asyncio.run(start_notification_hub())
    except KeyboardInterrupt:
        logger.info("Đã dừng Notification Event Hub.")
        sys.exit(0)
