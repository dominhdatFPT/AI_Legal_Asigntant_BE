"""Adaptive profile regression tests; no external providers or databases."""
import asyncio
import json
import time

import pytest

from app.core.config import Settings, get_settings
from app.services import evidence_selection as evidence
from app.services import llm_client, prompts, qa_agent, query_plan, rag_client, relevance, responder


@pytest.fixture(autouse=True)
def adaptive(monkeypatch):
    monkeypatch.setenv("ADAPTIVE_RAG_ENABLED", "true")
    monkeypatch.setenv("RELEVANCE_CHECK", "false")
    get_settings.cache_clear()
    query_plan._cache.clear()


def chunk(cid, issues, text=None, score=0.9):
    return {"id": cid, "content": text or f"Quy định {cid}", "matched_issue_ids": issues, "score": score,
            "document": {"title": "Luật Mẫu", "doc_number": "1/2024/QH15"}, "section_info": {"dieu": "1"}}


@pytest.mark.parametrize("text,expected", [
    ("Sang tên nhà cần giấy tờ gì?", "simple"),
    ("x" * 1199, "simple"), ("x" * 1200, "complex"),
    ("Câu 1: Đặt cọc? Câu 2: Phạt thế nào?", "complex"),
    ("1. Điều kiện chuyển nhượng\n2. Tiền phạt", "complex"),
    ("Tôi đặt cọc rồi thế chấp nhà", "complex"),
    ("Giao dịch năm 2020, tranh chấp năm 2024", "complex"),
    ("Ông A, bà B và công ty C có quyền gì", "complex"),
])
def test_router_full_input(text, expected):
    assert query_plan.route(text) == expected


def test_default_adaptive_configuration(monkeypatch):
    monkeypatch.delenv("ADAPTIVE_RAG_ENABLED")
    settings = Settings(_env_file=None)
    assert settings.adaptive_rag_enabled
    assert (settings.case_max_issues, settings.case_max_candidates, settings.case_context_token_budget) == (8, 48, 6000)


@pytest.mark.parametrize("outcome", ["not JSON", '{"facts": [], "issues": []}', "error", "timeout"])
def test_planner_fallback_covers_tail(monkeypatch, outcome):
    text = "Tình tiết dài. " * 160 + "\nCâu 1: Quyền đặt cọc?\nCâu 2: Hỏi cuối về phạt vi phạm?"

    async def generate(*args, **kwargs):
        if outcome == "error":
            raise llm_client.LLMError("down")
        if outcome == "timeout":
            raise TimeoutError()
        return outcome, 7, "planner"

    monkeypatch.setattr(llm_client, "generate", generate)
    info = {}
    result = asyncio.run(query_plan.plan(text, [], info=info))
    assert info["fallback"]
    assert "Hỏi cuối" in " ".join(i.retrieval_query for i in result.issues)
    assert "Tình tiết dài" in result.facts[0]


def test_fallback_groups_without_dropping_source():
    text = "\n".join(f"Câu {n}: nội dung riêng {n}" for n in range(1, 31))
    result = query_plan.fallback(text, max_issues=3)
    assert len(result.issues) == 3
    combined = "\n".join(i.source_text for i in result.issues)
    assert all(f"nội dung riêng {n}" in combined for n in range(1, 31))


def test_valid_planner_and_missing_numbered_question(monkeypatch):
    text = "Câu 1: Mua nhà?\nCâu 2: Thuế gì?"
    data = {"facts": ["mua nhà"], "issues": [
        {"issue_id": "I1", "question": "mua nhà", "retrieval_query": "điều kiện mua nhà", "source_text": text},
    ]}

    async def generate(*args, **kwargs):
        return json.dumps(data), 11, "planner"

    monkeypatch.setattr(llm_client, "generate", generate)
    info = {}
    assert asyncio.run(query_plan.plan(text, [], info=info)).issues[0].retrieval_query == "điều kiện mua nhà"
    assert info["tokens"] == 11
    query_plan._cache.clear()
    data["issues"][0]["source_text"] = "Câu 1: Mua nhà?"
    result = asyncio.run(query_plan.plan(text, [], info=info))
    assert result.issues[0].retrieval_query == "điều kiện mua nhà"
    assert any("Thuế" in i.question for i in result.issues)


