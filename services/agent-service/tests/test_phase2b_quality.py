"""Phase 2B: planner, focused retrieval, relevance rows, evidence value, partial_grounded finalizer, config sync."""
import asyncio
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.core.config import Settings, get_settings
from app.services import (admission, case_budget, evidence_selection, llm_client, prompts, qa_agent, query_plan, rag_client,
                          relevance, responder)
from app.services.query_plan import Issue, SourcePart

BE_ROOT = Path(__file__).resolve().parents[3]


def env(monkeypatch, **values):
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


# ── 1. Planner: clean source parts, independent validation, observable failures ────────────────────────────────────

def _assert_whole_words(text, parts):
    # Cuts only land on whitespace: the words, in order, are exactly the original words
    assert " ".join(parts).split() == text.split()
    assert all(len(p) <= query_plan.SOURCE_WINDOW_CHARS for p in parts)


def test_source_parts_cut_at_sentence_boundaries_first():
    sentences = [f"Ông An cam kết chuyển mục đích sử dụng đất số {i} trước ngày 30/06/2025." for i in range(40)]
    text = " ".join(sentences)
    parts = query_plan.source_parts(text)
    assert len(parts) > 1
    _assert_whole_words(text, parts)
    assert all(p.endswith(".") for p in parts)  # sentence-level cuts, so no clause is split


def test_source_parts_cut_at_clause_then_word_boundaries_never_mid_word():
    clause = "chuyển mục đích sử dụng đất phụ thuộc quyết định của cơ quan nhà nước"
    by_clause = ", ".join([clause] * 40)
    parts = query_plan.source_parts(by_clause)
    assert len(parts) > 1
    _assert_whole_words(by_clause, parts)
    by_word = " ".join([clause] * 40)  # no punctuation at all
    parts = query_plan.source_parts(by_word)
    assert len(parts) > 1
    _assert_whole_words(by_word, parts)
    assert all(p.split()[0] in clause.split() and p.split()[-1] in clause.split() for p in parts)


def test_source_parts_hard_cut_only_when_no_boundary_exists():
    text = "x" * 2000
    parts = query_plan.source_parts(text)
    assert "".join(parts) == text and all(len(p) <= query_plan.SOURCE_WINDOW_CHARS for p in parts)


def test_numbered_questions_still_split_on_labels():
    parts = query_plan.source_parts("Tình huống chung.\nCâu 1: Cọc có hiệu lực không?\nCâu 2: Thời hiệu bao lâu?")
    assert [p.split(":")[0].split()[-1] for p in parts[-2:]] == ["1", "2"]


def _table():
    return [SourcePart("P1", "request", "Đặt cọc 3 tỷ đồng để mua đất", 0, 10),
            SourcePart("P2", "request", "Thời hiệu khởi kiện đòi lại cọc", 11, 20),
            SourcePart("P3", "request", "Lãi trên khoản tiền cọc phải hoàn", 21, 30)]


def test_planner_validates_each_issue_independently_and_repairs_locally():
    raw = json.dumps({"facts": ["cọc 3 tỷ"], "issues": [
        {"s": "I1", "p": ["P1"], "q": "Đặt cọc", "r": "đặt cọc bảo đảm giao kết hợp đồng", "f": ["lãi đối với khoản tiền đặt cọc", "lãi đối với khoản tiền đặt cọc"],
         "n": ["quy tắc", "lãi"], "x": [{"doc_ref": "BLDS", "article": "328", "title": "Đặt cọc", "junk": "x"}, "bad", {"article": ["no"]}]},
        {"s": "I2", "p": ["P9"], "q": "không có đoạn", "r": "y"},      # unknown source part
        {"s": "I3", "p": ["P2"], "q": "", "r": "thời hiệu"},           # empty question: invalid field
    ]})
    reasons: list[str] = []
    plan = query_plan._parse_query_plan(raw, _table(), 8, "stop", reasons)
    first = plan.issues[0]
    assert first.issue_id == "I1" and first.exact_refs == [{"doc_ref": "BLDS", "article": "328", "title": "Đặt cọc"}]
    assert first.facet_queries == ["lãi đối với khoản tiền đặt cọc"]
    assert first.source_text == "Đặt cọc 3 tỷ đồng để mua đất"  # the application fills the original text; the model returns IDs only
    covered = {pid for issue in plan.issues for pid in issue.part_ids}
    assert covered == {"P1", "P2", "P3"}
    repaired = [i for i in plan.issues if i.part_ids in (["P2"], ["P3"])]
    assert len(repaired) == 2 and all(i.retrieval_query == i.source_text for i in repaired)  # local repair, no second LLM call
    text = " | ".join(reasons)
    assert "unknown part ids" in text and "invalid field question" in text and "P2 repaired locally" in text


