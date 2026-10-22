"""Phát event `user.blocked` khi admin khoá tài khoản.

Mọi service dùng common_auth từ chối ngay các access token phát hành trước lúc khoá
(common_auth.revocation); Notification Service gửi thông báo cho người dùng.
"""

from __future__ import annotations

from typing import Optional

from common_auth.events import publisher

ROUTING_KEY_USER_BLOCKED = "user.blocked"


class AdminMQClient:
    async def publish_user_blocked(
        self,
        user_id: str,
        admin_id: str,
        reason: str,
        duration_days: Optional[int] = None,
    ) -> bool:
        return await publisher.publish(
            ROUTING_KEY_USER_BLOCKED,
            {"user_id": str(user_id), "admin_id": str(admin_id), "reason": reason, "duration_days": duration_days},
        )


admin_mq = AdminMQClient()
