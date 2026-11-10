"""Biên bản hội thoại: cuộc im lặng 10 phút (hoặc đã đóng, hoặc đủ tin) thì gửi các tin chưa tóm tắt cho Agent,
kiểm kết quả rồi lưu JSON vào consultation_summaries. Tin gốc không bị xóa ở đây (dọn sau 10 ngày là việc khác).
Không giữ kết nối/khóa database trong lúc chờ Agent."""

import asyncio
import logging
import uuid

from app.core import database
from app.core.responses import ApiError
from app.core.config import SUMMARY_BATCH, SUMMARY_IDLE, SUMMARY_SWEEP_SECONDS
from app.models.consultation import as_utc, utcnow
from app.repositories import consultation_repository as repo
from app.services import agent_client
from app.services.http_client import UpstreamError
from app.services.pii import mask_pii
from app.services.transcript_validator import SCHEMA_VERSION, SummaryInvalid, validate_summary

logger = logging.getLogger("consultation-service")

MAX_FAILURES = 3
# shortcut: đếm lỗi trong bộ nhớ, mất khi khởi động lại; cần bền hơn thì thêm cột đếm lỗi vào consultations.
_failures: dict[uuid.UUID, int] = {}


def _iso(value) -> str:
    return as_utc(value).isoformat()


def summarize_next_batch(consultation_id: uuid.UUID, session_factory=None) -> bool:
    """Tóm tắt một lô tin chưa tóm tắt của cuộc. True = đã ghi một biên bản (có thể còn lô sau)."""
    if _failures.get(consultation_id, 0) >= MAX_FAILURES:
        return False
    new_session = session_factory or database.new_session
    with new_session() as db:
        c = repo.get_visible(db, consultation_id, utcnow())
        if c is None:
            return False
        since = as_utc(c.summarized_until)
        messages = repo.unsummarized_messages(db, c.id, since, SUMMARY_BATCH)
        if not messages:
            return False
        sent = [{"id": str(m.id), "role": "CITIZEN" if m.sender_id == c.citizen_user_id else "LAWYER",
                 "time": _iso(m.created_at), "content": mask_pii(m.content)} for m in messages]
        window_end = as_utc(messages[-1].created_at)
        previous = repo.latest_summary(db, c.id)
        payload = {
            "consultation_id": str(c.id), "window_start": since.isoformat() if since else None,
            "window_end": window_end.isoformat(),
            "previous_summary": {k: previous.get(k) for k in ("overview", "legal_domains")} if previous else None,
            "messages": sent,
        }
        db.rollback()  # trả kết nối về pool trước khi chờ LLM

        try:
            raw, prompt_version = agent_client.summarize_transcript(payload)
            summary = validate_summary(raw, [{"id": m["id"], "content": m["content"]} for m in sent])
        except (ApiError, UpstreamError, SummaryInvalid) as e:
            _failures[consultation_id] = _failures.get(consultation_id, 0) + 1
            logger.warning("Tóm tắt hội thoại lỗi consultation_id=%s lần=%s: %s", consultation_id,
                           _failures[consultation_id], type(e).__name__)
            return False
        if not repo.save_window_summary(db, c.id, since, window_end, len(messages), summary, SCHEMA_VERSION,
                                        prompt_version):
            db.rollback()
            return False
        db.commit()
        _failures.pop(consultation_id, None)
        return True


def summarize_all(consultation_id: uuid.UUID, session_factory=None) -> None:
    """Tóm tắt hết tin còn lại của cuộc (dùng khi đóng cuộc). Lỗi chỉ ghi log: vòng quét sẽ thử lại."""
    try:
        while summarize_next_batch(consultation_id, session_factory):
            pass
    except Exception as e:  # noqa: BLE001  việc nền không được làm hỏng gì
        logger.warning("Tóm tắt hội thoại lỗi consultation_id=%s: %s", consultation_id, type(e).__name__)


def run_sweep(session_factory=None) -> int:
    """Một lượt quét: trả số biên bản đã ghi."""
    with (session_factory or database.new_session)() as db:
        ids = repo.summary_candidates(db, utcnow(), SUMMARY_IDLE, SUMMARY_BATCH, limit=50)
    written = 0
    for consultation_id in ids:
        try:
            while summarize_next_batch(consultation_id, session_factory):
                written += 1
        except Exception as e:  # noqa: BLE001  một cuộc lỗi không làm hỏng cả lượt quét
            logger.warning("Quét tóm tắt lỗi consultation_id=%s: %s", consultation_id, type(e).__name__)
    return written


async def sweep_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(run_sweep)
        except Exception as e:  # noqa: BLE001  vòng quét không được chết
            logger.warning("Vòng quét tóm tắt lỗi: %s", type(e).__name__)
        await asyncio.sleep(SUMMARY_SWEEP_SECONDS)
