# Bộ điều hướng: chọn nhánh xử lý cho tin nhắn, không gọi LLM ở đây.
# Câu vô nghĩa trả sớm; có tài liệu đính kèm -> review_agent; còn lại -> qa_agent.
# Lưu DB do endpoint đảm nhận (khi có models/repositories).
import logging

from app.services import qa_agent, query_filter, responder, review_agent

logger = logging.getLogger(__name__)


def _meaningless_result(request_id: str | None) -> dict:
    # Câu gõ bừa: trả lời mặc định, không tốn lượt gọi RAG và AI
    logger.info("agent bỏ qua câu vô nghĩa request_id=%s", request_id)
    return {
        "content": query_filter.DEFAULT_ANSWER,
        "metadata": {"rag_chunk_ids": [], "model": None, "tokens_used": None},
    }


async def prepare(user_content: str, history: list[dict], request_id: str | None = None) -> dict | responder.LLMCall:
    """Nhánh không có tài liệu, dừng trước bước gọi LLM để endpoint stream tự phát chữ. Trả về dict {"content",
    "metadata"} khi xong luôn (câu vô nghĩa, ngoài phạm vi, LLM_BYPASS) hoặc responder.LLMCall khi cần gọi LLM.
    Ném rag_client.RAGError khi rag lỗi."""
    if query_filter.is_meaningless(user_content):
        return _meaningless_result(request_id)
    return await qa_agent.prepare(user_content, history, request_id=request_id)


async def process_message(
    user_content: str,
    history: list[dict],
    request_id: str | None = None,
    documents: list[dict] | None = None,
) -> dict:
    """
    history: [{"role": "user" | "assistant", "content": "..."}] (cũ → mới)
    documents: chữ đã trích của tài liệu người dùng đính kèm (document_client.get_extractions)
    Ném rag_client.RAGError hoặc llm_client.LLMError khi dịch vụ ngoài lỗi;
    endpoint đổi thành 503 RAG_SERVICE_ERROR / AI_SERVICE_ERROR theo openapi.yaml.
    """
    # Có tài liệu thì câu ngắn như "check" vẫn là yêu cầu rà soát, không phải gõ bừa
    if not documents and query_filter.is_meaningless(user_content):
        return _meaningless_result(request_id)

    if documents:
        return await review_agent.review(user_content, history, documents, request_id=request_id)
    return await qa_agent.answer(user_content, history, request_id=request_id)
