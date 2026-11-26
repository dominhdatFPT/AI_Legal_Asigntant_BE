"""Cổng admission thống nhất: quyết định CỤC BỘ (không RAG, không embedding, không LLM) trước agent.prepare /
agent.process_message / asyncio.gather của chat công khai.

Thứ tự: độ dài (413) -> cấu hình giá/route (503) -> sàn chi phí lạnh và chất lượng (422) -> hạn mức token ngày còn lại (429).
Từ chối ở bất kỳ bước nào thì CHƯA có RAG/embedding/LLM nào được gọi, CHƯA trừ quota, CHƯA lưu cặp tin nhắn.
Công thức tiền (nano-USD, cold cache K = 0): C = 300*I + 1200*O + 150*E; trần toàn câu 10_000_000 (= $0.01).
"""
import json
import logging
import re
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.services import case_budget, prompts, qa_agent, query_filter, query_plan, quota

logger = logging.getLogger(__name__)

IN_NANO, OUT_NANO = 300, 1200
PLANNER_OUTPUT_CEILING = 896   # trần output planner đã tính trong sàn chi phí (Astra audit, mục 2)
RESPONDER_OUTPUT_TARGET = 3500
CHECKLIST_TOKENS_PER_ISSUE, CHECKLIST_BASE_TOKENS = 200, 300
EVIDENCE_TOKENS_PER_CHUNK = 700  # nhánh simple/review: cận trên cho mỗi đoạn luật trong prompt
OUT_MIN = {"followup": 512, "simple": 512, "review": 800}
HISTORY_ALLOWANCE = {"complex": 1500, "simple": 2000, "review": 1500, "followup": 6000}
REWRITE_OUTPUT_TOKENS = 150