def test_planner_all_rows_invalid_raises_with_exact_reasons_and_finish_reason():
    raw = json.dumps({"issues": [{"s": "I1", "p": ["P9"], "q": "x", "r": "y"}]})
    with pytest.raises(ValueError) as caught:
        query_plan._parse_query_plan(raw, _table(), 8, "length")
    message = str(caught.value)
    assert "no valid planner issues" in message and "unknown part ids" in message and "finish_reason=length" in message


def _plan_json(parts):
    return json.dumps({"issues": [{"s": "I1", "p": ["P1"], "q": "Đặt cọc", "r": "đặt cọc"},
                                  {"s": "I2", "p": ["P2"], "q": "Thời hiệu", "r": "thời hiệu khởi kiện"}]})


QUESTION = "Câu 1: Đặt cọc là gì?\nCâu 2: Thời hiệu bao lâu?"


def test_plan_sends_compact_schema_honors_cap_logs_failure_reason(monkeypatch):
    seen = {}

    async def generate(system, history, user, **kwargs):
        seen.update(kwargs, system=system, user=user)
        return llm_client.Completion(("không phải json", 50, "m"), "length")

    monkeypatch.setattr(llm_client, "generate", generate)
    info = {}
    plan = asyncio.run(query_plan.plan(QUESTION, [], info=info))
    assert seen["max_output_tokens"] == 896 == get_settings().planner_max_output_tokens
    assert seen["response_format"]["type"] == "json_schema" and "source_text" not in seen["system"]
    assert json.loads(seen["user"]) == [{"id": p.id, "text": p.text} for p in query_plan.build_source_parts(QUESTION, [])]
    assert info["fallback"] and info["planner_finish_reason"] == "length"
    assert "no valid planner issues" in info["planner_failure"] and "finish_reason=length" in info["planner_failure"]
    assert len(plan.issues) == 2  # whole input still covered by the local fallback


def test_plan_cache_is_owner_scoped_and_keyed_by_schema_version(monkeypatch):
    env(monkeypatch, CASE_RELEVANCE_DEDUP_ENABLED="true")
    monkeypatch.setattr(query_plan, "_cache", type(query_plan._cache)())
    calls = []

    async def generate(system, history, user, **kwargs):
        calls.append(1)
        return llm_client.Completion((_plan_json(None), 40, "m"), "stop")

    monkeypatch.setattr(llm_client, "generate", generate)
    run = lambda owner: asyncio.run(query_plan.plan(QUESTION, [], info={}, owner_scope=owner))
    run("u1"), run("u1")
    assert len(calls) == 1
    run("u2")  # another owner never reads u1's plan
    assert len(calls) == 2
    monkeypatch.setattr(query_plan, "PLANNER_SCHEMA_VERSION", "compact-v-next")
    run("u1")
    assert len(calls) == 3


# ── 2. Focused retrieval and round 2 ───────────────────────────────────────────────────────────────────────────────

def _issue(iid="I1", needs=None, facets=None, refs=None, query="đặt cọc bảo đảm giao kết hợp đồng"):
    return Issue(issue_id=iid, question=f"Vấn đề {iid}", retrieval_query=query, evidence_needs=needs or ["quy tắc"],
                 facet_queries=facets or [], exact_refs=refs or [])


def test_query_rows_main_first_then_one_query_per_facet_within_cap():
    issues = [_issue("I1", facets=["lãi đối với tiền đặt cọc", "thời hiệu khởi kiện đòi cọc"], refs=[{"doc_ref": "BLDS", "article": "328"}]),
              _issue("I2", query="chuyển mục đích sử dụng đất", facets=["điều kiện chuyển mục đích"])]
    rows = qa_agent._query_rows(issues, cap=8, focused=True, spare=2)
    assert [r[0] for r in rows[:2]] == [issues[0].retrieval_query, "chuyển mục đích sử dụng đất"]
    assert rows[0][2] == [{"doc_ref": "BLDS", "article": "328"}]
    assert {r[0] for r in rows[2:]} == {"lãi đối với tiền đặt cọc", "thời hiệu khởi kiện đòi cọc", "điều kiện chuyển mục đích"}
    assert len(qa_agent._query_rows(issues, cap=5, focused=True, spare=2)) == 5 - 2  # leaves room for the missing-facet round
    assert len(qa_agent._query_rows(issues, cap=8, focused=False)) == 2  # legacy path: one query per issue, no facets


