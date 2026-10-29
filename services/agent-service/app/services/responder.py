"""Bước cuối của một agent: biến prompt + đoạn luật thành kết quả trả về.
Gọi LLM (llm_client.generate); ở chế độ test (LLM_BYPASS) thì trả thẳng các đoạn luật rag tìm được."""
import logging
import time
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.services import llm_client, prompts

logger = logging.getLogger(__name__)

BYPASS_HEADER = "[CHẾ ĐỘ TEST - KHÔNG QUA LLM] Các đoạn luật rag-service tìm được:"


@dataclass
class LLMCall:
    """Việc chuẩn bị đã xong (đã có prompt và đoạn luật), chỉ còn gọi LLM. Bước chuẩn bị trả về LLMCall thay vì
    câu trả lời khi cần LLM, để nơi gọi chọn gọi một lần (`run`) hoặc stream rồi ghép kết quả bằng `result`.
    started / rag_done / graded_done là mốc time.perf_counter() để đo thời gian từng bước."""

    system_prompt: str
    chunks: list[dict]
    history: list[dict]
    user_content: str
    request_id: str | None
    started: float
    rag_done: float
    graded_done: float | None = None  # chỉ có khi đã chạy bước chấm độ liên quan
    llm_options: dict = field(default_factory=dict)
    metadata_extra: dict = field(default_factory=dict)

    async def run(self) -> dict:
        answer, tokens, model_name = await llm_client.generate(
            self.system_prompt, self.history, self.user_content, **self.llm_options
        )
        return self.result(answer, tokens, model_name)

    def result(self, answer: str, tokens: int | None, model_name: str | None) -> dict:
        """Ghép {"content", "metadata"} và ghi log thời gian; gọi ngay khi LLM trả lời xong (hoặc stream xong)."""
        llm_done = time.perf_counter()
        # Đo thời gian từng bước để biết chậm ở RAG (embedding + tìm kiếm) hay ở LLM
        if self.graded_done is None:
            logger.info(
                "agent timing request_id=%s rag=%.2fs llm=%.2fs total=%.2fs model=%s tokens=%s chunks=%d history=%d",
                self.request_id, self.rag_done - self.started, llm_done - self.rag_done, llm_done - self.started,
                model_name, tokens, len(self.chunks), len(self.history),
            )
        else:
            logger.info(
                "agent timing request_id=%s rag=%.2fs relevance=%.2fs llm=%.2fs total=%.2fs model=%s tokens=%s chunks=%d history=%d",
                self.request_id, self.rag_done - self.started, self.graded_done - self.rag_done,
                llm_done - self.graded_done, llm_done - self.started,
                model_name, tokens, len(self.chunks), len(self.history),
            )
        return {
            "content": answer,
            "metadata": {
                "rag_chunk_ids": [c["id"] for c in self.chunks if c.get("id")],
                "model": model_name,
                "tokens_used": tokens,
                **self.metadata_extra,
            },
        }


async def respond(
    system_prompt: str,
    chunks: list[dict],
    history: list[dict],
    user_content: str,
    *,
    queries: list[str],
    request_id: str | None,
    started: float,
    rag_done: float,
    llm_options: dict | None = None,
    metadata_extra: dict | None = None,
    graded_done: float | None = None,
) -> dict:
    """Trả về {"content", "metadata"}. started / rag_done / graded_done là mốc time.perf_counter() để đo thời gian
    từng bước (graded_done chỉ có khi đã chạy bước chấm độ liên quan)."""
    llm_options = llm_options or {}
    metadata_extra = metadata_extra or {}
    if get_settings().llm_bypass:
        # Chỉ để test: trả thẳng các đoạn luật rag tìm được, không gọi LLM nên không có chữ nào do AI thêm vào
        logger.warning(
            "agent bypass LLM request_id=%s chunks=%d rag=%.2fs", request_id, len(chunks), rag_done - started,
        )
        body = "\n\n".join(prompts.format_chunk(i, c) for i, c in enumerate(chunks, start=1)) or prompts.NO_CONTEXT
        return {
            "content": f"{BYPASS_HEADER}\n\n{body}",
            "metadata": {
                "rag_chunk_ids": [c["id"] for c in chunks if c.get("id")],
                "model": "no-llm",
                "tokens_used": 0,
                "llm_bypass": True,
                "queries": queries,
                "system_prompt": system_prompt,
                **metadata_extra,
            },
        }
    return await LLMCall(
        system_prompt, chunks, history, user_content, request_id, started, rag_done, graded_done,
        llm_options, metadata_extra,
    ).run()
