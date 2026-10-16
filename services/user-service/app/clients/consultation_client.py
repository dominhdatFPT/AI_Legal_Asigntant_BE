"""Gọi sang Consultation Service.

Consultation Service CHƯA có database nên hiện tại hàm chỉ ghi log. Khi có API thật, chỉ cần sửa ruột
hàm `close_lawyer_consultations` (gọi POST /internal/v1/consultations/close-by-lawyer/{lawyer_id}
kèm X-Internal-Token), code của User Service không phải đổi.
"""

import logging
import uuid

logger = logging.getLogger(__name__)


def close_lawyer_consultations(lawyer_id: uuid.UUID, reason: str) -> None:
    """Kết thúc mọi cuộc tư vấn đang mở của luật sư (khi bị khóa, bị xóa hoặc bị hạ vai trò).

    Không bao giờ ném lỗi ra ngoài: tài khoản đã khóa/xóa trong DB là việc chính, đóng chat chỉ là việc phụ.
    """
    try:
        # TODO(consultation-service): gọi API nội bộ khi Consultation Service có database.
        logger.info("Chưa đóng cuộc tư vấn của luật sư %s (%s): consultation-service chưa có API", lawyer_id, reason)
    except Exception:  # pragma: no cover - phòng khi thay bằng HTTP call thật
        logger.exception("Không đóng được cuộc tư vấn của luật sư %s", lawyer_id)
