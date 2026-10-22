"""Worker ghi audit log đăng xuất (queue `q.audit.user_logout`, routing key `user.logged_out`).

Thu hồi access token không còn làm ở đây: mọi service tự nghe `user.logged_out`
qua common_auth.revocation. Chạy: py -m app.services.logout_worker
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from common_auth.events import consume_forever

from app.core.rabbitmq import ROUTING_KEY_USER_LOGGED_OUT

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
logger = logging.getLogger("LogoutAuditWorker")

QUEUE_AUDIT = "q.audit.user_logout"


async def process_audit_log(routing_key: str, data: Dict[str, Any]) -> None:
    payload = data.get("payload") or {}
    logger.info(
        "[AUDIT-LOG] [AUDIT-EVENT:%s] User=%s đăng xuất từ IP=%s, Agent=%s",
        data.get("event_id"),
        payload.get("user_id"),
        payload.get("client_ip"),
        payload.get("user_agent"),
    )


if __name__ == "__main__":
    try:
        asyncio.run(consume_forever(QUEUE_AUDIT, [ROUTING_KEY_USER_LOGGED_OUT], process_audit_log))
    except KeyboardInterrupt:
        logger.info("Đã dừng worker.")