def test_round2_queries_cover_only_missing_issue_and_missing_facets():
    issues = [_issue("I1"), _issue("I2"), _issue("I3")]
    rows = qa_agent._round2_queries(issues, {"I2"}, {"I1": ["lãi", "thời hiệu"]})
    assert [r[0] for r in rows] == ["I2", "I1", "I1"] and rows[1][1].startswith("lãi ") and rows[2][1].startswith("thời hiệu ")
    assert all(r[0] != "I3" for r in rows)


def test_search_many_passes_per_query_exact_refs_and_clause_expansion(monkeypatch):
    sent = {}

    async def search(query, **kwargs):
        sent[query] = kwargs
        return []

    monkeypatch.setattr(rag_client, "search", search)
    refs = [{"doc_ref": "BLDS", "article": "328", "title": "Đặt cọc"}]
    asyncio.run(rag_client.search_many([("I1", "q1", refs), ("I2", "q2")], 12, expand_adjacent_clauses=True, follow_citations=True))
    assert sent["q1"]["exact_refs"] == refs and not sent["q2"]["exact_refs"]
    assert all(kw["expand_adjacent_clauses"] and kw["follow_citations"] and kw["planned"] for kw in sent.values())


def _complex_env(monkeypatch):
    env(monkeypatch, ADAPTIVE_RAG_ENABLED="true", CASE_RELEVANCE_DEDUP_ENABLED="true", RELEVANCE_CHECK="false")


def _doc_chunk(cid="c1", content="Điều 328. Đặt cọc là việc một bên giao tiền.", ids=("I1",), dieu="328", khoan=None):
    return {"id": cid, "content": content, "score": 0.9, "section_info": {"dieu": dieu, "khoan": khoan},
            "document": {"id": "blds", "title": "Bộ luật Dân sự 2015", "doc_number": "91/2015/QH13"},
            "matched_issue_ids": list(ids)}


def test_retrieval_asks_focused_facets_then_only_the_missing_facets(monkeypatch):
    _complex_env(monkeypatch)
    plan = query_plan.QueryPlan(issues=[
        _issue("I1", needs=["quy tắc", "lãi", "thời hiệu"], facets=["lãi đối với khoản tiền đặt cọc", "thời hiệu khởi kiện đòi cọc"],
               refs=[{"doc_ref": "BLDS", "article": "328", "title": "Đặt cọc"}]),
        _issue("I2", query="chuyển mục đích sử dụng đất")])
    calls = []

    async def fake_plan(*args, **kwargs):
        return plan

    async def search_many(queries, *args, **kwargs):
        calls.append((queries, kwargs))
        if len(calls) == 1:
            return [[_doc_chunk(ids=(q[0],))] for q in queries]
        return [[] for _ in queries]

    monkeypatch.setattr(query_plan, "plan", fake_plan)
    monkeypatch.setattr(rag_client, "search_many", search_many)
    prepared = asyncio.run(qa_agent.prepare(QUESTION, []))
    first, second = calls[0][0], calls[1][0]
    assert first[0] == ("I1", plan.issues[0].retrieval_query, plan.issues[0].exact_refs)
    assert {q[1] for q in first} >= {"lãi đối với khoản tiền đặt cọc", "thời hiệu khởi kiện đòi cọc"}
    assert len(second) == 2 and {q[1].split()[0] for q in second} == {"lãi", "thời"} and {q[0] for q in second} == {"I1"}
    assert all(kw["expand_adjacent_clauses"] is True and kw["follow_citations"] is True for _, kw in calls)
    assert prepared.metadata_extra["retrieval_rounds"] == 2
    assert prepared.metadata_extra["retrieval_missing_facets"] == {"I1": ["lãi", "thời hiệu"]}


# ── 3. Relevance rows ──────────────────────────────────────────────────────────────────────────────────────────────

def _grade(monkeypatch, rows, chunks, issues, text_or_error, **kwargs):
    calls = []

    async def generate(prompt, *args, **kw):
        calls.append(prompt)
        return text_or_error, 5, "m"

    monkeypatch.setattr(llm_client, "generate", generate)
    info, memo = {}, kwargs.pop("memo", {})
    result = asyncio.run(relevance.grade_candidates_dedup(issues, chunks, info=info, memo=memo, **kwargs))
    return result, info, calls, memo


def _two_chunks():
    return [{"id": "a", "content": "Đặt cọc là việc giao tiền để bảo đảm giao kết hợp đồng.", "matched_issue_ids": ["I1"]},
            {"id": "b", "content": "Đặt cọc bị phạt cọc khi từ chối giao kết hợp đồng.", "matched_issue_ids": ["I1"]}]


