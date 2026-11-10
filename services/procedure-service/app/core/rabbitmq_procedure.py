"""Phát event `procedure.application.submitted` khi công dân nộp hồ sơ chính thức.

Notification Service nhận event này để báo tin cho người nộp (publisher dùng chung ở common_auth.events).
"""

from __future__ import annotations

import time
from common_auth.events import publisher

ROUTING_KEY_APPLICATION_SUBMITTED = "procedure.application.submitted"
ROUTING_KEY_FORM_TEMPLATE_DELETED = "procedure.form_template.deleted"


class ProcedureMQClient:
    async def publish_application_submitted(self, record_id: str, domain: str, user_id: str) -> bool:
        # Không gửi dữ liệu hồ sơ (thông tin cá nhân) lên bus; người nhận chỉ cần id.
        return await publisher.publish(
            ROUTING_KEY_APPLICATION_SUBMITTED,
            {"record_id": str(record_id), "domain": domain, "user_id": str(user_id)},
        )

    async def publish_form_template_deleted(self, template_id: str, admin_id: str = "") -> bool:
        """Phát sự kiện mẫu đơn bị xóa mềm để xử lý dọn dẹp hậu kỳ không đồng bộ."""
        return await publisher.publish(
            ROUTING_KEY_FORM_TEMPLATE_DELETED,
            {
                "template_id": str(template_id),
                "admin_id": str(admin_id),
                "timestamp": time.time(),
            },
        )


procedure_mq = ProcedureMQClient()