def test_search_many_concurrency_provenance_and_cache_safety(monkeypatch):
    state = {"active": 0, "peak": 0}
    original = chunk("shared", [])

    async def search(query, **kwargs):
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        try:
            await asyncio.sleep(0.001)
            if query == "bad":
                raise rag_client.RAGError("down")
            return [original]
        finally:
            state["active"] -= 1

    monkeypatch.setattr(rag_client, "search", search)
    groups = asyncio.run(rag_client.search_many([(f"I{i}", str(i)) for i in range(8)] + [("I9", "bad")]))
    assert state["peak"] == 3
    assert groups[-1] == [] and groups[0][0]["matched_issue_ids"] == ["I0"]
    assert groups[0][0]["retrieval_provenance"][0]["rank"] == 1
    assert original["matched_issue_ids"] == []
    with pytest.raises(rag_client.RAGError):
        asyncio.run(rag_client.search_many([("I1", "bad")]))


def test_selection_coverage_token_budget_and_duplicates():
    rows = [chunk("a", ["I1"], score=1), chunk("a2", ["I1"], score=0.99),
            chunk("b", ["I2"], score=0.2), chunk("b-copy", ["I3"], text="Quy định b")]
    budget = sum(evidence.token_cost(prompts.format_case_chunk(i, c) + "\n\n") for i, c in enumerate([rows[0], rows[2]], 1))
    result = evidence.select(rows, ["I1", "I2", "I3"], budget, 2)
    assert {c["id"] for c in result.chunks} == {"a", "b"}
    assert result.missing_issue_ids == [] and result.tokens <= budget
    small = evidence.select(rows, ["I1", "I2"], 1, 32)
    assert small.chunks == [] and small.missing_issue_ids == ["I1", "I2"]
    assert rows[2]["matched_issue_ids"] == ["I2"]


def test_round_robin_candidates_preserve_issue_and_merge_provenance():
    result = evidence.merge_candidates([
        [chunk("shared", ["I1"]), chunk("a", ["I1"])],
        [chunk("shared", ["I2"]), chunk("b", ["I2"])],
    ], 3)
    assert result[0]["matched_issue_ids"] == ["I1", "I2"]
    assert len(result) == 3


def test_smart_preview_finds_rule_at_end():
    text = "Nội dung tổng quát. " * 300 + "Ngoại lệ đặt cọc: giới hạn năm phần trăm."
    preview = prompts.evidence_preview(text, "giới hạn đặt cọc ngoại lệ", 800)
    assert "năm phần trăm" in preview and len(preview) <= 800


def test_grading_issue_batches_and_fallback(monkeypatch):
    issues = query_plan.fallback("Câu 1: Nhà?\nCâu 2: Thuế?").issues
    calls = []

    async def grade(question, rows, request_id=None, info=None):
        calls.append((question, len(rows)))
        info["tokens"] = 3
        return None if "Thuế" in question else rows[:1]

    monkeypatch.setattr(relevance, "grade", grade)
    rows = [chunk(str(i), ["I1"]) for i in range(13)] + [chunk("tax", ["I2"])]
    info = {}
    kept = asyncio.run(relevance.grade_issues(issues, rows, info=info))
    assert sorted(n for _, n in calls) == [1, 1, 12]
    assert len(kept) == 3 and info == {"tokens": 9, "degraded": True}


PARTIAL_ANSWER = (
    "## Kết luận ngắn và căn cứ chính\n- Nhà: theo [S1] được chuyển nhượng.\n\n"
    "## Vấn đề 1: Nhà\nQuy tắc [S1]; đối chiếu tình tiết; kết luận: được chuyển nhượng.\n\n"
    "## Vấn đề 2: Thuế\nQuy tắc [S2] áp dụng, nhưng ngoại lệ bị cắt"
)