def test_one_malformed_row_degrades_only_that_row_and_roles_are_kept(monkeypatch):
    issue = [_issue("I1", query="đặt cọc hợp đồng")]
    result, info, _, memo = _grade(monkeypatch, None, _two_chunks(), issue, '{"rows": [[1, [[1, "r"]]], [2, [[5, "r"]]]]}')
    good, bad = {c["id"]: c for c in result}["a"], {c["id"]: c for c in result}["b"]
    assert good["confirmed_issue_ids"] == ["I1"] and good["support_roles"] == {"I1": "rule"} and good["relevance_confirmed"]
    assert bad["confirmed_issue_ids"] == [] and bad["relevance_confirmed"] is False and bad["grading_degraded"]
    assert info["degraded"] and len(memo) == 1  # the invalid row writes no verdict to the memo


def test_truncated_json_keeps_the_complete_rows(monkeypatch):
    issue = [_issue("I1", query="đặt cọc hợp đồng")]
    result, info, _, _ = _grade(monkeypatch, None, _two_chunks(), issue, '{"rows": [[1, [[1, "c"]]], [2, [[1, "r"')
    by_id = {c["id"]: c for c in result}
    assert by_id["a"]["support_roles"] == {"I1": "condition"} and by_id["b"]["relevance_confirmed"] is False and info["degraded"]


def test_lexical_prefilter_skips_unrelated_pairs_without_spending_grader_tokens(monkeypatch):
    issue = [_issue("I1", query="đặt cọc hợp đồng")]
    unrelated = {"id": "x", "content": "Thuế thu nhập cá nhân của người lao động tự do.", "matched_issue_ids": ["I1"]}
    result, info, calls, _ = _grade(monkeypatch, None, [unrelated], issue, '{"rows": [[1, [[1, "r"]]]]}')
    assert calls == [] and result == [] and info["prefiltered_pairs"] == 1 and info["batches"] == 0
    exact = {**unrelated, "id": "y", "rank_sources": ["exact"]}  # an exact/citation hit is always graded
    result, _, calls, _ = _grade(monkeypatch, None, [exact], issue, '{"rows": [[1, [[1, "r"]]]]}')
    assert len(calls) == 1 and result[0]["confirmed_issue_ids"] == ["I1"]


def test_grader_batches_respect_the_total_output_cap_of_the_stage(monkeypatch):
    settings = Settings(_env_file=None, llm_provider="openrouter", openrouter_model="deepseek/deepseek-v4.1-flash", llm_aux_reasoning_max_tokens=0)
    monkeypatch.setattr(relevance, "get_settings", lambda: settings)
    chunks = [{"id": str(i), "content": f"Đặt cọc hợp đồng số {i}.", "matched_issue_ids": ["I1"]} for i in range(12)]
    sizes = []
    original = relevance._batch_fits

    def observe(budget, config, prompt, count):
        fits = original(budget, config, prompt, count)
        if fits:
            sizes.append(count)
        return fits

    async def generate(prompt, *args, **kwargs):
        assert kwargs["max_output_tokens"] <= 100  # the cap actually sent never exceeds the stage's output allowance
        return json.dumps({"rows": [[i, [[1, "r"]]] for i in range(1, sizes[-1] + 1)]}), 1, "m"

    monkeypatch.setattr(relevance, "_batch_fits", observe)
    monkeypatch.setattr(llm_client, "generate", generate)
    budget = case_budget.CaseBudget(max_tokens=0, max_cost_nano_usd=10_000_000, responder_reserved_output=0,
                                    stage_caps={"relevance": {"input": 3600, "output": 100}})
    result = asyncio.run(relevance.grade_candidates_dedup([_issue("I1", query="đặt cọc hợp đồng")], chunks, budget=budget, info={}))
    assert sizes and max(sizes) * 16 + 24 <= 100 and len(result) == 12


def test_evidence_window_keeps_whole_clauses():
    text = "\n".join(f"{n}. Khoản số {n} quy định điều kiện đặt cọc " + "chi tiết " * 12 for n in range(1, 11))
    window = prompts.evidence_window(text, "điều kiện đặt cọc", 400)
    lines = {line.strip() for line in text.split("\n")}
    assert len(window) <= 400 and window and all(line in lines for line in window.split("\n"))


# ── 4. Evidence selection: real tokens, clause value, swap, recount after packing ─────────────────────────────────

