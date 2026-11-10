"""Viết lại câu hỏi thành MỘT câu tìm kiếm văn bản pháp luật: nhờ LLM đổi từ đời thường sang thuật ngữ luật và ghép chủ đề từ
lịch sử cho câu hỏi nối tiếp ("thế còn đổi địa chỉ thì sao"). Lỗi bất kỳ thì trả câu gốc, không làm hỏng luồng hỏi đáp.
Không kiểm tra cờ QUERY_REWRITE_ENABLED: nơi gọi kiểm tra. Hiện chưa được nối vào luồng nào."""
import asyncio
import logging
import re
import time

from app.core.config import get_settings
from app.services import llm_client, prompts

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 150  # chỉ cần một dòng
HISTORY_MESSAGES = 4
HISTORY_CHARS = 500
MIN_REWRITE_CHARS = 5
MAX_REWRITE_CHARS = 500
LOG_CHARS = 200

_QUOTES = " \t\"'“”‘’«»`"
_PREFIX = re.compile(r"^(?:câu\s+hỏi|câu\s+tìm\s+kiếm)\s*:\s*", re.IGNORECASE)


def _clean(text: str) -> str:
    """Dòng không rỗng đầu tiên, bỏ khoảng trắng và dấu ngoặc kép bao ngoài, bỏ tiền tố "Câu hỏi:" / "Câu tìm kiếm:"."""
    line = next((l.strip() for l in text.splitlines() if l.strip()), "")
    line = line.strip(_QUOTES)
    return _PREFIX.sub("", line).strip(_QUOTES)


async def rewrite(user_content: str, history: list[dict], request_id: str | None = None) -> str:
    """Câu tìm kiếm đã viết lại; trả đúng `user_content` khi LLM lỗi, quá giờ hoặc kết quả không dùng được
    (rỗng, ngắn hơn MIN_REWRITE_CHARS, dài hơn MAX_REWRITE_CHARS). history: [{"role", "content"}] cũ -> mới."""
    settings = get_settings()
    recent = [
        {"role": m["role"], "content": (m.get("content") or "")[:HISTORY_CHARS]} for m in history[-HISTORY_MESSAGES:]
    ]
    started = time.perf_counter()
    # Model phụ riêng (chỉ OpenRouter); rỗng thì không truyền, dùng OPENROUTER_MODEL như cũ
    aux_model = {"model": settings.openrouter_aux_model.strip()} if settings.openrouter_aux_model.strip() else {}
    try:
        text, _tokens, _model = await llm_client.generate(
            prompts.REWRITE_PROMPT, recent, user_content,
            max_output_tokens=MAX_OUTPUT_TOKENS, timeout_seconds=settings.query_rewrite_timeout_seconds, max_retries=0,
            reasoning_max_tokens=settings.llm_aux_reasoning_max_tokens, **aux_model,
        )
    except (llm_client.LLMError, asyncio.TimeoutError) as e:
        logger.warning("agent viết lại câu hỏi lỗi, dùng câu gốc request_id=%s: %s", request_id, e)
        return user_content
    rewritten = _clean(text)
    if not MIN_REWRITE_CHARS <= len(rewritten) <= MAX_REWRITE_CHARS:
        logger.warning(
            "agent viết lại câu hỏi trả kết quả không dùng được, dùng câu gốc request_id=%s: %r", request_id, text[:200],
        )
        return user_content
    logger.info(
        "agent viết lại câu hỏi request_id=%s gốc=%r mới=%r llm=%.2fs",
        request_id, user_content[:LOG_CHARS], rewritten[:LOG_CHARS], time.perf_counter() - started,
    )
    return rewritten
