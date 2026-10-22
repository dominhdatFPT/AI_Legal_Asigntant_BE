"""Phát event `lawyer.approved` (chưa có luồng duyệt nào gọi tới).

Event này CHỈ dùng để thông báo. Không consumer nào được nâng quyền dựa trên nó: ai có quyền
ghi lên broker đều phát được event, nên đổi role phải đi qua API admin của User Service
(hoặc consumer phải tự kiểm tra lại hồ sơ đã duyệt trong DB của Lawyer Service).
"""

from __future__ import annotations

from typing import Optional

from common_auth.events import publisher

ROUTING_KEY_LAWYER_APPROVED = "lawyer.approved"


class LawyerMQClient:
    async def publish_lawyer_approved(
        self,
        lawyer_id: str,
        user_id: str,
        license_number: str,
        bar_association: str,
        approved_by: Optional[str] = None,
    ) -> bool:
        return await publisher.publish(
            ROUTING_KEY_LAWYER_APPROVED,
            {
                "lawyer_id": str(lawyer_id),
                "user_id": str(user_id),
                "license_number": license_number,
                "bar_association": bar_association,
                "approved_by": approved_by or "admin",
            },
        )


lawyer_mq = LawyerMQClient()
