import asyncio
import json
import time

import httpx
import pytest

from app.core.config import get_settings
from app.services import qa_agent, query_plan, rag_client
from app.services.case_budget import CaseBudget, estimate_text_tokens

CAP = 10_000_000
QUERIES = [("I1", "Điều kiện hoàn trả tiền đặt cọc"), ("I2", "Thời hạn yêu cầu bồi thường")]


def batch_response(body, rows, **usage):
    return httpx.Response(200, json={"data": {
        "items": rows,
        "embedding_usage": {"request_turn_id": body["request_turn_id"], "prompt_tokens": 5, "cost_nano_usd": 750, **usage},
    }})


def search_many(handler, queries=QUERIES, **kwargs):
    return asyncio.run(rag_client.search_many(queries, transport=httpx.MockTransport(handler), **kwargs))


def test_batch_reserves_and_settles_once_for_cache_misses(chunk, monkeypatch):
    monkeypatch.setenv("RAG_CACHE_ENABLED", "true")
    monkeypatch.setenv("CACHE_BACKEND", "memory")
    get_settings.cache_clear()
    rag_client.clear_cache()
    budget = CaseBudget(max_cost_nano_usd=CAP)
    seen = []
    settlements = []
    settle = budget.settle_embedding

    def track_settlement(reservation, usage=None, **kwargs):
        settlements.append((reservation, usage))
        return settle(reservation, usage, **kwargs)

    def handler(request):
        assert request.url.path == "/internal/v1/rag/search/batch"
        assert request.headers["X-Request-ID"] == "req-p1"
        assert request.headers["X-Internal-Token"] == "test-internal-token"
        body = json.loads(request.content)
        seen.append(body)
        assert 100 <= body["deadline_ms"] < 2500
        items = body["items"]
        assert [item["embedding_token_budget"] for item in items] == [estimate_text_tokens(q) for _, q in QUERIES]
        assert budget.in_flight_embedding_tokens == sum(item["embedding_token_budget"] for item in items)
        assert budget.in_flight_cost_nano_usd == budget.in_flight_embedding_tokens * 150
        return batch_response(body, [
            {"item_id": item["item_id"], "status": "ok", "chunks": [{**chunk, "id": item["item_id"]}]}
            for item in reversed(items)
        ])

    monkeypatch.setattr(budget, "settle_embedding", track_settlement)
    try:
        kwargs = {"request_id": "req-p1", "deadline": time.monotonic() + 2.5, "budget": budget}
        groups = search_many(handler, **kwargs)
        assert [rows[0]["id"] for rows in groups] == ["1", "2"]
        assert [rows[0]["matched_issue_ids"] for rows in groups] == [["I1"], ["I2"]]
        assert not groups.degraded and search_many(handler, **kwargs) == groups
        assert len(seen) == len(settlements) == len(budget.ledger) == 1
        assert budget.ledger[0]["request_turn_id"] == seen[0]["request_turn_id"]
        assert budget.confirmed_cost_nano_usd == 750 and budget.confirmed_embedding_tokens == 5
        assert budget.in_flight_cost_nano_usd == budget.unsettled_cost_nano_usd == 0
    finally:
        rag_client.clear_cache()


@pytest.mark.parametrize("failure", ["uncertain", "timeout", "500", "429", "overloaded"])
def test_batch_reservation_and_breaker_on_failure(monkeypatch, failure):
    budget = CaseBudget(max_cost_nano_usd=CAP)
    monkeypatch.setattr(rag_client, "_fails", 1)
    reserved = []
    settlements = []
    settle = budget.settle_embedding

    def track_settlement(reservation, usage=None, **kwargs):
        settlements.append((reservation, usage, kwargs))
        return settle(reservation, usage, **kwargs)

    def handler(request):
        body = json.loads(request.content)
        reserved.append(budget.in_flight_cost_nano_usd)
        if failure == "timeout":
            raise httpx.ReadTimeout("request was sent", request=request)
        if failure == "overloaded":
            return httpx.Response(503, json={"error": {"code": "RAG_OVERLOADED"}})
        if failure == "uncertain":
            return batch_response(body, [
                {"item_id": item["item_id"], "status": "degraded", "error_code": "EMBEDDING_SERVICE_UNAVAILABLE"}
                for item in body["items"]
            ], usage_uncertain=True, prompt_tokens=0, cost_nano_usd=0)
        return httpx.Response(int(failure), json={"error": {"code": "UNAVAILABLE"}})

    monkeypatch.setattr(budget, "settle_embedding", track_settlement)
    groups = search_many(handler, budget=budget)
    assert groups == [[], []] and groups.degraded
    assert len(reserved) == len(settlements) == 1 and reserved[0] > 0
    assert budget.in_flight_embedding_tokens == 0 and budget.in_flight_cost_nano_usd == 0
    assert budget.confirmed_cost_nano_usd == 0
    if failure == "overloaded":
        assert settlements[0][2] == {"sent": False}
        assert budget.ledger_total_nano == budget.embedding_attempts == 0
        assert rag_client._fails == 1 and not rag_client._breaker_open()
    else:
        assert budget.unsettled_cost_nano_usd == reserved[0]
        assert budget.ledger_total_nano <= CAP
        if failure in {"timeout", "500", "uncertain"}:
            assert rag_client._fails == 2


