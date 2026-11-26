import asyncio
import hashlib

import httpx
import pytest

from app.core.config import get_settings
from app.services import contract_review, qa_agent, query_plan, rag_client, review_agent
from app.services.case_budget import CaseBudget


OWNER = "person@example.test"
EXPECTED_KEY = hashlib.sha256(OWNER.encode()).hexdigest()[:16]


def test_qa_routes_single_and_complex_with_same_anonymous_tenant(monkeypatch):
    monkeypatch.setenv("ADAPTIVE_RAG_ENABLED", "true")
    monkeypatch.setenv("RAG_BATCH_ENABLED", "true")
    monkeypatch.setenv("LLM_BYPASS", "false")
    monkeypatch.setenv("QUERY_REWRITE_ENABLED", "false")
    monkeypatch.setenv("CASE_MAX_RETRIEVAL_ROUNDS", "1")
    get_settings.cache_clear()
    calls = []

    def route(question):
        return "complex" if question.startswith("Complex") else "simple"

    async def plan(*args, **kwargs):
        return query_plan.QueryPlan(issues=[query_plan.Issue(
            issue_id="I1", question="Đặt cọc?", retrieval_query="tiền đặt cọc", source_text="Đặt cọc?", part_ids=["P1"],
        )])

    async def post(path, payload, request_id, transport):
        calls.append((path, payload, request_id))
        if path == "/internal/v1/rag/search":
            return httpx.Response(200, json={"data": {"chunks": []}})
        return httpx.Response(200, json={"data": {
            "items": [{"item_id": item["item_id"], "status": "ok", "chunks": []} for item in payload["items"]],
        }})

    monkeypatch.setattr(query_plan, "route", route)
    monkeypatch.setattr(query_plan, "plan", plan)
    monkeypatch.setattr(rag_client, "_post", post)

    async def run():
        await asyncio.gather(
            qa_agent.prepare("Điều kiện hoàn trả tiền đặt cọc?", [], "simple-turn", owner_scope=OWNER),
            qa_agent.prepare("Complex: khi nào phải hoàn trả tiền đặt cọc?", [], "complex-turn", owner_scope=OWNER),
        )

    asyncio.run(run())
    requests = {request_id: (path, payload) for path, payload, request_id in calls}
    assert set(requests) == {"simple-turn", "complex-turn"}
    for request_id, endpoint, priority in (
        ("simple-turn", "/internal/v1/rag/search", "interactive"),
        ("complex-turn", "/internal/v1/rag/search/batch", "batch"),
    ):
        path, payload = requests[request_id]
        assert path == endpoint
        assert payload["fairness_key"] == EXPECTED_KEY
        assert payload["priority"] == priority
        assert OWNER not in str(payload)


def test_review_batch_preserves_single_search_options_order_and_chunks(monkeypatch):
    queries = ["Tiền đặt cọc? Cần giấy tờ gì?", "Thời hạn hoàn trả tiền đặt cọc?"]
    chunk = {
        "id": "first", "content": "Điều về đặt cọc", "score": 0.94, "rank_sources": ["bm25", "vector"],
        "rrf_rank": 1, "effective_date": "2026-11-24",
        "retrieval_sources": [{"source": "lexical", "rank": 1}],
    }
    groups = {
        queries[0]: [chunk, {"id": "second", "content": "Hoàn trả", "score": 0.7}],
        queries[1]: [{"id": "third", "content": "Thời hạn", "score": 0.8}, {**chunk, "score": 0.6}],
    }
    calls = []

    async def post(path, payload, request_id, transport):
        calls.append((path, payload))
        if path == "/internal/v1/rag/search":
            return httpx.Response(200, json={"data": {"chunks": groups[payload["query"]]}})
        optional = {"rank_sources", "rrf_rank", "effective_date", "retrieval_sources", "document_version"}
        return httpx.Response(200, json={"data": {"items": [
            {"item_id": item["item_id"], "status": "ok",
             "chunks": [{**dict.fromkeys(optional), **row} for row in groups[item["query"]]]}
            for item in reversed(payload["items"])
        ]}})

    monkeypatch.setattr(rag_client, "_post", post)
    monkeypatch.setenv("RAG_BATCH_ENABLED", "false")
    get_settings.cache_clear()
    single = asyncio.run(review_agent.search_for_review(queries, "review-turn", owner_scope=OWNER))
    assert len(calls) == 2
    assert all(p == "/internal/v1/rag/search" and b["priority"] == "batch" and not b.get("planned")
               and b["fairness_key"] == EXPECTED_KEY for p, b in calls)

    calls.clear()
    monkeypatch.setenv("RAG_BATCH_ENABLED", "true")
    get_settings.cache_clear()
    batch = asyncio.run(review_agent.search_for_review(queries, "review-turn", owner_scope=OWNER))
    (path, body), = calls
    assert path == "/internal/v1/rag/search/batch"
    assert body["fairness_key"] == EXPECTED_KEY and body["priority"] == "batch"
    assert [item["query"] for item in body["items"]] == queries
    assert all(item["planned"] is False for item in body["items"])
    assert batch == single == contract_review.merge_chunks([groups[q] for q in queries])
    assert [row["id"] for row in batch] == ["first", "third", "second"]


def test_review_keeps_legacy_monkeypatched_single_search(monkeypatch):
    monkeypatch.setenv("RAG_BATCH_ENABLED", "true")
    get_settings.cache_clear()
    calls = []

    async def fake_search(query, top_k=5, request_id=None):
        calls.append((query, top_k, request_id))
        return [{"id": query}]

    monkeypatch.setattr(rag_client, "search", fake_search)
    queries = ["Quy định A", "Quy định B"]
    result = asyncio.run(review_agent.search_for_review(queries, "request-id", owner_scope=OWNER))
    assert result == [{"id": q} for q in queries]
    assert calls == [(q, get_settings().rag_top_k, "request-id") for q in queries]


@pytest.mark.parametrize("code", ["RAG_OVERLOADED", "RAG_RETRIEVAL_OVERLOADED", "DEADLINE_EXCEEDED"])
def test_single_scheduling_errors_keep_correct_embedding_reservation(code):
    budget = CaseBudget(max_cost_nano_usd=10_000_000)
    held = []

    def handler(request):
        held.append(budget.in_flight_cost_nano_usd)
        return httpx.Response(503, json={"error": {"code": code}})

    with pytest.raises(rag_client.RAGError):
        asyncio.run(rag_client.search(
            "Thời hạn hoàn trả tiền đặt cọc?", budget=budget, transport=httpx.MockTransport(handler),
        ))
    assert len(held) == 1 and held[0] > 0
    assert budget.in_flight_cost_nano_usd == 0
    assert budget.ledger_total_nano == (0 if code == "RAG_OVERLOADED" else held[0])
    assert budget.ledger_total_nano <= budget.max_cost_nano_usd
    assert rag_client._fails == 0
