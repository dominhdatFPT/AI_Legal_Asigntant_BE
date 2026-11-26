"""Agent hỏi đáp pháp luật: tìm đoạn luật theo câu hỏi rồi nhờ LLM trả lời dựa trên các đoạn đó."""
import asyncio
import json
import logging
import re
import time

from app.core.config import get_settings
from app.services import case_budget, contract_review, evidence_selection, llm_client, prompts, query_plan, query_rewrite, rag_client, relevance, responder

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 20  # rag-service từ chối top_k lớn hơn 20
_FOLLOWUP_TRANSFORM_RE = re.compile(
    r"^\s*(?:hãy\s+|vui lòng\s+|giúp tôi\s+|bạn\s+)?"
    r"(?:tóm tắt|rút gọn|viết lại|trình bày lại|tổng hợp lại|cô đọng|nói ngắn|làm ngắn|kết luận ngắn|lập bảng)",
    re.IGNORECASE,
)


def _is_history_transform_request(user_content: str, history: list[dict]) -> bool:
    """Nhận diện yêu cầu tóm tắt/rút gọn/trình bày lại câu trả lời trước trong hội thoại."""
    if not history or not any(m.get("role") == "assistant" and m.get("content") for m in history):
        return False
    text = user_content.strip()
    if not text or len(text) > 400:
        return False
    return bool(_FOLLOWUP_TRANSFORM_RE.match(text))


def _budget_kw(budget) -> dict:
    return {"budget": budget} if budget is not None and budget.enabled else {}


async def _search_with_rewrite(
    user_content: str, history: list[dict], request_id: str | None, top_k: int, budget=None,
    *, fairness_key: str | None = None,
) -> list[dict]:
    """Tìm câu gốc (chạy nền, song song với việc viết lại) rồi nếu câu viết lại khác thì tìm thêm và gộp xen kẽ, câu gốc đứng
    trước. Không thay hẳn câu gốc vì câu viết lại có thể sai chi tiết. Chỉ lỗi tìm câu gốc (RAGError) mới ném ra ngoài; viết lại
    hoặc tìm câu viết lại lỗi thì ghi warning và dùng riêng kết quả câu gốc."""
    options = {**_budget_kw(budget), **rag_client.single_scheduling(fairness_key, "interactive")}
    original = asyncio.create_task(rag_client.search(user_content, top_k=top_k, request_id=request_id, **options))
    try:
        try:
            rewritten = await query_rewrite.rewrite(user_content, history, request_id, **_budget_kw(budget))
        except Exception as e:
            logger.warning("agent viết lại câu hỏi lỗi bất ngờ, dùng câu gốc request_id=%s: %s", request_id, e)
            return await original
        if " ".join(rewritten.split()).lower() == " ".join(user_content.split()).lower():
            return await original
        try:
            extra = await rag_client.search(rewritten, top_k=top_k, request_id=request_id, **options)
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