def test_adaptive_pipeline_second_round_usage_and_prompt(monkeypatch):
    monkeypatch.setenv("RELEVANCE_CHECK", "true")
    get_settings.cache_clear()
    calls = []

    async def plan(text, history, request_id=None, info=None):
        info["tokens"] = 10
        return query_plan.fallback(text)

    async def search(query, **kwargs):
        calls.append(query)
        if "Thuế" in query and calls.count(query) == 1 and "quy định" not in query:
            return []
        return [chunk("tax" if "Thuế" in query else "house", [])]

    async def grade(question, rows, request_id=None, info=None):
        info["tokens"] = 5
        return rows

    async def generate(*args, **kwargs):
        assert kwargs["max_output_tokens"] == 3500
        return llm_client.Completion((PARTIAL_ANSWER, 100, "answer"), "length")

    monkeypatch.setattr(query_plan, "plan", plan)
    monkeypatch.setattr(rag_client, "search", search)
    monkeypatch.setattr(relevance, "grade", grade)
    monkeypatch.setattr(llm_client, "generate", generate)
    prepared = asyncio.run(qa_agent.prepare("Câu 1: Nhà?\nCâu 2: Thuế?", []))
    assert prepared.metadata_extra["retrieval_rounds"] == 2
    assert prepared.metadata_extra["missing_issue_ids"] == []
    assert "[S1]" in prepared.system_prompt and "Dữ kiện/văn bản còn thiếu" in prepared.system_prompt
    result = asyncio.run(prepared.run())
    assert result["metadata"]["tokens_used"] == 120
    metadata = result["metadata"]
    assert metadata["answer_mode"] == "partial_grounded" and metadata["truncated"] and metadata["finish_reason"] == "length"
    assert "Vấn đề 1" in result["content"] and "Vấn đề 2" not in result["content"] and "bị cắt" not in result["content"]
    assert metadata["missing_facets"] and metadata["missing_facets"][0].startswith("I2")


