import logging
import time
import uuid

from fastapi import APIRouter, Depends, Header
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.api.v1.conversations import _error, _ok, _request_id
from app.core.database import get_db
from app.core.internal_auth import require_internal_token
from app.repositories import conversation as repo
from app.schemas.minutes import MinutesOut, MinutesRequest
from app.schemas.summary import SummaryOut, SummaryRequest
from app.services import llm_client, minutes, minutes_prompt, summary

logger = logging.getLogger(__name__)

# Gateway không chuyển tiếp /internal/...: chỉ service khác trong mạng Docker gọi được, kèm X-Internal-Token
router = APIRouter(
    prefix="/internal/v1/agent/conversations",
    tags=["internal"],
    dependencies=[Depends(require_internal_token)],
)


def _load_messages(db: Session, conversation_id: uuid.UUID, payload: SummaryRequest) -> list[tuple[str, str]] | None:
    """(role, content) cũ → mới của các tin trước `until`; None nếu không có hội thoại của đúng chủ này."""
    if repo.get_conversation_of_user(db, conversation_id, payload.user_id) is None:
        return None
    messages = repo.get_messages_before(db, conversation_id, payload.until, limit=summary.MAX_MESSAGES)
    return [(m.role, m.content) for m in messages]


@router.post("/{conversation_id}/summary")
async def summarize_conversation(
    conversation_id: uuid.UUID,
    payload: SummaryRequest,
    db: Session = Depends(get_db),
    x_request_id: str | None = Header(default=None),
):
    """
    Tóm tắt các tin của hội thoại gửi trước `until` cho luật sư đọc. Không lưu bản tóm tắt; gọi lại cùng tham số là an toàn.
    Hội thoại không có / đã xóa / không phải của `user_id` đều trả cùng một lỗi 404 để không lộ hội thoại có tồn tại hay không.
    Log chỉ ghi conversation_id, số tin, thời gian, request_id, không ghi nội dung.
    """
    request_id = _request_id(x_request_id)
    started = time.monotonic()
    rows = await run_in_threadpool(_load_messages, db, conversation_id, payload)
    if rows is None:
        raise _error(404, "CONVERSATION_NOT_FOUND", "Không tìm thấy hội thoại.")
    transcript, used = summary.build_transcript(rows)
    if not used:
        raise _error(422, "CONVERSATION_EMPTY", "Hội thoại chưa có tin nhắn nào trước thời điểm yêu cầu.")

    try:
        text, model = await summary.summarize(transcript)
    except llm_client.LLMError as e:
        # Chỉ ghi loại lỗi, không ghi chi tiết: không để lọt nội dung tin nhắn vào log
        logger.error(
            "agent tóm tắt hội thoại lỗi request_id=%s conversation_id=%s số_tin=%d thời_gian=%.2fs: %s",
            request_id, conversation_id, used, time.monotonic() - started, type(e).__name__,
        )
        raise _error(503, "AI_SERVICE_ERROR", "Dịch vụ AI đang gặp sự cố, vui lòng thử lại sau.")

    logger.info(
        "agent tóm tắt hội thoại request_id=%s conversation_id=%s số_tin=%d/%d model=%s thời_gian=%.2fs",
        request_id, conversation_id, used, len(rows), model, time.monotonic() - started,
    )
    return _ok(SummaryOut(conversation_id=conversation_id, summary=text), request_id)


minutes_router = APIRouter(
    prefix="/internal/v1/agent/transcripts",
    tags=["internal"],
    dependencies=[Depends(require_internal_token)],
)


@minutes_router.post("/summary")
async def summarize_transcript(payload: MinutesRequest, x_request_id: str | None = Header(default=None)):
    """
    Trích biên bản JSON từ một đoạn chat user - luật sư (consultation-service gửi nội dung, đã che PII). Không lưu gì.
    Chỉ bảo đảm trả về một object JSON; consultation-service kiểm schema và số liệu nguyên văn. Log không ghi nội dung.
    """
    request_id = _request_id(x_request_id)
    started = time.monotonic()
    try:
        data, model = await minutes.summarize(payload)
    except llm_client.LLMError as e:
        logger.error("agent biên bản lỗi request_id=%s consultation_id=%s số_tin=%d thời_gian=%.2fs: %s", request_id,
                     payload.consultation_id, len(payload.messages), time.monotonic() - started, type(e).__name__)
        raise _error(503, "AI_SERVICE_ERROR", "Dịch vụ AI đang gặp sự cố, vui lòng thử lại sau.")
    except ValueError:  # json.JSONDecodeError cũng là ValueError
        logger.error("agent biên bản không phải JSON request_id=%s consultation_id=%s", request_id,
                     payload.consultation_id)
        raise _error(502, "AI_OUTPUT_INVALID", "AI không trả về JSON hợp lệ.")
    logger.info("agent biên bản request_id=%s consultation_id=%s số_tin=%d model=%s thời_gian=%.2fs", request_id,
                payload.consultation_id, len(payload.messages), model, time.monotonic() - started)
    return _ok(MinutesOut(summary=data, prompt_version=minutes_prompt.PROMPT_VERSION), request_id)
