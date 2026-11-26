"""Tự động xóa hội thoại (và toàn bộ tin nhắn ON DELETE CASCADE) khi quá hạn không có tin nhắn mới.

- Nguồn chân lý là cột `conversations.updated_at` (cập nhật lúc tạo và mỗi khi lưu cặp tin nhắn mới).
- Xóa theo từng lô nhỏ trong transaction ngắn để tránh giữ khóa lâu; sau khi DB commit mới xóa khóa tương ứng
  trong Redis `history_cache`.
- Chỉ ghi log tổng số lượng đã xóa và loại lỗi; không bao giờ log nội dung tin nhắn hay user_id."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.core import database
from app.core.config import get_settings
from app.repositories import conversation as repo
from app.services import history_cache

logger = logging.getLogger(__name__)

MAX_BATCHES_PER_SWEEP = 50


def retention_cutoff(now: datetime | None = None) -> datetime | None:
    """Mốc thời gian hết hạn (UTC). Trả None nếu CONVERSATION_RETENTION_DAYS <= 0."""
    days = get_settings().conversation_retention_days
    if days <= 0:
        return None
    current = datetime.now(timezone.utc) if now is None else (
        now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    )
    return current - timedelta(days=days)


def purge_expired_once(
    session_factory=None,
    *,
    now: datetime | None = None,
    batch_size: int | None = None,
    max_batches: int = MAX_BATCHES_PER_SWEEP,
) -> int:
    """Xóa các hội thoại quá hạn theo từng lô ngắn; trả tổng số hội thoại đã xóa."""
    cutoff = retention_cutoff(now)
    if cutoff is None:
        return 0
    settings = get_settings()
    limit = settings.conversation_cleanup_batch_size if batch_size is None else max(1, batch_size)
    make_session = session_factory or database.new_session
    total_deleted = 0

    for _ in range(max(1, max_batches)):
        with make_session() as db:
            deleted_rows = repo.delete_expired_conversations(db, cutoff, batch_size=limit)
        if not deleted_rows:
            break
        for user_id, conversation_id in deleted_rows:
            history_cache.invalidate(user_id, conversation_id)
        total_deleted += len(deleted_rows)
        if len(deleted_rows) < limit:
            break

    if total_deleted > 0:
        logger.info(
            "Đã tự động xóa %d hội thoại quá %d ngày không hoạt động",
            total_deleted,
            settings.conversation_retention_days,
        )
    return total_deleted


async def cleanup_loop() -> None:
    """Vòng quét nền xóa hội thoại quá hạn theo chu kỳ CONVERSATION_CLEANUP_INTERVAL_SECONDS."""
    while True:
        settings = get_settings()
        if settings.conversation_retention_days <= 0 or settings.conversation_cleanup_interval_seconds <= 0:
            return
        try:
            await asyncio.to_thread(purge_expired_once)
        except Exception as exc:  # noqa: BLE001 - tác vụ nền không được làm gián đoạn API
            logger.warning("Quét xóa hội thoại quá hạn lỗi (%s)", type(exc).__name__)
        await asyncio.sleep(settings.conversation_cleanup_interval_seconds)
