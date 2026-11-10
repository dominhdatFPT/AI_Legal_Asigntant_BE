"""Agent hỏi đáp pháp luật: tìm đoạn luật theo câu hỏi rồi nhờ LLM trả lời dựa trên các đoạn đó."""
import asyncio
import logging
import time

from app.core.config import get_settings
from app.services import cache, contract_review, prompts, query_rewrite, rag_client, relevance, responder

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 20  # rag-service từ chối top_k lớn hơn 20


async def _search_with_rewrite(
    user_content: str, history: list[dict], request_id: str | None, top_k: int,
) -> list[dict]:
    """Tìm câu gốc (chạy nền, song song với việc viết lại) rồi nếu câu viết lại khác thì tìm thêm và gộp xen kẽ, câu gốc đứng
    trước. Không thay hẳn câu gốc vì câu viết lại có thể sai chi tiết. Chỉ lỗi tìm câu gốc (RAGError) mới ném ra ngoài; viết lại
    hoặc tìm câu viết lại lỗi thì ghi warning và dùng riêng kết quả câu gốc."""
    original = asyncio.create_task(rag_client.search(user_content, top_k=top_k, request_id=request_id))
    try:
        try:
            rewritten = await query_rewrite.rewrite(user_content, history, request_id)
        except Exception as e:
            logger.warning("agent viết lại câu hỏi lỗi bất ngờ, dùng câu gốc request_id=%s: %s", request_id, e)
            return await original
        if " ".join(rewritten.split()).lower() == " ".join(user_content.split()).lower():
            return await original
        try:
            extra = await rag_client.search(rewritten, top_k=top_k, request_id=request_id)
        except Exception as e:  # RAGError hoặc lỗi bất ngờ: bước thêm này không được làm hỏng câu trả lời
            logger.warning("agent tìm theo câu viết lại lỗi, dùng câu gốc request_id=%s: %s", request_id, e)
            return await original
        original_chunks = await original
        try:
            merged = contract_review.merge_chunks([original_chunks, extra], limit=top_k)
        except Exception as e:
            logger.warning("agent gộp kết quả tìm lỗi, dùng câu gốc request_id=%s: %s", request_id, e)
            return original_chunks
        logger.info(
            "agent tìm theo câu gốc và câu viết lại request_id=%s gốc=%d viết_lại=%d sau_gộp=%d",
            request_id, len(original_chunks), len(extra), len(merged),
        )
        return merged
    finally:
        # Tác vụ nền luôn được chờ hoặc huỷ gọn: không chạy mồ côi, không để "exception never retrieved"
        if not original.done():
            original.cancel()
        await asyncio.gather(original, return_exceptions=True)


async def prepare(user_content: str, history: list[dict], request_id: str | None = None) -> dict | responder.LLMCall:
    """Bước chuẩn bị: rag rồi (nếu bật) chấm độ liên quan. Trả về:
    - dict {"content", "metadata"}: xong luôn, không cần gọi LLM trả lời (ngoài phạm vi, LLM_BYPASS, trúng cache câu trả lời);
    - responder.LLMCall: cần gọi LLM; `await call.run()` lấy cả câu, hoặc stream bằng llm_client.generate_stream.
    Ném rag_client.RAGError khi rag lỗi (nơi gọi đổi thành 503)."""
    settings = get_settings()
    started = time.perf_counter()
    # Cache câu trả lời: chỉ câu đầu hội thoại (lịch sử rỗng), LLM_BYPASS tắt. Lỗi cache thì ghi warning rồi chạy như không có cache
    cache_key = None
    if settings.answer_cache_enabled and not history and not settings.llm_bypass:
        try:
            normalized = cache.normalize(user_content)
            if normalized:
                cache_key = (normalized, settings.llm_provider, settings.openrouter_model)
                hit = responder.get_cached_answer(cache_key)
                if hit is not None:
                    logger.info(
                        "agent timing request_id=%s cache=hit total=%.2fs", request_id, time.perf_counter() - started,
                    )
                    # Lần này không tốn token; model và rag_chunk_ids giữ như lúc lưu
                    return {**hit, "metadata": {**hit["metadata"], "cache_hit": True, "tokens_used": 0}}
        except Exception:
            logger.warning("answer cache lỗi khi đọc, chạy như không có cache request_id=%s", request_id, exc_info=True)
    # Tìm 2 bước: lấy rộng rag_candidate_k đoạn làm ứng viên rồi để lượt chấm chọn lại (không thêm lượt gọi AI)
    candidate_k = min(settings.rag_candidate_k, MAX_CANDIDATES)
    two_step = candidate_k > settings.rag_top_k and settings.relevance_check and not settings.llm_bypass
    search_k = candidate_k if two_step else settings.rag_top_k
    # LLM_BYPASS không có lượt gọi LLM nào, kể cả để viết lại câu hỏi
    if settings.query_rewrite_enabled and not settings.llm_bypass:
        chunks = await _search_with_rewrite(user_content, history, request_id, search_k)
    else:
        chunks = await rag_client.search(user_content, top_k=search_k, request_id=request_id)
    rag_done = time.perf_counter()

    graded_done = None
    # LLM_BYPASS là chế độ soi đoạn luật thô, không có lượt gọi LLM nào kể cả để chấm
    if settings.relevance_check and not settings.llm_bypass:
        info: dict = {}
        relevant = await relevance.grade(user_content, chunks, request_id, info=info)
        graded_done = time.perf_counter()
        if relevant is not None and not relevant:
            if two_step:
                logger.info(
                    "agent tìm 2 bước request_id=%s ứng_viên=%d giữ=0 trường_hợp=b (không đoạn nào dùng được)",
                    request_id, len(chunks),
                )
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
            candidates = len(chunks)
            chunks = relevant[:max(settings.rag_select_max, 1)] if two_step else relevant
            if two_step:
                logger.info(
                    "agent tìm 2 bước request_id=%s ứng_viên=%d giữ=%d trường_hợp=a (chấm chọn)",
                    request_id, candidates, len(chunks),
                )
        elif two_step:  # không chấm được: như trước khi có tìm 2 bước, chỉ lấy rag_top_k đoạn đầu của ứng viên
            candidates = len(chunks)
            chunks = chunks[:settings.rag_top_k]
            logger.info(
                "agent tìm 2 bước request_id=%s ứng_viên=%d giữ=%d trường_hợp=c (không chấm được)",
                request_id, candidates, len(chunks),
            )

    system_prompt = prompts.build_qa_prompt(chunks)
    if settings.llm_bypass:
        return await responder.respond(
            system_prompt, chunks, history, user_content,
            queries=[user_content], request_id=request_id, started=started, rag_done=rag_done,
        )
    return responder.LLMCall(
        system_prompt, chunks, history, user_content, request_id, started, rag_done, graded_done,
        cache_key=cache_key,
    )


async def answer(user_content: str, history: list[dict], request_id: str | None = None) -> dict:
    """Ném rag_client.RAGError hoặc llm_client.LLMError khi dịch vụ ngoài lỗi (nơi gọi đổi thành 503)."""
    prepared = await prepare(user_content, history, request_id)
    if isinstance(prepared, dict):
        return prepared
    return await prepared.run()