async def prepare(user_content: str, history: list[dict], request_id: str | None = None, *, owner_scope: str | None = None,
                  budget=None) -> dict | responder.LLMCall:
    """Bước chuẩn bị: rag rồi (nếu bật) chấm độ liên quan. Trả về:
    - dict {"content", "metadata"}: xong luôn, không cần gọi LLM trả lời (ngoài phạm vi, LLM_BYPASS);
    - responder.LLMCall: cần gọi LLM; `await call.run()` lấy cả câu, hoặc stream bằng llm_client.generate_stream.
    Ném rag_client.RAGError khi rag lỗi (nơi gọi đổi thành 503)."""
    settings = get_settings()
    started = time.perf_counter()
    budget = budget if budget is not None and budget.enabled else None
    primary_model, _ = case_budget.resolve_effective_models(settings)
    # Mọi nhánh dùng cùng sổ cái $0.01: model + budget đi theo từng lần gọi LLM
    ledger_options = {"model": primary_model, "budget": budget, "budget_stage": "responder"} if budget else {}
    if _is_history_transform_request(user_content, history) and not settings.llm_bypass:
        return responder.LLMCall(
            prompts.FOLLOWUP_SYSTEM_PROMPT, [], history, user_content,
            request_id, started, started,
            llm_options={"max_output_tokens": settings.case_max_output_tokens, **ledger_options},
            metadata_extra={"profile": "followup_summary"}, budget=budget,
        )
    if settings.adaptive_rag_enabled and query_plan.route(user_content) == "complex" and not settings.llm_bypass:
        return await _prepare_complex(user_content, history, request_id, started, owner_scope=owner_scope, admitted=budget)
    # Tìm 2 bước: lấy rộng rag_candidate_k đoạn làm ứng viên rồi để lượt chấm chọn lại (không thêm lượt gọi AI)
    answer_k = min(max(settings.rag_top_k, 1), MAX_CANDIDATES)
    candidate_k = min(max(settings.rag_candidate_k, 0), MAX_CANDIDATES)
    two_step = (
        candidate_k > answer_k
        and settings.relevance_check
        and not settings.llm_bypass
    )
    # Giữ ít nhất 4 chỗ cho câu gốc và các ý con, trong giới hạn API.
    search_k = min(
        max(candidate_k if two_step else answer_k, 4),
        MAX_CANDIDATES,
    )
    # LLM_BYPASS không có lượt gọi LLM nào, kể cả để viết lại câu hỏi
    fairness_key = rag_client.make_fairness_key(owner_scope, budget, request_id)
    if settings.query_rewrite_enabled and not settings.llm_bypass:
        chunks = await _search_with_rewrite(user_content, history, request_id, search_k, budget, fairness_key=fairness_key)
    else:
        chunks = await rag_client.search(
            user_content, top_k=search_k, request_id=request_id,
            **_budget_kw(budget), **rag_client.single_scheduling(fairness_key, "interactive"),
        )
    rag_done = time.perf_counter()

    graded_done = None
    auxiliary_tokens = 0
    # LLM_BYPASS là chế độ soi đoạn luật thô, không có lượt gọi LLM nào kể cả để chấm
    if settings.relevance_check and not settings.llm_bypass:
        info: dict = {}
        relevant = await relevance.grade(user_content, chunks, request_id, info=info, **_budget_kw(budget))
        auxiliary_tokens += info.get("tokens") or 0
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
            chunks = chunks[:answer_k]
            logger.info(
                "agent tìm 2 bước request_id=%s ứng_viên=%d giữ=%d trường_hợp=c (không chấm được)",
                request_id, candidates, len(chunks),
            )

    if budget is not None and not settings.llm_bypass:  # bớt đoạn luật cuối cho tới khi prompt + output tối thiểu vừa sổ cái
        chunks = responder.fit_chunks(budget, prompts.build_qa_prompt, chunks, history, user_content, settings.llm_max_output_tokens)
    system_prompt = prompts.build_qa_prompt(chunks)
    if settings.llm_bypass:
        return await responder.respond(
            system_prompt, chunks, history, user_content,
            queries=[user_content], request_id=request_id, started=started, rag_done=rag_done,
        )
    return responder.LLMCall(
        system_prompt, chunks, history, user_content, request_id, started, rag_done, graded_done,
        auxiliary_tokens=auxiliary_tokens, budget=budget,
        llm_options={"max_output_tokens": settings.llm_max_output_tokens, **ledger_options} if budget else {},
    )


def _build_sources_map(chunks: list[dict], prefix: str = "") -> dict[str, dict]:
    """Đóng gói metadata + toàn văn trích đoạn để giao diện hiển thị nút nguồn khi người dùng click."""
    return {
        f"{prefix}{i}": {
            "chunk_id": c.get("id"),
            "document": c.get("document"),
            "section_info": c.get("section_info"),
            "content": c.get("content", ""),
            "matched_issue_ids": c.get("matched_issue_ids", []),
        }
        for i, c in enumerate(chunks, 1)
    }


async def answer(user_content: str, history: list[dict], request_id: str | None = None, *, owner_scope: str | None = None,
                 budget=None) -> dict:
    """Ném rag_client.RAGError hoặc llm_client.LLMError khi dịch vụ ngoài lỗi (nơi gọi đổi thành 503)."""
    prepared = await prepare(user_content, history, request_id, **({"owner_scope": owner_scope} if owner_scope is not None else {}),
                             **({"budget": budget} if budget is not None else {}))
    if isinstance(prepared, dict):
        return prepared
    return await prepared.run()



