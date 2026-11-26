"""Phát event `user.blocked` để thu hồi access token trên các service dùng common_auth.

Notification Service gửi thông báo khóa tài khoản khi sự kiện cho phép thông báo.
"""

from __future__ import annotations

from typing import Optional

from common_auth.events import publisher

ROUTING_KEY_USER_BLOCKED = "user.blocked"


class AdminMQClient:
    @staticmethod
    def blocked_payload(
        user_id: str,
        admin_id: str,
        reason: str,
        duration_days: Optional[int] = None,
        *,
        notify_user: bool = True,
        reason_code: str | None = None,
    ) -> dict:
        payload = {
            "user_id": str(user_id),
            "admin_id": str(admin_id),
            "reason": reason,
            "reason_code": reason_code or (reason if reason in ("role_changed", "deleted") else "locked"),
            "duration_days": duration_days,
        }
        if not notify_user:
            payload["notify_user"] = False
        return payload

    async def publish_user_blocked(
        self,
        user_id: str,
        admin_id: str,
        reason: str,
        duration_days: Optional[int] = None,
        *,
        notify_user: bool = True,
        reason_code: str | None = None,
    ) -> bool:
        payload = self.blocked_payload(
            user_id, admin_id, reason, duration_days, notify_user=notify_user, reason_code=reason_code,
        )
        return await publisher.publish(ROUTING_KEY_USER_BLOCKED, payload)


admin_mq = AdminMQClient()