def _k(cid, content, khoan, rank):
    chunk = _doc_chunk(cid, content, ("I1",), dieu="6", khoan=khoan)
    chunk["document"] = {"id": "nq03", "title": "Nghị quyết 03/2026/NQ-HĐTP", "doc_number": "03/2026/NQ-HĐTP"}
    chunk.update(confirmed_issue_ids=["I1"], retrieval_provenance=[{"issue_id": "I1", "query": "q", "rank": rank}])
    return chunk


K3 = _k("k3", "Khoản 3 Điều 6: bên có lỗi làm mất hiệu lực hợp đồng phải bồi thường thiệt hại.", "3", 1)
K4 = _k("k4", "Khoản 4 Điều 6: tiền lãi trên khoản phạt cọc tính từ ngày nhận cọc đến ngày trả.", "4", 2)
K5 = _k("k5", "Khoản 5 Điều 6: thời hiệu khởi kiện yêu cầu hoàn trả tiền cọc là ba năm.", "5", 3)
NEEDS = _issue("I1", needs=["lỗi", "lãi", "phạt cọc", "thời hiệu"])


def test_token_cost_is_tokens_not_utf8_bytes():
    text = prompts.format_case_chunk(1, K4) + "\n\n"
    assert evidence_selection.token_cost(text) == case_budget.estimate_text_tokens(text) < len(text.encode("utf-8"))
    selection = evidence_selection.select([K3], ["I1"], 6000, 16, issues=[NEEDS], use_value_density=True, confirmed_only=True)
    one = prompts.format_case_chunk(1, selection.chunks[0]) + "\n\n"
    assert selection.tokens == evidence_selection.token_cost(one) and selection.bytes == len(one.encode("utf-8"))
    assert selection.bytes > selection.tokens  # bytes and tokens are tracked separately


def test_distinct_clauses_of_the_same_law_each_get_independent_value():
    selection = evidence_selection.select([K3, K4, K5], ["I1"], 6000, 16, issues=[NEEDS], use_value_density=True, confirmed_only=True)
    assert [c["id"] for c in selection.chunks] == ["k3", "k4", "k5"]  # old logic gave K.4/K.5 zero value once K.3 was in


def test_a_distinct_clause_has_value_even_when_it_adds_no_new_facet_word():
    neutral = _issue("I1", needs=["quy tắc"])  # no facet term asked: only the per-clause value can pick K.4 / K.5
    rows = [_k("k3", "Khoản 3 Điều 6 quy định về hồ sơ.", "3", 1), _k("k4", "Khoản 4 Điều 6 quy định về trình tự.", "4", 2),
            _k("k5", "Khoản 5 Điều 6 quy định về thời điểm.", "5", 3)]
    selection = evidence_selection.select(rows, ["I1"], 6000, 16, issues=[neutral], use_value_density=True, confirmed_only=True)
    assert [c["id"] for c in selection.chunks] == ["k3", "k4", "k5"]
    assert evidence_selection.select(rows, ["I1"], 6000, 2, issues=[neutral], use_value_density=True, confirmed_only=True).chunks[1]["id"] == "k4"


def test_facet_weighted_density_prefers_the_clause_with_more_new_facets_under_a_tight_budget():
    cost = lambda n, c: evidence_selection.token_cost(prompts.format_case_chunk(n, c) + "\n\n")
    selection = evidence_selection.select([K3, K5, K4], ["I1"], cost(1, K3) + cost(2, K4), 16, issues=[NEEDS],
                                          use_value_density=True, confirmed_only=True)
    assert [c["id"] for c in selection.chunks] == ["k3", "k4"]  # K4 covers lãi + phạt cọc, K5 only thời hiệu


def test_facet_list_covers_the_required_legal_facets():
    for term in ("lỗi", "lãi", "bồi thường", "phạt cọc", "thời hiệu", "hiệu lực", "chuyển mục đích",
                 "trở ngại khách quan", "bất khả kháng", "thẩm quyền", "ngoại lệ", "điều kiện"):
        assert term in evidence_selection.FACET_TERMS


def _spy_pipeline(monkeypatch, selection):
    _complex_env(monkeypatch)
    plan = query_plan.QueryPlan(issues=[_issue("I1"), _issue("I2", query="thuế chuyển nhượng")])
    swaps = []

    async def fake_plan(*args, **kwargs):
        return plan

    async def search_many(queries, *args, **kwargs):
        return [[_doc_chunk(ids=(q[0],))] for q in queries]

    def swap(sel, *args, **kwargs):
        swaps.append(sel)
        return sel

    monkeypatch.setattr(query_plan, "plan", fake_plan)
    monkeypatch.setattr(rag_client, "search_many", search_many)
    monkeypatch.setattr(evidence_selection, "select", lambda *a, **k: selection)
    monkeypatch.setattr(evidence_selection, "swap_for_coverage", swap)
    return swaps


