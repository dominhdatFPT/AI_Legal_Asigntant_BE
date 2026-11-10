"""Tóm tắt một hội thoại cho luật sư (API nội bộ của consultation-service). Không lưu bản tóm tắt, không log nội dung tin nhắn."""
import asyncio

from app.services import llm_client, prompts

# Giới hạn đầu vào: hội thoại dài thì bỏ tin CŨ NHẤT, giữ phần gần mốc `until`
MAX_MESSAGES = 60  # số tin lấy từ DB (gần `until` nhất)
MAX_MESSAGE_CHARS = 3000  # mỗi tin cắt còn chừng này ký tự (câu trả lời rà soát tài liệu có thể dài hơn nhiều)
MAX_TRANSCRIPT_CHARS = 24000  # tổng ký tự đưa vào prompt (~8 nghìn token tiếng Việt), thấp hơn xa giới hạn của các model đang dùng

# Consultation chờ tối đa 60 giây. `generate` thử lần lượt từng provider, mỗi provider chờ PROVIDER_TIMEOUT_SECONDS và không
# thử lại cùng provider; TOTAL_TIMEOUT_SECONDS chặn tổng để nhiều provider cộng lại cũng không vượt 60 giây.
PROVIDER_TIMEOUT_SECONDS = 20.0
TOTAL_TIMEOUT_SECONDS = 50.0
MAX_OUTPUT_TOKENS = 1200  # ~2000 ký tự tiếng Việt, chừa dư

ROLE_LABELS = {"user": "Người dùng", "assistant": "Trợ lý AI"}


def build_transcript(messages: list[tuple[str, str]]) -> tuple[str, int]:
    """messages: (role, content) cũ → mới. Trả về (đoạn văn đưa cho LLM, số tin đã dùng).
    Bỏ tin cũ nhất cho vừa MAX_TRANSCRIPT_CHARS, luôn giữ ít nhất tin mới nhất."""
    blocks: list[str] = []
    total = 0
    for role, content in reversed(messages):
        text = content.strip()
        if not text:
            continue
        if len(text) > MAX_MESSAGE_CHARS:
            text = text[:MAX_MESSAGE_CHARS].rstrip() + " " + prompts.TRUNCATED_MARK
        block = f"{ROLE_LABELS.get(role, role)}: {text}"
        if blocks and total + len(block) > MAX_TRANSCRIPT_CHARS:
            break
        blocks.append(block)
        total += len(block) + 2
    return "\n\n".join(reversed(blocks)), len(blocks)


async def summarize(transcript: str) -> tuple[str, str | None]:
    """Nhờ LLM tóm tắt. Trả về (bản tóm tắt, model). Lỗi hoặc quá giờ thì ném llm_client.LLMError."""
    try:
        text, _tokens, model = await asyncio.wait_for(
            llm_client.generate(
                prompts.SUMMARY_SYSTEM_PROMPT, [], transcript,
                max_output_tokens=MAX_OUTPUT_TOKENS, timeout_seconds=PROVIDER_TIMEOUT_SECONDS, max_retries=0,
            ),
            timeout=TOTAL_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as e:
        raise llm_client.LLMError(f"Tóm tắt quá {TOTAL_TIMEOUT_SECONDS:.0f} giây") from e
    summary = text.strip()
    if not summary:
        raise llm_client.LLMError("LLM trả về bản tóm tắt rỗng")
    return summary, model
