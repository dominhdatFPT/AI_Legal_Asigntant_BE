"""Live Câu 1 calibration hotfix: byte/token bound, truncated planner salvage, bare headings, degraded-grader fallback."""
import asyncio
import json
import time

from app.core.config import get_settings
from app.services import admission, evidence_selection, qa_agent, query_plan, rag_client, relevance, responder
from app.services.query_plan import SourcePart
from tests.test_phase2b_quality import PRODUCTION, env


def test_cau_1_admits_with_calibrated_byte_bound(monkeypatch):
    from tests.test_cost_gate_and_budget import CAU_1
    env(monkeypatch, **{**PRODUCTION, "OPENROUTER_API_KEY": "k"})
    decision = admission.admit(CAU_1, [], subject="user-1", limit_override=20000)
    assert decision.min_quota_tokens <= 20000 and decision.floor_nano <= 10_000_000
    assert decision.responder_output_cap == 3500
    # the live Câu 1 (~2.246 ký tự, 4 câu hỏi) is not in the repo: CAU_1 padded to that length stands in for it
    longer = admission.admit(CAU_1 + " " + CAU_1[:391], [], subject="user-1", limit_override=20000)
    assert longer.min_quota_tokens <= 20000 and longer.floor_nano <= 10_000_000 and longer.responder_output_cap >= 3400


def _parts(n):
    return [SourcePart(f"P{i}", "request", f"Đặt cọc đất số {i}", 0, 1) for i in range(1, n + 1)]


def test_truncated_planner_json_keeps_complete_issue_rows():
    row = lambda i: {"s": f"I{i}", "p": [f"P{i}"], "q": "Đặt cọc", "r": "hiệu lực đặt cọc", "x": [{"doc_ref": "BLDS", "article": "328"}]}
    raw = json.dumps({"issues": [row(1), row(2)]})[:-2] + ',{"s":"I3","p":["P3"],"q":"Lã'  # cut mid-third row
    plan = query_plan._parse_query_plan(raw, _parts(3), 8, "length", [])
    assert [i.issue_id for i in plan.issues[:2]] == ["I1", "I2"]
    assert plan.issues[0].exact_refs == [{"doc_ref": "BLDS", "article": "328"}]  # nested "x" dict is not mistaken for an issue
    assert any(i.part_ids == ["P3"] for i in plan.issues)  # uncovered part repaired locally


def test_bare_heading_is_not_a_standalone_issue():
    text = ("Ông A đặt cọc 500 triệu đồng mua đất của bà B.\n\nBà B không chuyển nhượng đúng hạn.\n\n"
            "Các vấn đề pháp lý cần giải quyết:\n\nHợp đồng đặt cọc có hiệu lực không? Bà B có phải phạt cọc không?")
    parts = query_plan.source_parts(text)
    assert not any(p.strip() == "Các vấn đề pháp lý cần giải quyết:" for p in parts)
    assert any(p.startswith("Các vấn đề pháp lý cần giải quyết:") and "hiệu lực" in p for p in parts)
    assert all(not i.question.strip().endswith(":") for i in query_plan.fallback(text).issues)


def test_degraded_grader_falls_back_to_ungraded_chunks_not_zero(monkeypatch):
    env(monkeypatch, **{**PRODUCTION, "OPENROUTER_API_KEY": "k"})
    chunk = {"id": "c1", "content": "Điều 328 đặt cọc", "document": {"title": "BLDS"}, "matched_issue_ids": ["I1"]}
    issue = query_plan.Issue(issue_id="I1", question="Đặt cọc?", retrieval_query="đặt cọc", source_text="Đặt cọc?", part_ids=["P1"])

    async def plan(*a, **k):
        return query_plan.QueryPlan(issues=[issue])

    async def search_many(queries, *a, **k):
        return [[dict(chunk)] for _ in queries]

    async def grade(issues, chunks, request_id=None, info=None, **k):  # whole batch failed: ungraded, NOT rejected
        info.update(tokens=0, degraded=True)
        return [{**c, "confirmed_issue_ids": [], "relevance_confirmed": False, "grading_degraded": True} for c in chunks]

    monkeypatch.setattr(query_plan, "plan", plan)
    monkeypatch.setattr(rag_client, "search_many", search_many)
    monkeypatch.setattr(relevance, "grade_candidates_dedup", grade)
    call = asyncio.run(qa_agent._prepare_complex("Đặt cọc?", [], "r", time.perf_counter()))
    assert [c["id"] for c in call.chunks] == ["c1"] and call.metadata_extra["evidence_unconfirmed"] is True
    assert call.metadata_extra["grading_degraded"] and not call.no_evidence


def test_complex_call_without_any_chunk_skips_responder(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("responder must not be called")
    monkeypatch.setattr(responder.llm_client, "generate", boom)
    call = responder.LLMCall("s", [], [], "q", None, time.perf_counter(), time.perf_counter(),
                             metadata_extra={"profile": "complex", "issues": []})
    assert asyncio.run(call.run())["metadata"]["answer_mode"] == "insufficient_evidence"
