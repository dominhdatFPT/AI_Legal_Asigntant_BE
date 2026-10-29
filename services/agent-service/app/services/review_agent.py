"""Agent rà soát tài liệu: tìm luật theo NỘI DUNG tài liệu đính kèm rồi nhờ LLM nhận xét theo khung cố định (không hỏi lại)."""
import asyncio
import logging
import time

from app.core.config import get_settings
from app.services import contract_review, prompts, rag_client, responder

logger = logging.getLogger(__name__)


async def search_for_review(queries: list[str], request_id: str | None) -> list[dict]:
    """Tìm song song từng câu, gộp xen kẽ. Một câu lỗi thì bỏ qua; tất cả lỗi mới ném RAGError."""
    top_k = get_settings().rag_top_k
    results = await asyncio.gather(
        *(rag_client.search(q, top_k=top_k, request_id=request_id) for q in queries), return_exceptions=True,
    )
    failed = [r for r in results if isinstance(r, BaseException)]
    for error in failed:
        if not isinstance(error, rag_client.RAGError):
            raise error  # lỗi lập trình, không được nuốt như lỗi mạng
        logger.warning("RAG lỗi một câu tìm khi rà soát request_id=%s: %s", request_id, error)
    if len(failed) == len(results):
        raise failed[0]
    return contract_review.merge_chunks([r for r in results if not isinstance(r, BaseException)])


async def review(
    user_content: str,
    history: list[dict],
    documents: list[dict],
    request_id: str | None = None,
) -> dict:
    """documents: chữ đã trích của tài liệu đính kèm (document_client.get_extractions), không rỗng.
    Ném rag_client.RAGError hoặc llm_client.LLMError khi dịch vụ ngoài lỗi (nơi gọi đổi thành 503)."""
    started = time.perf_counter()
    settings = get_settings()
    contract_type = next(
        (t for t in (contract_review.detect_contract_type(d["text"]) for d in documents) if t != "khac"), "khac",
    )
    queries = contract_review.review_queries(contract_type, user_content, documents[0]["text"])
    # Không tìm được câu nào (tài liệu rỗng, câu hỏi chung chung) thì tìm theo câu hỏi như thường
    queries = queries or [user_content]
    chunks = await search_for_review(queries, request_id)
    # Không thử lại cùng provider: 40s x 2 lần vượt timeout của gateway, người dùng gửi lại và bị trả lời trùng
    llm_options = {
        "max_output_tokens": settings.llm_review_max_output_tokens,
        "timeout_seconds": settings.llm_review_timeout_seconds,
        "max_retries": 0,
    }
    metadata_extra = {"mode": "contract_review", "contract_type": contract_type}
    logger.info(
        "agent rà soát request_id=%s contract_type=%s queries=%d chunks=%d rag=%.2fs",
        request_id, contract_type, len(queries), len(chunks), time.perf_counter() - started,
    )
    rag_done = time.perf_counter()
    return await responder.respond(
        prompts.build_review_prompt(chunks, documents), chunks, history, user_content,
        queries=queries, request_id=request_id, started=started, rag_done=rag_done,
        llm_options=llm_options, metadata_extra=metadata_extra,
    )
