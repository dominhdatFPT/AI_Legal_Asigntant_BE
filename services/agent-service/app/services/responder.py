"""Bước cuối của một agent: biến prompt + đoạn luật thành kết quả trả về.
Gọi LLM (llm_client.generate); ở chế độ test (LLM_BYPASS) thì trả thẳng các đoạn luật rag tìm được."""
import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.services import case_budget, llm_client, prompts
from app.services.case_budget import CaseBudget, BudgetExhaustedError

logger = logging.getLogger(__name__)

_HEADING = re.compile(r"(?m)^(?=#{1,3}\s)")  # tách tại đầu mỗi tiêu đề Markdown (mỗi mục = một đơn vị phân tích)
_CITATION = re.compile(r"\[S(\d+)\]")
BYPASS_HEADER ="[CHẾ ĐỘ TEST - KHÔNG QUA LLM] Các đoạn luật rag-service tìm được:"

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
    auxiliary_tokens: int = 0
    deadline: float | None = None
    budget: CaseBudget | None = None
    source_parts: list | None = None
    fallback_reason: str | None = None

    @property
    def no_evidence(self) -> bool:
        """Hồ sơ phức tạp không có trích đoạn nào: mọi mục sẽ bị finalize_complex bác, nên không gọi responder (tránh tốn tiền vô ích)."""
        return self.metadata_extra.get("profile") == "complex" and not self.chunks and not self.fallback_reason

    async def run(self) -> dict:
        # Không còn "bản tóm tắt mẫu" thay cho câu trả lời: lỗi nhà cung cấp/hết giờ/hết ngân sách là LỖI (nơi gọi đổi thành 503)
        if self.fallback_reason:
            raise BudgetExhaustedError(f"Không đủ ngân sách để trả lời ({self.fallback_reason})")
        remaining = self.remaining_seconds()
        if remaining == 0:
            raise llm_client.LLMError("Đã hết thời gian xử lý tình huống")
        if self.no_evidence:
            return self.finalize_complex("", 0, None, False, "no_evidence")
        try:
            async with asyncio.timeout(remaining):
                completion = await llm_client.generate(
                    self.system_prompt, self.history, self.user_content, **self.llm_options
                )
        except TimeoutError as exc:
            raise llm_client.LLMError("Đã hết thời gian xử lý tình huống") from exc
        answer, tokens, model_name = completion
        return self.finalize_complex(answer, tokens, model_name, getattr(completion, "truncated", False),
                                     getattr(completion, "finish_reason", None))

    def finalize_complex(self, answer, tokens, model_name, truncated=False, finish_reason=None):
        """Dùng chung cho POST và SSE. Hồ sơ phức tạp bị cắt (`length`) KHÔNG bị thay bằng mẫu: giữ các mục trọn vẹn có căn cứ."""
        if self.metadata_extra.get("profile") != "complex":
            return self.result(answer, tokens, model_name, truncated, finish_reason)
        if truncated or finish_reason == "length":
            return self._partial_grounded(answer, tokens, model_name, finish_reason or "length")
        if not answer.strip():
            return self._insufficient(tokens, model_name, finish_reason or "empty")
        return self.result(answer, tokens, model_name, truncated, finish_reason)

    def _grounded(self, unit: str) -> bool:
        """Mục có ít nhất một mã căn cứ và mọi mã đều trỏ tới một trích đoạn thật đã gửi cho model."""
        numbers = {int(n) for n in _CITATION.findall(unit)}
        return bool(numbers) and all(1 <= n <= len(self.chunks) for n in numbers)

    def _partial_grounded(self, answer: str, tokens, model: str | None, finish_reason: str) -> dict:
        units = [u.strip() for u in _HEADING.split(answer) if u.strip()]
        if len(units) < 2:  # không có tiêu đề mục: coi mỗi đoạn văn là một đơn vị
            units = [u.strip() for u in re.split(r"\n\s*\n", answer) if u.strip()]
        kept = [u for u in units[:-1] if self._grounded(u)]  # đơn vị cuối là phần bị cắt dở, kể cả kết luận phụ thuộc điều kiện của nó
        if not kept:
            return self._insufficient(tokens, model, finish_reason, truncated=True)
        cited = {int(n) for u in kept for n in _CITATION.findall(u)}
        covered = {iid for n in cited for iid in (self.chunks[n - 1].get("confirmed_issue_ids") or self.chunks[n - 1].get("matched_issue_ids", []))}
        missing = [f"{i.get('issue_id')}: {str(i.get('question', ''))[:120]}" for i in self.metadata_extra.get("issues", [])
                   if i.get("issue_id") not in covered]
        missing += [f"{iid}/{term}" for iid, terms in (self.metadata_extra.get("retrieval_missing_facets") or {}).items() for term in terms]
        content = "\n\n".join(kept) + "\n\n> **Lưu ý:** bài phân tích bị giới hạn độ dài nên các phần còn lại chưa được trình bày đầy đủ; bạn có thể hỏi tiếp riêng từng phần."
        result = self.result(content, tokens, model, True, finish_reason)
        result["metadata"].update(answer_mode="partial_grounded", truncated=True, finish_reason=finish_reason, missing_facets=missing)
        return result

    def _insufficient(self, tokens, model: str | None, finish_reason: str | None, truncated: bool = False) -> dict:
        """Trạng thái rõ ràng khi không có đơn vị dùng được: KHÔNG phải một bài tư vấn pháp lý, không chứa nhận định pháp luật nào."""
        content = ("Mình chưa hoàn tất được phần phân tích có căn cứ cho câu hỏi này trong một lượt. "
                   "Bạn có thể tách thành từng vấn đề (ví dụ hiệu lực và phạt cọc trước, rồi lãi, bồi thường và thời hiệu) "
                   "hoặc hỏi luật sư để được tư vấn chính xác.")
        result = self.result(content, tokens, model, truncated, finish_reason)
        missing = [f"{i.get('issue_id')}: {str(i.get('question', ''))[:120]}" for i in self.metadata_extra.get("issues", [])]
        result["metadata"].update(answer_mode="insufficient_evidence", truncated=truncated, finish_reason=finish_reason,
                                  missing_facets=missing)
        return result

    def remaining_seconds(self) -> float | None:
        return max(0.0, self.deadline - time.monotonic()) if self.deadline is not None else None

    def result(self, answer: str, tokens: int | None, model_name: str | None,
               truncated: bool = False, finish_reason: str | None = None) -> dict:
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
        result = {
            "content": answer,
            "metadata": {
                "rag_chunk_ids": [c["id"] for c in self.chunks if c.get("id")],
                "model": model_name,
                "tokens_used": (tokens or 0) + self.auxiliary_tokens if self.auxiliary_tokens else tokens,
                **self.metadata_extra,
            },
        }
        if self.budget is not None and self.budget.enabled:
            result["metadata"]["budget_summary"] = self.budget.summary()
            if self.budget.attempts and self.budget.confirmed_tokens > 0:
                result["metadata"]["tokens_used"] = self.budget.confirmed_tokens
        if truncated or finish_reason:
            result["metadata"].update(truncated=truncated, finish_reason=finish_reason)
        return result


def fit_chunks(budget: CaseBudget, build_prompt, chunks: list[dict], history: list[dict], user_content: str, desired_out: int) -> list[dict]:
    """Bớt đoạn luật CUỐI cho tới khi prompt (cận trên) + output tối thiểu đã giữ vừa sổ cái $0.01. Không còn đoạn nào mà vẫn
    không vừa thì giữ nguyên: lần gọi LLM sẽ bị sổ cái từ chối (BudgetExhaustedError) chứ không bao giờ vượt trần."""
    s = get_settings()
    provider = s.llm_provider.split(",")[0].strip().lower() or "openrouter"
    model = case_budget.resolve_effective_models(s)[0]
    chunks = list(chunks)
    while chunks:
        upper = case_budget.estimate_chat_input_upper_bound(build_prompt(chunks), history, user_content, budget.strict_byte_upper_bound)
        if budget.available_responder_output_tokens(provider, model, upper, desired_out, min_output=budget.responder_min_output or 128,
                                                    reasoning_tokens=s.openrouter_reasoning_max_tokens):
            break
        chunks.pop()
    return chunks


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
    budget: CaseBudget | None = None,
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
        llm_options, metadata_extra, budget=budget,
    ).run()