class AdmissionError(Exception):
    """Lỗi từ chối trước khi tốn tiền; main.py đổi thành {data, error{code,message}, request_id} với đúng status_code."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code, self.code, self.message = status_code, code, message


@dataclass
class AdmissionDecision:
    branch: str = "none"                # complex | simple | followup | review | none (không tốn tiền)
    budget: case_budget.CaseBudget | None = None
    history: list = field(default_factory=list)  # lịch sử đã chọn cục bộ; agent chỉ được dùng phần này
    issue_groups: int = 0
    planner_input_tokens: int = 0       # P
    responder_fixed_tokens: int = 0     # R_0
    floor_nano: int = 0
    fixed_nano: int = 0
    min_quota_tokens: int = 0
    responder_input_cap: int = 0
    responder_output_cap: int = 0
    request_turn_id: str = ""
    public: bool = False


def _reject_budget(detail: str = "") -> AdmissionError:
    return AdmissionError(422, "QUESTION_EXCEEDS_BUDGET",
                          "Câu hỏi này có nhiều vấn đề cần đối chiếu hơn mức xử lý trong một lượt. Bạn có thể hỏi trước về "
                          "hiệu lực và phạt cọc, rồi gửi phần lãi, bồi thường và thời hiệu ở câu hỏi sau, hoặc rút gọn "
                          "tình tiết. " + detail + "Lượt này chưa trừ hạn mức.")


def estimate_issue_groups(text: str) -> int:
    """Số nhóm yêu cầu pháp lý độc lập (>= 1): tách theo nhãn đánh số hoặc câu hỏi, gộp các ý gần trùng nhau."""
    parts = query_plan.source_parts(text)
    labelled = len(query_plan.NUMBERED.findall(text)) >= 2
    if labelled:
        first = query_plan.NUMBERED.search(text)
        parts = [p for p in parts if text.find(p) >= (first.start() if first else 0)] or parts  # bỏ phần tình tiết chung
        while len(parts) > 1 and "?" not in parts[0] and any("?" in p for p in parts[1:]):
            parts = parts[1:]  # đoạn "Câu 1: tình tiết..." chưa có câu hỏi là tình tiết chung, không phải một nhóm vấn đề
    else:
        parts = [s.strip() for s in re.split(r"(?<=\?)\s+|\n+", text) if "?" in s]
    seen: list[set] = []
    for part in parts:
        words = {w for w in re.findall(r"[^\W_]+", query_plan.NUMBERED.sub("", part).casefold()) if len(w) >= 3}
        if not words or any(len(words & s) / len(words | s) >= 0.7 for s in seen):
            continue
        seen.append(words)
    return max(1, len(seen))


def select_history(history: list[dict], allowance: int, strict: bool = False) -> list[dict]:
    """Giữ các tin MỚI NHẤT còn vừa phần cho phép (cận trên token); thứ tự thời gian được giữ nguyên."""
    kept, used = [], 0
    for message in reversed(history):
        cost = case_budget.estimate_text_tokens(message.get("content", ""), strict) + case_budget.MESSAGE_FRAMING_TOKENS
        if used + cost > allowance:
            break
        kept.append(message)
        used += cost
    return kept[::-1]


def _verify_configuration(settings, branch: str) -> None:
    provider = (settings.llm_provider.split(",")[0].strip().lower() or "openrouter")
    primary, aux = case_budget.resolve_effective_models(settings)
    needs_aux = branch == "complex" or settings.relevance_check
    ok = (case_budget.is_verified_route(provider, primary) and (not needs_aux or case_budget.is_verified_route(provider, aux))
          and settings.embedding_price_nano_per_token > 0)
    if not ok:
        logger.error("[ADMISSION] giá/route chưa xác minh provider=%s model=%s aux=%s", provider, primary, aux)
        raise AdmissionError(503, "AI_BUDGET_CONFIGURATION_UNAVAILABLE",
                             "Hệ thống chưa xác minh được cấu hình giá hoặc giới hạn token của AI cho lượt này. Đây là lỗi vận "
                             "hành, không phải do câu hỏi của bạn; vui lòng thử lại sau. Lượt này chưa trừ hạn mức.")


def _classify(question: str, history: list[dict], documents, settings) -> str:
    if documents:
        return "review"
    if qa_agent._is_history_transform_request(question, history) and not settings.llm_bypass:
        return "followup"
    if settings.adaptive_rag_enabled and query_plan.route(question) == "complex" and not settings.llm_bypass:
        return "complex"
    return "simple"


def _chat_tokens(system_prompt, history, question, strict):
    return case_budget.estimate_chat_input_upper_bound(system_prompt, history, question, strict)


def admit(question: str, history: list[dict] | None = None, *, documents: list[dict] | None = None, public: bool = False,
          subject: str | None = None, username: str | None = None, today: str | None = None,
          limit_override: int | None = None) -> AdmissionDecision:
    """Quyết định admission đồng bộ, thuần cục bộ. Ném AdmissionError (413/503/422) hoặc HTTPException 429 (quota)."""
    settings = get_settings()
    history = list(history or [])
    if len(question) > settings.admission_max_question_chars:
        limit = f"{settings.admission_max_question_chars:,}".replace(",", ".")
        raise AdmissionError(413, "QUESTION_TOO_LONG", f"Câu hỏi dài quá {limit} ký tự. Vui lòng rút gọn hoặc tách thành "
                             "nhiều câu hỏi. Lượt này chưa trừ hạn mức.")
    budget_nano = settings.case_cost_budget_micro_usd * 1000
    if not settings.admission_gate_enabled or budget_nano <= 0:
        return AdmissionDecision(history=history, public=public)  # trần tiền tắt (chỉ môi trường test)
    if not documents and query_filter.is_meaningless(question):
        return AdmissionDecision(history=history, public=public)  # trả lời mặc định, không tốn RAG/LLM
    branch = _classify(question, history, documents, settings)
    if not settings.llm_bypass:
        _verify_configuration(settings, branch)
    strict = False  # shortcut: chế độ byte-strict (cận trên chứng minh được) chỉ bật khi có tokenizer + template xác minh
    emb_cap, emb_price = settings.embedding_total_input_tokens, settings.embedding_price_nano_per_token
    rel_in, rel_out = settings.relevance_total_input_tokens, settings.relevance_total_output_tokens
    planner_out = min(settings.planner_max_output_tokens, PLANNER_OUTPUT_CEILING)
    sel_history = select_history(history, HISTORY_ALLOWANCE[branch], strict)
    m = P = R0 = in_floor = 0
    out_cfg = settings.llm_max_output_tokens
    stage_caps: dict = {}
    llm_tokens_floor = 0
    if branch == "complex":
        m = estimate_issue_groups(question)
        if m > settings.admission_max_issue_groups:
            raise _reject_budget(f"(Phát hiện khoảng {m} nhóm vấn đề, tối đa {settings.admission_max_issue_groups}.) ")
        parts = query_plan.build_source_parts(question, sel_history)
        P = _chat_tokens(query_plan.PLANNER_PROMPT.replace("{limit}", str(settings.case_max_issues)), sel_history,
                         json.dumps([{"id": p.id, "text": p.text} for p in parts], ensure_ascii=False), strict)
        skeleton = prompts.build_complex_prompt([], "", output_target=RESPONDER_OUTPUT_TARGET)
        R0 = _chat_tokens(skeleton, sel_history, question, strict) + CHECKLIST_BASE_TOKENS + CHECKLIST_TOKENS_PER_ISSUE * m
        in_floor = R0 + 1200 + 450 * m                       # R_min = R_0 + D_min
        out_cfg = settings.case_complex_max_output_tokens or settings.case_max_output_tokens
        out_floor = min(1600 + 225 * m, out_cfg)             # O_min, nhưng không đòi hơn cap cấu hình (Phase 2B nâng cap)
        fixed = emb_price * emb_cap + OUT_NANO * planner_out + IN_NANO * rel_in + OUT_NANO * rel_out
        floor = fixed + IN_NANO * (P + in_floor) + OUT_NANO * out_floor
        stage_caps = {"planner": {"output": planner_out}, "relevance": {"input": rel_in, "output": rel_out}}
        llm_tokens_floor = P + in_floor + out_floor + planner_out + rel_in + rel_out
        out_target = min(RESPONDER_OUTPUT_TARGET, out_cfg)
        in_target = max(in_floor, settings.responder_input_target)
    else:
        skeleton = {"followup": prompts.FOLLOWUP_SYSTEM_PROMPT,
                    "simple": prompts.build_qa_prompt([]),
                    "review": prompts.build_review_prompt([], documents or [])}[branch]
        R0 = _chat_tokens(skeleton, sel_history, question, strict)
        evidence = 0 if branch == "followup" else min(max(settings.rag_top_k, 1), 20) * EVIDENCE_TOKENS_PER_CHUNK
        in_floor = R0 + evidence
        out_floor = OUT_MIN[branch]
        out_cfg = {"followup": settings.case_max_output_tokens, "simple": settings.llm_max_output_tokens,
                   "review": settings.llm_review_max_output_tokens}[branch]
        fixed = 0 if branch == "followup" else emb_price * emb_cap
        if branch != "followup" and settings.query_rewrite_enabled and not settings.llm_bypass:
            rewrite_in = _chat_tokens(prompts.REWRITE_PROMPT, sel_history[-4:], question, strict)
            fixed += IN_NANO * rewrite_in + OUT_NANO * REWRITE_OUTPUT_TOKENS
            llm_tokens_floor += rewrite_in + REWRITE_OUTPUT_TOKENS
        if branch != "followup" and settings.relevance_check and not settings.llm_bypass:
            fixed += IN_NANO * rel_in + OUT_NANO * rel_out
            stage_caps["relevance"] = {"input": rel_in, "output": rel_out}
            llm_tokens_floor += rel_in + rel_out
        floor = fixed + IN_NANO * in_floor + OUT_NANO * out_floor
        llm_tokens_floor += in_floor + out_floor
        out_target, in_target = min(out_cfg, RESPONDER_OUTPUT_TARGET), in_floor
        if settings.llm_bypass:  # không có lượt LLM nào: chỉ embedding có thể tốn tiền
            floor, llm_tokens_floor, fixed = emb_price * emb_cap, 0, emb_price * emb_cap
    if floor > budget_nano:
        raise _reject_budget()
    # Cấp đồng thời input chứng cứ và output cuối trong phần tiền còn lại: ưu tiên giảm output về sàn rồi mới giảm input.
    room = budget_nano - fixed - IN_NANO * P
    out_alloc, in_alloc = out_target, in_target
    if IN_NANO * in_alloc + OUT_NANO * out_alloc > room:
        out_alloc = max(out_floor, min(out_alloc, (room - IN_NANO * in_alloc) // OUT_NANO))
        in_alloc = max(in_floor, min(in_alloc, (room - OUT_NANO * out_alloc) // IN_NANO))
    out_alloc = max(out_alloc, 0)
    budget = case_budget.CaseBudget(
        max_cost_nano_usd=budget_nano, branch="public_chat" if public else branch, embedding_cap_tokens=emb_cap,
        embedding_price_nano=emb_price, planner_output_cap=planner_out if branch == "complex" else 0,
        responder_output_cap=out_target, stage_caps=stage_caps,
        responder_input_cap=in_alloc, responder_min_output=out_floor, strict_byte_upper_bound=strict, responder_reserved_output=out_alloc,
        responder_reserved_input=in_alloc)
    decision = AdmissionDecision(
        branch=branch, budget=budget, history=sel_history, issue_groups=m, planner_input_tokens=P, responder_fixed_tokens=R0,
        floor_nano=floor, fixed_nano=fixed, min_quota_tokens=llm_tokens_floor, responder_input_cap=in_alloc,
        responder_output_cap=out_alloc, request_turn_id=budget.request_turn_id, public=public)
    if subject is not None:  # hạn mức token ngày: kiểm tra phần còn lại so với sàn tối thiểu, TÁCH khỏi trần $0.01
        quota.check_daily_quota(subject, target_date=today, username=username, limit_override=limit_override,
                                min_tokens=llm_tokens_floor)
    logger.info("[ADMISSION] ok branch=%s m=%d P=%d R0=%d floor_nano=%d request_turn_id=%s",
                decision.branch, m, P, R0, floor, decision.request_turn_id)
    return decision
