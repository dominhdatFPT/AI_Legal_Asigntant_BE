"""Chấm độ liên quan: nhờ LLM chọn trong các đoạn luật rag tìm được những đoạn thật sự dùng trả lời câu hỏi.
rag-service luôn trả đủ top_k đoạn kể cả khi câu hỏi nằm ngoài kho luật và điểm số không phân biệt được, nên không dùng ngưỡng điểm."""
import json
import logging

from app.core.config import get_settings
from app.services import llm_client, prompts

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 100  # chỉ cần một dòng JSON


def _parse(text: str, total: int) -> list[int] | None:
    """Số thứ tự (1..total, không trùng, xếp tăng dần) trong {"relevant": [...]}; None nếu không đúng dạng.
    Cắt từ "{" đầu đến "}" cuối nên chịu được JSON bọc trong khối ```json hoặc có chữ dẫn trước/sau."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    numbers = data.get("relevant") if isinstance(data, dict) else None
    if not isinstance(numbers, list):
        return None
    # bool là con của int trong Python: loại ra, "true" không phải số thứ tự
    if not all(isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= total for n in numbers):
        return None
    return sorted(set(numbers))


async def grade(
    user_content: str,
    chunks: list[dict],
    request_id: str | None = None,
    info: dict | None = None,
) -> list[dict] | None:
    """Trả về các đoạn liên quan theo thứ tự ban đầu (có thể rỗng = không đoạn nào dùng được).
    None = không chấm được, nơi gọi cứ dùng nguyên các đoạn: LLM lỗi hoặc quá giờ, JSON hỏng, số thứ tự ngoài phạm vi.
    `info` (nếu truyền) được điền {"model", "tokens"} của lượt chấm khi LLM trả lời được."""
    if not chunks:
        return []
    settings = get_settings()
    try:
        text, tokens, model_name = await llm_client.generate(
            prompts.build_relevance_prompt(chunks), [], user_content,
            max_output_tokens=MAX_OUTPUT_TOKENS, timeout_seconds=settings.relevance_timeout_seconds, max_retries=0,
        )
    except llm_client.LLMError as e:
        logger.warning("agent chấm liên quan lỗi, giữ nguyên các đoạn luật request_id=%s: %s", request_id, e)
        return None
    if info is not None:
        info.update(model=model_name, tokens=tokens)
    numbers = _parse(text, len(chunks))
    if numbers is None:
        logger.warning(
            "agent chấm liên quan trả kết quả không hợp lệ, giữ nguyên các đoạn luật request_id=%s: %r",
            request_id, text[:200],
        )
        return None
    return [chunks[n - 1] for n in numbers]
