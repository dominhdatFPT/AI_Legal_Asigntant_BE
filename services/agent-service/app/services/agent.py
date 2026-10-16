# Business logic: nhận message → gọi RAG search → gọi Gemini → trả câu trả lời
# Lưu DB do endpoint đảm nhận (khi có models/repositories).
from app.services import llm_client, rag_client

SYSTEM_PROMPT = """Bạn là trợ lý pháp lý cho người dân Việt Nam.

Quy tắc:
- Chỉ trả lời dựa trên các trích đoạn văn bản pháp luật bên dưới.
- Mỗi ý quan trọng phải ghi rõ căn cứ: điều, khoản, tên và số hiệu văn bản.
- Nếu trích đoạn không đủ để trả lời, nói rõ là chưa đủ căn cứ và gợi ý người dùng hỏi luật sư. Không tự bịa quy định.
- Trả lời bằng tiếng Việt, ngắn gọn, dễ hiểu.

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
    chunks = await rag_client.search(user_content, request_id=request_id)
    answer, tokens, model_name = await llm_client.generate(
        build_system_prompt(chunks), history, user_content,
    )
    return {
        "content": answer,
        "metadata": {
            "rag_chunk_ids": [c["id"] for c in chunks if c.get("id")],
            "model": model_name,
            "tokens_used": tokens,
        },
    }