def test_swap_runs_when_only_unpacked_ids_remain_and_stats_are_recomputed_after_final_packing(monkeypatch):
    chunk = {**_doc_chunk(), "matched_issue_ids": ["I1"]}
    stale = evidence_selection.Selection([chunk], [], 999_999, ["I2"], 1)  # claims nothing is missing, with bogus size
    swaps = _spy_pipeline(monkeypatch, stale)
    prepared = asyncio.run(qa_agent.prepare(QUESTION, []))
    assert swaps, "swap_for_coverage must run when unpacked_issue_ids is non-empty even though missing_issue_ids is empty"
    meta = prepared.metadata_extra
    text = prompts.format_case_chunk(1, prepared.chunks[0]) + "\n\n"
    assert meta["evidence_tokens"] == meta["context_tokens"] == evidence_selection.token_cost(text) != 999_999
    assert meta["evidence_bytes"] == len(text.encode("utf-8")) > meta["evidence_tokens"]
    assert meta["missing_issue_ids"] == ["I2"]  # coverage measured on what is actually sent, not what select() claimed
    assert list(meta["sources"]) == ["S1"] and meta["sources"]["S1"]["chunk_id"] == "c1"


# ── 5. Responder: partial_grounded, insufficient state, POST/SSE parity ───────────────────────────────────────────

ISSUES = [{"issue_id": f"I{n}", "question": q} for n, q in enumerate(["Hiệu lực?", "Lãi?", "Thời hiệu?"], 1)]
CHUNKS = [{"id": f"c{n}", "content": "x", "matched_issue_ids": [f"I{n}"], "confirmed_issue_ids": [f"I{n}"]} for n in (1, 2, 3)]
LEAD = "## Kết luận ngắn và căn cứ chính\n- Cọc có hiệu lực [S1]; lãi tính theo [S2]."
UNIT1 = "## Vấn đề 1: Hiệu lực\nQuy tắc [S1]. Đối chiếu tình tiết. Kết luận: hợp đồng cọc có hiệu lực."
UNIT2 = "## Vấn đề 2: Lãi\nQuy tắc [S2]. Ngoại lệ: không có. Kết luận: tính lãi từ ngày nhận cọc."
CUT = "## Vấn đề 3: Thời hiệu\nQuy tắc [S3] là ba năm, trừ trường hợp"
TRUNCATED = "\n\n".join([LEAD, UNIT1, UNIT2, CUT])


def _call(extra=None, chunks=None):
    return responder.LLMCall("system", chunks if chunks is not None else CHUNKS, [], "q", None, time.perf_counter(), time.perf_counter(),
                             metadata_extra={"profile": "complex", "issues": ISSUES, **(extra or {})})


def test_length_keeps_complete_grounded_units_and_drops_the_cut_off_tail():
    result = _call({"retrieval_missing_facets": {"I3": ["thời hiệu"]}}).finalize_complex(TRUNCATED, 321, "m", True, "length")
    content, meta = result["content"], result["metadata"]
    assert all(part in content for part in (LEAD, UNIT1, UNIT2))
    assert "Vấn đề 3" not in content and "trừ trường hợp" not in content  # fragment and its conditioned conclusion are gone
    assert meta["answer_mode"] == "partial_grounded" and meta["truncated"] is True and meta["finish_reason"] == "length"
    assert meta["missing_facets"] == ["I3: Thời hiệu?", "I3/thời hiệu"] and meta["tokens_used"] == 321


def test_units_with_unverified_or_missing_citations_are_dropped():
    answer = "\n\n".join([LEAD, "## Vấn đề 1: Hiệu lực\nQuy tắc [S9]. Kết luận: có hiệu lực.", "## Ghi chú\nKhông có mã căn cứ.", UNIT2, CUT])
    content = _call().finalize_complex(answer, 1, "m", True, "length")["content"]
    assert LEAD in content and UNIT2 in content and "[S9]" not in content and "Ghi chú" not in content


