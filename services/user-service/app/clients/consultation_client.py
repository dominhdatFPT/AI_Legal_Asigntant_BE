"""Consultation Service tự đóng cuộc tư vấn qua sự kiện `user.blocked`."""

import logging
import uuid

logger = logging.getLogger(__name__)


def close_lawyer_consultations(lawyer_id: uuid.UUID, reason: str) -> None:
    """No-op giữ tương thích cho điểm gọi cũ. Consumer `user.blocked` xử lý việc đóng tư vấn."""
    try:
        # TODO(consultation-service): gọi API nội bộ khi Consultation Service có database.
        logger.info("Chưa đóng cuộc tư vấn của luật sư %s (%s): consultation-service chưa có API", lawyer_id, reason)
    except Exception:  # pragma: no cover - phòng khi thay bằng HTTP call thật
        logger.exception("Không đóng được cuộc tư vấn của luật sư %s", lawyer_id)