def test_embedding_usage_over_reserve_keeps_hold_and_closes_budget():
    budget = CaseBudget(max_cost_nano_usd=CAP, responder_reserved_output=0)
    reservation = budget.reserve_embedding(10)
    usage = {"prompt_tokens": 11, "cost_nano_usd": 1650}
    budget.settle_embedding(reservation, usage)
    budget.settle_embedding(reservation, usage)
    assert budget.exceeded and budget.reserve_embedding(1) is None
    assert budget.confirmed_cost_nano_usd == 0 and budget.unsettled_cost_nano_usd == 1500
    assert len(budget.ledger) == 1


def test_single_cache_distinguishes_all_retrieval_options(monkeypatch, chunk):
    monkeypatch.setenv("RAG_CACHE_ENABLED", "true")
    monkeypatch.setenv("CACHE_BACKEND", "memory")
    get_settings.cache_clear()
    rag_client.clear_cache()
    version = ["0"]
    monkeypatch.setattr(rag_client.cache, "get_corpus_version", lambda: version[0])
    refs = [{"doc_ref": "BLDS", "article": "328"}, {"doc_ref": "LDD", "article": "27"}]
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"data": {"chunks": [{**chunk, "id": str(len(requests))}], "embedding_usage": {
            "prompt_tokens": 1, "cost_nano_usd": 150,
        }}})

    transport = httpx.MockTransport(handler)
    baseline = {"expand_adjacent_clauses": True, "follow_citations": True, "exact_refs": refs,
                "doc_types": ["luat", "nghi_dinh"], "embedding_tokens": 40}

    async def query(text="Điều kiện đặt cọc?", **changes):
        budget = CaseBudget(max_cost_nano_usd=CAP)
        return (await rag_client.search(text, transport=transport, budget=budget, **{**baseline, **changes}))[0]["id"]

    async def run():
        assert await query() == "1"
        assert await query("  ĐIỀU KIỆN   ĐẶT CỌC!", doc_types=["NGHI_DINH", "luat", "LUAT"]) == "1"
        assert await query(expand_adjacent_clauses=False) == "2"
        assert await query(follow_citations=False) == "3"
        assert await query(exact_refs=list(reversed(refs))) == "4"
        assert await query(exact_refs=[*refs, {"article": "2"}]) == "5"
        assert await query(embedding_tokens=39) == "6"
        assert await query(planned=True) == "7"
        assert await query(top_k=6) == "8"
        version[0] = "1"
        assert await query() == "9"
        monkeypatch.setattr(rag_client, "_RETRIEVAL_PIPELINE_VERSION", "next")
        assert await query() == "10"

    try:
        asyncio.run(run())
        assert requests[1]["expand_adjacent_clauses"] is False
        assert requests[2]["follow_citations"] is False
    finally:
        rag_client.clear_cache()


@pytest.mark.parametrize("status,item_usage", [
    ("ok", {"budget_capped": True}),
    ("ok", {"skipped_reason": "embedding_failed"}),
    ("degraded", {}),
    ("overloaded", {}),
    ("error", {}),
])
def test_batch_degraded_or_capped_items_stay_out_of_cache(monkeypatch, chunk, status, item_usage):
    monkeypatch.setenv("RAG_CACHE_ENABLED", "true")
    monkeypatch.setenv("CACHE_BACKEND", "memory")
    get_settings.cache_clear()
    rag_client.clear_cache()
    seen = []

    def handler(request):
        body = json.loads(request.content)
        ids = [item["item_id"] for item in body["items"]]
        seen.append(ids)
        return batch_response(body, [
            {"item_id": item_id, "status": status if item_id == "1" else "ok", "chunks": [chunk],
             "embedding_usage": item_usage if item_id == "1" else {}}
            for item_id in ids
        ])

    try:
        first = search_many(handler)
        second = search_many(handler)
        assert first.degraded and second.degraded
        assert seen == [["1", "2"], ["1"]]
        assert first[1] == second[1] and first[1][0]["matched_issue_ids"] == ["I2"]
    finally:
        rag_client.clear_cache()


@pytest.mark.parametrize("fallback", [False, True])
def test_qa_marks_partial_retrieval_degraded(monkeypatch, chunk, fallback):
    monkeypatch.setenv("CASE_MAX_RETRIEVAL_ROUNDS", "1")
    get_settings.cache_clear()
    plan = query_plan.QueryPlan(issues=[
        query_plan.Issue(issue_id=issue, question=query, retrieval_query=query) for issue, query in QUERIES
    ])

    async def plan_request(*args, **kwargs):
        return plan

    monkeypatch.setattr(query_plan, "plan", plan_request)

    def handler(request):
        body = json.loads(request.content)
        assert len(body["items"]) == 2
        first, missing = body["items"]
        return batch_response(body, [
            {"item_id": first["item_id"], "status": "ok", "chunks": [chunk]},
            {"item_id": missing["item_id"], "status": "overloaded", "error_code": "DEADLINE_EXCEEDED"},
        ])

    if fallback:
        async def single(query, **kwargs):
            if query == QUERIES[1][1]:
                raise rag_client.RAGError("partial")
            return [chunk]

        monkeypatch.setattr(rag_client, "search", single)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            async def get_client(timeout):
                return client

            monkeypatch.setattr(rag_client, "_get_http_client", get_client)
            return await qa_agent._prepare_complex("Hai câu hỏi pháp lý", [], "req-p1", time.monotonic())

    call = asyncio.run(run())
    assert call.metadata_extra["retrieval_degraded"] is True
    assert call.metadata_extra["missing_issue_ids"] == ["I2"]
    assert call.chunks[0]["matched_issue_ids"] == ["I1"]
    assert call.chunks[0]["retrieval_provenance"][0]["issue_id"] == "I1"