def test_no_usable_unit_returns_explicit_insufficient_state_not_a_template():
    for answer in ("## Vấn đề 1: Hiệu lực\nQuy tắc [S1] nhưng bị cắt", "## Mở đầu\nKhông có căn cứ\n\n## Vấn đề 1\nQuy tắc [S1] dở"):
        result = _call().finalize_complex(answer, 7, "m", True, "length")
        meta = result["metadata"]
        assert meta["answer_mode"] == "insufficient_evidence" and meta["truncated"] and meta["finish_reason"] == "length"
        assert meta["missing_facets"] and "Quy tắc" not in result["content"] and "short_fallback" not in json.dumps(meta)
    empty = _call().finalize_complex("  ", 3, "m", False, "stop")
    assert empty["metadata"]["answer_mode"] == "insufficient_evidence" and empty["metadata"]["truncated"] is False


def test_complete_answer_is_returned_unchanged():
    answer = "\n\n".join([LEAD, UNIT1, UNIT2])
    result = _call().finalize_complex(answer, 9, "m", False, "stop")
    assert result["content"] == answer and "answer_mode" not in result["metadata"]


def test_post_and_sse_deliver_the_same_partial_content_and_metadata(monkeypatch):
    from app.api.v1 import conversations
    keys = ("answer_mode", "truncated", "finish_reason", "missing_facets", "tokens_used")

    async def generate(*args, **kwargs):
        return llm_client.Completion((TRUNCATED, 321, "m"), "length")

    monkeypatch.setattr(llm_client, "generate", generate)
    post = asyncio.run(_call().run())

    async def stream(*args, info, **kwargs):
        for piece in (TRUNCATED[:40], TRUNCATED[40:]):
            yield piece
        info.update(tokens=321, model="m", truncated=True, finish_reason="length")

    saved = []

    async def threadpool(fn, *args, **kwargs):
        saved.append(args)
        return object(), object()

    monkeypatch.setattr(llm_client, "generate_stream", stream)
    monkeypatch.setattr(conversations, "run_in_threadpool", threadpool)
    monkeypatch.setattr(conversations.quota, "add_daily_tokens", lambda *a, **k: None)
    monkeypatch.setattr(conversations.MessageOut, "model_validate", lambda value: {})
    monkeypatch.setattr(conversations, "SendMessageResponse", lambda **kwargs: kwargs)

    async def collect():
        return [event async for event in conversations._stream_events(
            None, NS(user_id="u"), NS(content="q", document_ids=[]), "id", datetime.now(timezone.utc), _call(), None)]

    events = asyncio.run(collect())
    deltas = [e for e in events if e.startswith("event: delta")]
    assert len(deltas) == 1 and events[-1].startswith("event: done") and "Vấn đề 3" not in deltas[0]
    content, metadata = saved[0][3], saved[0][4]
    assert content == post["content"] == json.loads(deltas[0].split("data: ", 1)[1])["text"]
    assert {k: metadata[k] for k in keys} == {k: post["metadata"][k] for k in keys}


def test_provider_error_deadline_and_zero_budget_raise_instead_of_returning_a_template(monkeypatch):
    async def fail(*args, **kwargs):
        raise llm_client.LLMError("down")

    monkeypatch.setattr(llm_client, "generate", fail)
    with pytest.raises(llm_client.LLMError):
        asyncio.run(_call().run())
    call = _call()
    call.fallback_reason = "budget_exhausted"
    with pytest.raises(case_budget.BudgetExhaustedError):
        asyncio.run(call.run())
    expired = _call()
    expired.deadline = time.monotonic() - 1
    with pytest.raises(llm_client.LLMError):
        asyncio.run(expired.run())


def test_complex_prompt_leads_with_conclusions_and_self_contained_issue_units():
    prompt = prompts.build_complex_prompt([], "{}", output_target=3500)
    assert prompt.index("Kết luận ngắn và căn cứ chính") < prompt.index("Vấn đề N")
    assert "quy tắc & hiệu lực -> đối chiếu tình tiết -> lỗi/ngoại lệ -> kết luận & hậu quả pháp lý" in prompt
    assert "2730 tokens" in prompt and "Dữ kiện/văn bản còn thiếu" in prompt


# ── 6. Câu 1 vẫn qua admission dưới $0.01 với cấu hình nâng cap, và đồng bộ cấu hình ───────────────────────────────

