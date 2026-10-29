"""Agent hỏi đáp pháp luật: tìm đoạn luật theo câu hỏi rồi nhờ LLM trả lời dựa trên các đoạn đó."""
import logging
import time

from app.core.config import get_settings
from app.services import prompts, rag_client, relevance, responder

logger = logging.getLogger(__name__)


async def prepare(user_content: str, history: list[dict], request_id: str | None = None) -> dict | responder.LLMCall:
    """Bước chuẩn bị: rag rồi (nếu bật) chấm độ liên quan. Trả về:
    - dict {"content", "metadata"}: xong luôn, không cần gọi LLM trả lời (ngoài phạm vi, LLM_BYPASS);
    - responder.LLMCall: cần gọi LLM; `await call.run()` lấy cả câu, hoặc stream bằng llm_client.generate_stream.
    Ném rag_client.RAGError khi rag lỗi (nơi gọi đổi thành 503)."""
    settings = get_settings()
    started = time.perf_counter()
    chunks = await rag_client.search(user_content, top_k=settings.rag_top_k, request_id=request_id)
    rag_done = time.perf_counter()

    graded_done = None
    # LLM_BYPASS là chế độ soi đoạn luật thô, không có lượt gọi LLM nào kể cả để chấm
    if settings.relevance_check and not settings.llm_bypass:
        info: dict = {}
        relevant = await relevance.grade(user_content, chunks, request_id, info=info)
        graded_done = time.perf_counter()
        if relevant is not None and not relevant:
            logger.info(
                "agent timing request_id=%s rag=%.2fs relevance=%.2fs total=%.2fs model=%s tokens=%s chunks=%d out_of_scope=1",
                request_id, rag_done - started, graded_done - rag_done, graded_done - started,
                info.get("model"), info.get("tokens"), len(chunks),
            )
            return {
                "content": prompts.OUT_OF_SCOPE_ANSWER,
                "metadata": {
                    "rag_chunk_ids": [], "model": info.get("model"), "tokens_used": info.get("tokens"),
                    "type": "out_of_scope",
                },
            }
        if relevant:  # None (không chấm được) thì giữ nguyên các đoạn
            chunks = relevant

    system_prompt = prompts.build_qa_prompt(chunks)
    if settings.llm_bypass:
        return await responder.respond(
            system_prompt, chunks, history, user_content,
            queries=[user_content], request_id=request_id, started=started, rag_done=rag_done,
        )
    return responder.LLMCall(
        system_prompt, chunks, history, user_content, request_id, started, rag_done, graded_done,
    )


async def answer(user_content: str, history: list[dict], request_id: str | None = None) -> dict:
    """Ném rag_client.RAGError hoặc llm_client.LLMError khi dịch vụ ngoài lỗi (nơi gọi đổi thành 503)."""
    prepared = await prepare(user_content, history, request_id)
    if isinstance(prepared, dict):
        return prepared
    return await prepared.run()
