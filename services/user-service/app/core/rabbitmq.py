"""Phát event đăng xuất `user.logged_out` (publisher dùng chung ở common_auth.events).

Mọi service dùng common_auth nghe event này để thu hồi access token ngay (common_auth.revocation).
"""

from __future__ import annotations

import logging
from typing import Optional

from common_auth.events import EXCHANGE as EXCHANGE_NAME, publisher, rabbitmq_url as get_rabbitmq_url  # noqa: F401

logger = logging.getLogger(__name__)

ROUTING_KEY_USER_LOGGED_OUT = "user.logged_out"


async def publish_user_logged_out(
    user_id: str,
    role: str,
    jti: Optional[str] = None,
    exp: Optional[int] = None,
    client_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    request_id: Optional[str] = None,
) -> bool:
    try:
        return await publisher.publish(
            ROUTING_KEY_USER_LOGGED_OUT,
            {
                "user_id": str(user_id),
                "role": role,
                "jti": jti,
                "exp": exp,
                # client_ip/user_agent chỉ dành cho audit log (q.audit.user_logout).
                "client_ip": client_ip,
                "user_agent": user_agent,
                "request_id": request_id,
            },
        )
    except Exception as e:
        logger.warning("Lỗi khi phát sự kiện user.logged_out: %s", e)
        return False