PRODUCTION = {
    "LLM_PROVIDER": "openrouter", "OPENROUTER_MODEL": "deepseek/deepseek-v4.1-flash", "OPENROUTER_AUX_MODEL": "deepseek/deepseek-v4.1-flash",
    "OPENROUTER_REASONING_MAX_TOKENS": "0", "LLM_AUX_REASONING_MAX_TOKENS": "0", "OPENROUTER_MAX_RETRIES": "0", "LLM_BYPASS": "false",
    "CASE_COST_BUDGET_MICRO_USD": "10000", "CASE_TOKEN_BUDGET_MAX": "0", "CASE_TOKEN_BUDGET_TARGET": "0",
    "DAILY_TOKEN_QUOTA_ENABLED": "true", "DAILY_TOKEN_LIMIT": "20000", "PLANNER_MAX_OUTPUT_TOKENS": "896",
    "CASE_COMPLEX_MAX_OUTPUT_TOKENS": "3500", "CASE_MAX_OUTPUT_TOKENS": "3500", "CASE_TOTAL_DEADLINE_SECONDS": "90",
    "ADAPTIVE_RAG_ENABLED": "true", "RELEVANCE_CHECK": "true", "CASE_RELEVANCE_DEDUP_ENABLED": "true", "QUERY_REWRITE_ENABLED": "false",
    "CASE_MAX_ISSUES": "8", "CASE_MAX_RETRIEVAL_QUERIES": "8", "CASE_MAX_RETRIEVAL_ROUNDS": "2", "CASE_RETRIEVAL_CONCURRENCY": "3",
    "CASE_CANDIDATE_K_PER_ISSUE": "12", "CASE_MAX_CANDIDATES": "48", "CASE_SELECT_MAX": "16", "RESPONDER_INPUT_TARGET": "8000",
    "CASE_CONTEXT_TOKEN_BUDGET": "6000", "RELEVANCE_TOTAL_INPUT_TOKENS": "3600", "RELEVANCE_TOTAL_OUTPUT_TOKENS": "320",
    "EMBEDDING_TOTAL_INPUT_TOKENS": "1024", "RELEVANCE_CHUNK_CHARS": "800", "EMBEDDING_PRICE_NANO_PER_TOKEN": "150",
}
RAG_EMBEDDING = {"EMBEDDING_PROVIDER": "openrouter", "OPENROUTER_EMBEDDING_MODEL": "google/gemini-embedding-001",
                 "EMBEDDING_MODEL": "gemini-embedding-001"}
RAG_ONLINE = {"EMBEDDING_ONLINE_MAX_RETRIES": "0", "EMBEDDING_PRICE_NANO_PER_TOKEN": "150"}


def test_cau_1_still_passes_admission_under_one_cent_with_the_raised_caps(monkeypatch):
    from tests.test_cost_gate_and_budget import CAU_1
    env(monkeypatch, **{**PRODUCTION, "OPENROUTER_API_KEY": "k"})
    settings = get_settings()
    assert (settings.planner_max_output_tokens, settings.case_complex_max_output_tokens, settings.case_token_budget_max) == (896, 3500, 0)
    decision = admission.admit(CAU_1, [])
    assert decision.branch == "complex" and decision.floor_nano <= 10_000_000
    assert decision.budget.max_tokens == 0 and decision.budget.enabled
    assert decision.responder_output_cap >= 1600 + 225 * decision.issue_groups
    assert decision.min_quota_tokens <= settings.daily_token_limit


def _norm(value):
    if isinstance(value, float):
        return f"{value:g}"
    return str(value).strip().lower() if str(value).lower() in ("true", "false") else str(value).strip()


def test_config_py_defaults_match_the_standard_profile(monkeypatch):
    for key in PRODUCTION:
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    for key, expected in PRODUCTION.items():
        assert _norm(getattr(settings, key.lower())) == expected, key


def _compose_env(service):
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load((BE_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    out = {}
    for key, value in data["services"][service]["environment"].items():
        match = re.fullmatch(r"\$\{[A-Z0-9_]+:-(.*)\}", str(value))
        out[key] = match.group(1) if match else str(value)
    return out


def _render_env(service):
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load((BE_ROOT / "render.yaml").read_text(encoding="utf-8"))
    entry = next(s for s in data["services"] if s["name"] == service)
    return {e["key"]: str(e["value"]) for e in entry["envVars"] if "value" in e}


def test_compose_and_render_agent_env_match_the_standard_profile():
    for source in (_compose_env("agent-service"), _render_env("ailegal-agent-service")):
        for key, expected in PRODUCTION.items():
            assert _norm(source.get(key)) == expected, key


def test_online_embedding_route_is_the_same_on_compose_and_render():
    compose, render = _compose_env("rag-service"), _render_env("ailegal-rag-service")
    for source in (compose, render):
        for key, expected in {**RAG_EMBEDDING, **RAG_ONLINE}.items():
            assert source.get(key) == expected, key
    worker = _compose_env("rag-worker")
    assert all(worker.get(k) == v for k, v in RAG_EMBEDDING.items())
