"""Phát event `procedure.application.submitted` khi công dân nộp hồ sơ chính thức.

Notification Service nhận event này để báo tin cho người nộp (publisher dùng chung ở common_auth.events).
"""

from __future__ import annotations

from common_auth.events import publisher

ROUTING_KEY_APPLICATION_SUBMITTED = "procedure.application.submitted"


class ProcedureMQClient:
    async def publish_application_submitted(self, record_id: str, domain: str, user_id: str) -> bool:
        # Không gửi dữ liệu hồ sơ (thông tin cá nhân) lên bus; người nhận chỉ cần id.
        return await publisher.publish(
            ROUTING_KEY_APPLICATION_SUBMITTED,
            {"record_id": str(record_id), "domain": domain, "user_id": str(user_id)},
        )


procedure_mq = ProcedureMQClient()
