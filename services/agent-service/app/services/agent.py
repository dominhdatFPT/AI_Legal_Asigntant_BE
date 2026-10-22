# Business logic: nhận message → gọi RAG search → gọi Gemini → trả câu trả lời
# Lưu DB do endpoint đảm nhận (khi có models/repositories).
import logging
import time

from app.core.config import get_settings
from app.services import llm_client, query_filter, rag_client

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Bạn là trợ lý pháp lý cho người dân Việt Nam. Trả lời bằng tiếng Việt, đi thẳng vào trọng tâm.

Cách trả lời:
1. Câu đầu tiên trả lời thẳng câu hỏi (được hay không được, cần gì, mất bao lâu, bao nhiêu tiền...).
2. Sau đó tối đa 3 đến 5 gạch đầu dòng, mỗi dòng một ý ngắn, căn cứ ghi gọn trong ngoặc ở cuối dòng, ví dụ: (Khoản 1 Điều 188 Luật Đất đai 2024).
3. Tổng dưới 200 chữ. Không nhắc lại câu hỏi, không mở bài, không kết bài, không chào hỏi.
4. Chỉ dùng thông tin trong các trích đoạn bên dưới; bỏ qua trích đoạn không liên quan. Không tự bịa quy định.
5. Nếu trích đoạn không đủ căn cứ, nói ngắn gọn là chưa đủ căn cứ và gợi ý hỏi luật sư.
6. Nếu câu hỏi thiếu thông tin để trả lời chính xác, hỏi lại đúng 1 câu thay vì liệt kê mọi trường hợp.

Trích đoạn pháp luật:
{context}"""

NO_CONTEXT = "(Không tìm thấy trích đoạn pháp luật nào liên quan đến câu hỏi.)"


def format_chunk(index: int, chunk: dict) -> str:
    """Ví dụ: [1] Khoản 1 Điều 188, Luật Đất đai 2024 (31/2024/QH15)"""
    section = chunk.get("section_info") or {}
    document = chunk.get("document") or {}

    parts = []
    if section.get("diem"):
        parts.append(f"Điểm {section['diem']}")
    if section.get("khoan"):
        parts.append(f"Khoản {section['khoan']}")
    if section.get("dieu"):
        parts.append(f"Điều {section['dieu']}")
    citation = " ".join(parts)

    title = document.get("title") or "Văn bản không rõ tên"
    if document.get("doc_number"):
        title += f" ({document['doc_number']})"
    header = f"[{index}] {citation}, {title}" if citation else f"[{index}] {title}"

    return f"{header}\n{chunk.get('content', '').strip()}"


def build_system_prompt(chunks: list[dict]) -> str:
    context = "\n\n".join(format_chunk(i, c) for i, c in enumerate(chunks, start=1))
    return SYSTEM_PROMPT.format(context=context or NO_CONTEXT)


async def process_message(
    user_content: str,
    history: list[dict],
    request_id: str | None = None,
) -> dict:
    """
    history: [{"role": "user" | "assistant", "content": "..."}] (cũ → mới)
    Ném rag_client.RAGError hoặc llm_client.LLMError khi dịch vụ ngoài lỗi;
    endpoint đổi thành 503 RAG_SERVICE_ERROR / AI_SERVICE_ERROR theo openapi.yaml.
    """
    if query_filter.is_meaningless(user_content):
        # Câu gõ bừa: trả lời mặc định, không tốn lượt gọi RAG và AI
        logger.info("agent bỏ qua câu vô nghĩa request_id=%s", request_id)
        return {
            "content": query_filter.DEFAULT_ANSWER,
            "metadata": {"rag_chunk_ids": [], "model": None, "tokens_used": None},
        }

    started = time.perf_counter()
    chunks = await rag_client.search(user_content, top_k=get_settings().rag_top_k, request_id=request_id)
    rag_done = time.perf_counter()
    answer, tokens, model_name = await llm_client.generate(
        build_system_prompt(chunks), history, user_content,
    )
    llm_done = time.perf_counter()
    # Đo thời gian từng bước để biết chậm ở RAG (embedding + tìm kiếm) hay ở LLM
    logger.info(
        "agent timing request_id=%s rag=%.2fs llm=%.2fs total=%.2fs model=%s tokens=%s chunks=%d history=%d",
        request_id, rag_done - started, llm_done - rag_done, llm_done - started,
        model_name, tokens, len(chunks), len(history),
    )
    return {
        "content": answer,
        "metadata": {
            "rag_chunk_ids": [c["id"] for c in chunks if c.get("id")],
            "model": model_name,
            "tokens_used": tokens,
        },
    }