def test_simple_keeps_fast_path(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("simple must not invoke planner")

    async def search(*args, **kwargs):
        return []

    monkeypatch.setattr(query_plan, "plan", forbidden)
    monkeypatch.setattr(rag_client, "search", search)
    prepared = asyncio.run(qa_agent.prepare("Sang tên nhà cần gì?", []))
    assert prepared.llm_options == {} and "Tổng dưới 200 chữ" in prepared.system_prompt


def test_total_deadline_stops_planner(monkeypatch):
    monkeypatch.setenv("CASE_TOTAL_DEADLINE_SECONDS", "0.01")
    get_settings.cache_clear()

    async def plan(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(query_plan, "plan", plan)
    prepared = asyncio.run(qa_agent.prepare("Câu 1: Nhà?\nCâu 2: Thuế?", []))
    with pytest.raises(llm_client.LLMError):  # hết giờ là LỖI, không phải bản tóm tắt mẫu
        asyncio.run(prepared.run())


@pytest.mark.parametrize("wrapper", [lambda x: x, lambda x: '```json\n' + x + '\n```',
                                   lambda x: x + '\n' + json.dumps({"facts": ["bổ sung"]})])
def test_compact_parser_and_repair(wrapper):
    parts = query_plan.build_source_parts("Câu 1: Mua nhà?\nCâu 2: Thuế gì?", [])
    raw = json.dumps({"facts": [], "issues": [{"s": "I1", "p": ["P1"], "q": "Mua nhà", "r": "mua nhà"}]})
    result = query_plan._parse_query_plan(wrapper(raw), parts, 12, None)
    assert result.issues[0].retrieval_query == "mua nhà"
    assert any("Thuế" in i.question for i in result.issues)


def test_planner_cache_hit(monkeypatch):
    calls = []
    async def generate(*args, **kwargs):
        calls.append(1)
        return json.dumps({"issues": [{"s": "I1", "p": ["P1"], "q": "mua nhà", "r": "điều kiện mua nhà"}]}), 1, "model"
    monkeypatch.setattr(llm_client, "generate", generate)
    asyncio.run(query_plan.plan("Câu 1: Mua nhà?", []))
    info = {}
    asyncio.run(query_plan.plan("Câu 1: Mua nhà?", [], info=info))
    assert len(calls) == 1 and info["planner_status"] == "cache_hit"


def test_answer_deadline_is_shared():
    call = responder.LLMCall("system", [], [], "q", None, time.perf_counter(), time.perf_counter(), deadline=time.monotonic() - 1)
    assert call.remaining_seconds() == 0
    with pytest.raises(llm_client.LLMError):
        asyncio.run(call.run())


def test_max_input_fallback_with_one_issue_preserves_tail(monkeypatch):
    monkeypatch.setenv("CASE_MAX_ISSUES", "1")
    get_settings.cache_clear()
    text = "a" * 19980 + " IMPORTANT TAIL RULE"
    result = query_plan.fallback(text)
    assert len(result.issues) == 1 and result.issues[0].retrieval_query == text


def test_selection_rejects_oversized_chunks_and_reports_missing():
    rows = [chunk("large", ["I1"], text="x" * 10000), chunk("small", ["I2"])]
    result = evidence.select(rows, ["I1", "I2"], 1000, 32)
    assert [c["id"] for c in result.chunks] == ["small"]
    assert result.tokens <= 1000 and result.missing_issue_ids == ["I1"]


def test_query_budget_and_supplement_failure_preserve_available_evidence(monkeypatch):
    calls = []

    async def plan(text, history, request_id=None, info=None):
        return query_plan.fallback(text)

    async def search(query, **kwargs):
        calls.append(query)
        if "Thuế" in query:
            if "quy định" in query:
                raise rag_client.RAGError("supplement down")
            return []
        return [chunk("house", [])]

    monkeypatch.setattr(query_plan, "plan", plan)
    monkeypatch.setattr(rag_client, "search", search)
    result = asyncio.run(qa_agent.prepare("Câu 1: Nhà?\nCâu 2: Thuế?", []))
    assert [c["id"] for c in result.chunks] == ["house"]
    assert result.metadata_extra["retrieval_degraded"]
    assert result.metadata_extra["missing_issue_ids"] == ["I2"]
    assert result.metadata_extra["retrieval_query_count"] == len(calls) == 3

    monkeypatch.setenv("CASE_MAX_RETRIEVAL_QUERIES", "2")
    get_settings.cache_clear()
    calls.clear()
    result = asyncio.run(qa_agent.prepare("Câu 1: Nhà?\nCâu 2: Thuế?", []))
    assert len(calls) == 2 and result.metadata_extra["retrieval_rounds"] == 1


def test_followup_summary_request_skips_rag_and_uses_history(monkeypatch):
    async def fail_search(*args, **kwargs):
        raise AssertionError("RAG search should not be called for summary follow-up")

    monkeypatch.setattr(rag_client, "search", fail_search)
    history = [
        {"role": "user", "content": "Phân tích chuyển nhượng dự án BĐS"},
        {"role": "assistant", "content": "1. Kết luận sơ bộ..."},
    ]
    prepared = asyncio.run(
        qa_agent.prepare("Tóm tắt trong 1800 từ với nội dung cốt lõi và chính xác nhất, không cần liệt kê các bộ luật và nguồn ra", history)
    )
    assert isinstance(prepared, qa_agent.responder.LLMCall)
    assert prepared.metadata_extra["profile"] == "followup_summary"
    assert prepared.system_prompt == prompts.FOLLOWUP_SYSTEM_PROMPT
    assert prepared.history == history


def test_context_budget_rejects_values_above_limit():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings(_env_file=None, case_context_token_budget=40001)


def test_planner_bad_metadata_is_isolated_and_questions_are_distinct():
    parts = query_plan.build_source_parts("Câu 1: Quyền chuyển nhượng nhà thế nào?\nCâu 2: Quyền chuyển nhượng nhà bị hạn chế khi nào?", [])
    raw = json.dumps({"facts": None, "issues": [
        {"s": "bad", "p": [["P1"]], "q": "bad", "r": "bad", "event_refs": [{}]},
        {"s": "I1", "p": ["P1"], "q": parts[0].text, "r": "x" * 19990, "event_refs": [{}]},
    ]})
    result = query_plan._parse_query_plan(raw, parts, 1)
    assert result.facts == []
    assert not query_plan._covers_part(query_plan.Issue(issue_id="x", question=parts[0].text, retrieval_query="x"), parts[1])
    assert parts[1].text in result.issues[0].retrieval_query
    assert parts[1].text in result.issues[0].source_text


def test_planner_repairs_unrelated_assigned_part_without_duplicate_id():
    part = query_plan.SourcePart("P1", "request", "Thuế chuyển nhượng", 0, 19)
    raw = json.dumps({"issues": [{"s": "I1", "p": ["P1"],
                                  "q": "Thừa kế tài sản", "r": "thừa kế"}]})
    result = query_plan._parse_query_plan(raw, [part], 1)
    assert part.text in result.issues[0].retrieval_query
    assert result.issues[0].part_ids == ["P1"]


def test_planner_preserves_both_sides_when_query_exceeds_limit():
    part = query_plan.SourcePart("P1", "request", "m" * 15000, 0, 15000)
    raw = json.dumps({"issues": [{"s": "I1", "p": ["P1"],
                                  "q": "unrelated", "r": "o" * 15000}]})
    result = query_plan._parse_query_plan(raw, [part], 1)
    query = result.issues[0].retrieval_query
    assert len(query) <= 20000
    assert query.startswith("o" * 9999)
    assert query.endswith("m" * 10000)
    assert result.issues[0].part_ids == ["P1"]


def test_complex_answer_provider_error_is_an_error_not_a_template(monkeypatch):
    async def fail(*args, **kwargs):
        raise llm_client.LLMError("provider disconnected")
    monkeypatch.setattr(llm_client, "generate", fail)
    call = responder.LLMCall("system", [{"id": "c1"}], [], "q", None, time.perf_counter(), time.perf_counter(),
                             metadata_extra={"profile": "complex", "issues": []})
    with pytest.raises(llm_client.LLMError):
        asyncio.run(call.run())


@pytest.mark.parametrize("failure", ["error", "timeout"])
def test_complex_stream_failure_emits_error_and_saves_nothing(monkeypatch, failure):
    from types import SimpleNamespace
    from datetime import datetime, timezone
    from app.api.v1 import conversations

    async def stream(*args, info, **kwargs):
        yield "partial answer"
        raise llm_client.LLMError("disconnected") if failure == "error" else TimeoutError()

    saved = []

    async def threadpool(fn, *args, **kwargs):
        saved.append(args)
        return object(), object()
    monkeypatch.setattr(llm_client, "generate_stream", stream)
    monkeypatch.setattr(conversations, "run_in_threadpool", threadpool)
    call = responder.LLMCall("system", [{"id": "c1"}], [], "q", None, time.perf_counter(), time.perf_counter(),
                             metadata_extra={"profile": "complex", "issues": []})

    async def collect():
        return [event async for event in conversations._stream_events(
            None, SimpleNamespace(user_id="u"), SimpleNamespace(content="q", document_ids=[]),
            "id", datetime.now(timezone.utc), call, None)]
    events = asyncio.run(collect())
    assert len(events) == 1 and events[0].startswith("event: error") and "partial answer" not in events[0]
    assert not saved


@pytest.mark.parametrize("failure", ["length"])
def test_complex_stream_falls_back_and_saves(monkeypatch, failure):
    from types import SimpleNamespace
    from datetime import datetime, timezone
    from app.api.v1 import conversations

    async def stream(*args, info, **kwargs):
        yield "partial answer"
        if failure == "error":
            raise llm_client.LLMError("disconnected")
        if failure == "timeout":
            raise TimeoutError()
        info.update(finish_reason="length", truncated=True)

    saved = []
    async def threadpool(fn, *args, **kwargs):
        saved.append(args[3])
        return object(), object()
    monkeypatch.setattr(llm_client, "generate_stream", stream)
    monkeypatch.setattr(conversations, "run_in_threadpool", threadpool)
    monkeypatch.setattr(conversations.quota, "add_daily_tokens", lambda *a, **k: None)
    monkeypatch.setattr(conversations.MessageOut, "model_validate", lambda value: {})
    monkeypatch.setattr(conversations, "SendMessageResponse", lambda **kwargs: kwargs)
    call = responder.LLMCall("system", [{"id": "c1"}], [], "q", None, time.perf_counter(), time.perf_counter(),
                             metadata_extra={"profile": "complex", "issues": []})
    async def collect():
        return [event async for event in conversations._stream_events(
            None, SimpleNamespace(user_id="u"), SimpleNamespace(content="q", document_ids=[]),
            "id", datetime.now(timezone.utc), call, None)]
    events = asyncio.run(collect())
    assert len(events) == 2 and events[0].startswith("event: delta") and events[1].startswith("event: done")
    assert "partial answer" not in events[0]
    assert saved and saved[0] == json.loads(events[0].split("data: ", 1)[1])["text"]