def _clip_words(text: str, limit: int) -> str:
    """Cut at a word boundary, never mid-word."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return text[:cut if cut > limit // 2 else limit].rstrip()


def _query_rows(issues, cap: int, focused: bool, spare: int = 0) -> list[list]:
    """[[query, [issue_ids], exact_refs]]. One main query per issue first, then (focused mode) one query per distinct
    legal facet in round-robin order while leaving `spare` slots for the missing-facet round."""
    if not focused:
        return [[i.retrieval_query, [i.issue_id], i.exact_refs] for i in issues]
    rows: dict = {}

    def add(query, issue, refs):
        key = (" ".join(query.split()).casefold(), json.dumps(refs, sort_keys=True, ensure_ascii=False))
        row = rows.setdefault(key, [query, [], refs])
        if issue.issue_id not in row[1]:
            row[1].append(issue.issue_id)

    for issue in issues:
        add(issue.retrieval_query, issue, issue.exact_refs)
    room = cap - spare - len(rows)
    for depth in range(3):
        for issue in issues:
            if room > 0 and depth < len(issue.facet_queries):
                before = len(rows)
                add(issue.facet_queries[depth], issue, [])
                room -= len(rows) - before
    return list(rows.values())


def _round2_queries(issues, missing_ids, gaps) -> list[tuple]:
    """Round 2 asks ONLY for what is still missing: whole issues without evidence, then individual uncovered facets."""
    rows = []
    for issue in issues:
        if issue.issue_id in missing_ids:
            query = _clip_words(" ".join([*issue.document_hints, *issue.evidence_needs, issue.retrieval_query]), 1000)
            rows.append((issue.issue_id, query, *([issue.exact_refs] if issue.exact_refs else [])))
    for issue in issues:
        focus = issue.retrieval_query if len(issue.retrieval_query) <= 300 else issue.question
        rows.extend((issue.issue_id, _clip_words(f"{term} {focus}", 400)) for term in gaps.get(issue.issue_id, []))
    return rows


def _attach_query_ids(group, ids):
    return [{**chunk, "matched_issue_ids": list(dict.fromkeys(chunk.get("matched_issue_ids", []) + ids))} for chunk in group]


def _remove_depth_chunk(chunks, issue_ids, confirmed_only=False):
    for index in range(len(chunks) - 1, -1, -1):
        remaining = chunks[:index] + chunks[index + 1:]
        key = "confirmed_issue_ids" if confirmed_only else "matched_issue_ids"
        covered = {issue_id for chunk in remaining for issue_id in chunk.get(key, [])}
        old = set(chunks[index].get(key, [])) & set(issue_ids)
        if old <= covered:
            return remaining
    return chunks


async def _prepare_complex(user_content: str, history: list[dict], request_id: str | None,
                           started: float, *, owner_scope: str | None = None, admitted=None) -> responder.LLMCall:
    settings = get_settings()
    deadline = started + settings.case_total_deadline_seconds
    effective_output = settings.case_complex_max_output_tokens or settings.case_max_output_tokens
    if admitted is not None:  # sổ cái đã được admission tạo và giữ sẵn input + output của responder
        budget = admitted
        budget.deadline = deadline
        budget.max_tokens = budget.max_tokens or settings.case_token_budget_max
        budget.target_tokens = budget.target_tokens or settings.case_token_budget_target
    else:
        budget = case_budget.CaseBudget(target_tokens=settings.case_token_budget_target,
            max_tokens=settings.case_token_budget_max, max_cost_nano_usd=settings.case_cost_budget_micro_usd * 1000,
            deadline=deadline, responder_reserved_output=min(1900, effective_output))
    optimized = budget.enabled or settings.case_relevance_dedup_enabled
    prep_deadline = deadline - min(30.0, settings.case_total_deadline_seconds / 3.0) if optimized else deadline
    primary_model, _ = case_budget.resolve_effective_models(settings)
    provider = settings.llm_provider.split(",")[0].strip().lower() or "openrouter"
    source_parts_snapshot = query_plan.build_source_parts(user_content, history)
    plan_snapshot = None
    best_chunks = []
    total_tokens = 0
    usage = {}
    selection = None
    rag_done = started
    degraded = retrieval_degraded = False
    rounds = query_count = 0
    relevance_memo = {}
    try:
        async with asyncio.timeout(max(0.001, prep_deadline - time.monotonic())):
            kwargs = {"budget": budget, "owner_scope": owner_scope} if optimized else {}
            search_kw = _budget_kw(budget)
            rag_options = {
                "expand_adjacent_clauses": True, "follow_citations": True,
                "fairness_key": rag_client.make_fairness_key(owner_scope, budget, request_id),
                "priority": "batch",
            }
            plan_snapshot = await query_plan.plan(user_content, history, request_id, info=usage, **kwargs)
            total_tokens = usage.get("tokens") or 0
            issue_ids = [i.issue_id for i in plan_snapshot.issues]
            cap = settings.case_max_retrieval_queries
            spare = 2 if optimized and settings.case_max_retrieval_rounds >= 2 else 0
            grouped = _query_rows(plan_snapshot.issues, cap, optimized, spare)
            if len(grouped) > cap:
                remainder = grouped[cap - 1:]
                bundled = "\n".join(row[0] for row in remainder)
                ids = [item for row in remainder for item in row[1]]
                refs = [ref for row in remainder for ref in row[2]][:4]
                grouped = grouped[:cap - 1] + [[bundled if len(bundled) <= rag_client.MAX_QUERY_LENGTH else user_content, ids, refs]]
            second_round_limit = 2 if optimized and settings.case_max_retrieval_rounds >= 2 and len(grouped) <= cap - 2 else cap
            queries = [(ids[0], query, *([refs] if refs else [])) for query, ids, refs in grouped]
            try:
                groups = await rag_client.search_many(queries, settings.case_candidate_k_per_issue, request_id,
                                                       **({"deadline": prep_deadline} if optimized else {}), **search_kw, **rag_options)
                retrieval_degraded |= getattr(groups, "degraded", False)
                groups = [_attach_query_ids(group, grouped[n][1]) for n, group in enumerate(groups)]
            except rag_client.RAGError:
                groups = [[] for _ in queries]
                retrieval_degraded = True
            query_count = len(queries)
            candidates = evidence_selection.merge_candidates(groups, settings.case_max_candidates)
            rag_done = time.perf_counter()
            graded = []
            for round_index in range(settings.case_max_retrieval_rounds):
                rounds += 1
                grading = {}
                try:
                    if settings.relevance_check and settings.case_relevance_dedup_enabled:
                        relevant = await relevance.grade_candidates_dedup(plan_snapshot.issues, candidates, request_id,
                            info=grading, budget=budget if budget.enabled else None, memo=relevance_memo)
                    elif settings.relevance_check:
                        relevant = await relevance.grade_issues(plan_snapshot.issues, candidates, request_id, info=grading,
                                                                 **({"budget": budget} if budget.enabled else {}))
                    else:
                        relevant = candidates
                except Exception:
                    if round_index == 0:
                        raise
                    total_tokens += grading.get("tokens") or 0
                    retrieval_degraded = True
                    break
                total_tokens += grading.get("tokens") or 0
                degraded |= grading.get("degraded", False)
                graded = evidence_selection.merge_candidates([graded, relevant], settings.case_max_candidates)
                opts = {"issues": plan_snapshot.issues, "use_value_density": True, "confirmed_only": settings.relevance_check} if optimized else {}
                selection = evidence_selection.select(graded, issue_ids, settings.case_context_token_budget, settings.case_select_max, **opts)
                # Ứng viên có sẵn nhưng chưa xếp vừa (unpacked) cũng phải thử hoán đổi, kể cả khi missing rỗng
                if optimized and (selection.missing_issue_ids or selection.unpacked_issue_ids):
                    selection = evidence_selection.swap_for_coverage(selection, graded, issue_ids, settings.case_context_token_budget,
                        settings.case_select_max, issues=plan_snapshot.issues, confirmed_only=settings.relevance_check)
                best_chunks = selection.chunks
                missing_ids = set(selection.missing_issue_ids)
                gaps = {}
                if optimized:
                    missing_ids -= set(selection.unpacked_issue_ids)
                    # Chỉ hỏi tiếp khía cạnh còn thiếu của issue ĐÃ có căn cứ (issue chưa có gì thì dùng truy vấn chính)
                    key = "confirmed_issue_ids" if settings.relevance_check else "matched_issue_ids"
                    covered = {iid for c in best_chunks for iid in c.get(key, [])}
                    gaps = {iid: terms for iid, terms in evidence_selection.missing_facets(
                        best_chunks, plan_snapshot.issues, settings.relevance_check).items() if iid in covered}
                if (not (missing_ids or gaps) or round_index + 1 >= settings.case_max_retrieval_rounds or query_count >= cap
                        or (optimized and (time.monotonic() >= prep_deadline or budget.exceeded))):
                    break
                extra_queries = _round2_queries(plan_snapshot.issues, missing_ids, gaps)[:min(cap - query_count, second_round_limit)]
                query_count += len(extra_queries)
                try:
                    extra_groups = await rag_client.search_many(extra_queries, settings.case_candidate_k_per_issue, request_id,
                                                                **({"deadline": prep_deadline} if optimized else {}), **search_kw, **rag_options)
                    retrieval_degraded |= getattr(extra_groups, "degraded", False)
                except Exception:
                    retrieval_degraded = True
                    break
                candidates = evidence_selection.merge_candidates(extra_groups, settings.case_max_candidates)
    except TimeoutError:
        total_tokens = max(total_tokens, usage.get("tokens") or 0)
        retrieval_degraded = True

    plan = plan_snapshot or query_plan.fallback(user_content)
    issue_ids = [i.issue_id for i in plan.issues]
    ungraded_fallback = False
    if optimized and settings.relevance_check and degraded and not best_chunks and plan_snapshot is not None:
        # Grader hỏng KHÔNG đồng nghĩa chunk bị bác: chỉ chunk "chưa chấm" (grading_degraded) được dùng thay vì gửi responder 0 chunk
        ungraded = [c for c in graded if c.get("grading_degraded") and not c.get("confirmed_issue_ids")]
        if ungraded:
            best_chunks = evidence_selection.select(ungraded, issue_ids, settings.case_context_token_budget, settings.case_select_max,
                                                    issues=plan_snapshot.issues, use_value_density=True).chunks
            ungraded_fallback = bool(best_chunks)
    confirmed = optimized and settings.relevance_check and not ungraded_fallback
    chunks = best_chunks
    plan_text = query_plan.format_plan_for_prompt(plan, source_parts_snapshot) if optimized else plan.model_dump_json()
    effective_history = history
    output_target = effective_output if optimized else settings.case_max_output_tokens
    fallback_reason = None
    if budget.enabled:
        def available(current_chunks, current_history):
            prompt = prompts.build_complex_prompt(current_chunks, plan_text, output_target=effective_output)
            upper = case_budget.estimate_chat_input_upper_bound(prompt, current_history, user_content, budget.strict_byte_upper_bound)
            out = budget.available_responder_output_tokens(provider, primary_model, upper, effective_output,
                min_output=budget.responder_min_output or 256, reasoning_tokens=settings.openrouter_reasoning_max_tokens)
            return out, bool(budget.responder_input_cap and upper > budget.responder_input_cap)
        dynamic_output, over_cap = available(chunks, effective_history)
        if not dynamic_output and effective_history:
            effective_history = []
            dynamic_output, over_cap = available(chunks, effective_history)
        # Bớt chứng cứ sâu cho tới khi vừa cả phần input mục tiêu lẫn output tối thiểu đã giữ cho responder
        while (not dynamic_output or over_cap) and len(chunks) > len(plan.issues):
            reduced = _remove_depth_chunk(chunks, issue_ids, confirmed)
            if len(reduced) == len(chunks):
                break
            chunks = reduced
            dynamic_output, over_cap = available(chunks, effective_history)
        if dynamic_output:
            output_target = dynamic_output
        else:
            fallback_reason = "budget_exhausted"
    prompt = prompts.build_complex_prompt(chunks, plan_text, **({"output_target": output_target} if optimized else {}))
    options = {"max_output_tokens": output_target, "max_retries": 0, "timeout_seconds": max(0.001, deadline - time.monotonic())}
    if budget.enabled:
        options.update(model=primary_model, budget=budget, budget_stage="responder")
    # Đo lại SAU lần đóng gói cuối: đúng phần chứng cứ thực sự gửi cho responder (không phải kết quả của select)
    final = evidence_selection.pack_stats(chunks, issue_ids, confirmed)
    metadata = {"profile": "complex", "planner_fallback": usage.get("fallback", False), "grading_degraded": degraded, "evidence_unconfirmed": ungraded_fallback,
        "missing_issue_ids": final["missing_issue_ids"], "retrieval_degraded": retrieval_degraded,
        "retrieval_missing_facets": evidence_selection.missing_facets(chunks, plan.issues, confirmed),
        "context_tokens": final["evidence_tokens"], "evidence_tokens": final["evidence_tokens"], "evidence_bytes": final["evidence_bytes"],
        "retrieval_rounds": rounds, "retrieval_query_count": query_count,
        "issues": [i.model_dump() for i in plan.issues], "sources": _build_sources_map(chunks, "S")}
    if budget.enabled:
        metadata["budget_summary"] = budget.summary()
    return responder.LLMCall(prompt, chunks, effective_history, user_content, request_id, started, rag_done, time.perf_counter(),
        llm_options=options, metadata_extra=metadata, auxiliary_tokens=total_tokens, deadline=deadline,
        budget=budget if budget.enabled else None, source_parts=source_parts_snapshot, fallback_reason=fallback_reason)
