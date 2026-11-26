import asyncio
import json
import threading
import time
from datetime import date
from uuid import UUID

import httpx
import pytest
from sqlalchemy import URL, create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app.api.v1.internal import embedder_dep
from app.core.config import get_settings
from app.core.database import Base, get_db
from app.main import app
from app.models import DocumentChunk, LegalDocument
from app.services import hybrid, search
from app.services.embedding import OpenRouterEmbedder

SINGLE = "/internal/v1/rag/search"
BATCH = f"{SINGLE}/batch"
TOKEN = {"X-Internal-Token": "test-internal-token"}
MODEL = "gemini-embedding-001"
DOC_NUMBER = "91/2015/QH13"
QUESTION = "Hoàn trả tiền đặt cọc"
VECTOR = [1.0] + [0.0] * 767


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("HYBRID_ENABLED", "true")
    monkeypatch.setenv("QUERY_EXPANSION_ENABLED", "false")
    monkeypatch.setenv("RAG_DB_CONCURRENCY_LIMIT", "4")
    monkeypatch.setenv("RAG_DB_QUEUE_MAX_WAITING", "40")
    monkeypatch.setenv("EMBEDDING_PRICE_NANO_PER_TOKEN", "150")
    get_settings.cache_clear()
    hybrid.reset_index()
    engine = create_engine(
        URL.create("sqlite", database=str(tmp_path / "rag.db")),
        poolclass=QueuePool, pool_size=3, max_overflow=2, pool_timeout=0.5,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as db:
        doc = LegalDocument(
            title="Luật kiểm thử", doc_type="luat", doc_number=DOC_NUMBER, content="Toàn văn",
            status="active", is_processed=True, effective_date=date(2026, 10, 10),
        )
        db.add(doc)
        db.flush()
        for index, (article, clause, content) in enumerate([
            ("7", "1", "Điều 7. Hoàn trả tiền đặt cọc. Áp dụng Điều 8 của Luật này."),
            ("7", "2", "Điều 7. Hạn xử lý. Trong bảy ngày."),
            ("8", None, "Điều 8. Thời hạn xử lý."),
        ]):
            db.add(DocumentChunk(
                id=UUID(int=index + 1), document_id=doc.id, chunk_index=index, content=content,
                section_info={"dieu": article, "khoan": clause}, embedding=VECTOR, embedding_model=MODEL,
            ))
        db.commit()

    def db_dependency():
        with sessions() as db:
            yield db

    def sqlite_vector(db, vector, top_k, embedding_model, doc_types):
        assert vector == VECTOR
        rows = search.document_repo.retrieval_rows(db, embedding_model, doc_types=doc_types)
        return [(chunk, doc, (chunk.chunk_index + 1) / 10) for chunk, doc, _ in rows[:top_k]]

    monkeypatch.setitem(app.dependency_overrides, get_db, db_dependency)
    monkeypatch.setattr(search, "get_session", sessions)
    monkeypatch.setattr(search.document_repo, "semantic_search", sqlite_vector)
    try:
        yield engine
    finally:
        hybrid.reset_index()
        engine.dispose()


def item(index, query, **kwargs):
    return {
        "item_id": str(index), "query": query, "top_k": 3, "embedding_token_budget": 128,
        "exact_refs": [{"doc_ref": DOC_NUMBER, "article": "7", "clause": "1"}], **kwargs,
    }


def provider_response(inputs):
    return httpx.Response(200, json={
        "data": [{"index": i, "embedding": VECTOR} for i in reversed(range(len(inputs)))],
        "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)},
    })


def rag_http():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://rag", headers=TOKEN)


def use_provider(monkeypatch, upstream, sync_client=None):
    embedder = OpenRouterEmbedder(api_key="test", client=sync_client, async_client=upstream)
    monkeypatch.setitem(app.dependency_overrides, embedder_dep, lambda: embedder)
    return embedder


def test_burst_four_db_slots_and_one_provider_call_per_user(corpus, monkeypatch):
    connections = {"active": 0, "peak": 0}
    lock = threading.Lock()

    @event.listens_for(corpus, "checkout")
    def checkout(*_):
        with lock:
            connections["active"] += 1
            connections["peak"] = max(connections["peak"], connections["active"])
        time.sleep(0.003)

    @event.listens_for(corpus, "checkin")
    def checkin(*_):
        with lock:
            connections["active"] -= 1

    async def run():
        calls = []
        before_embedding = asyncio.Barrier(10)
        after_checkout_check = asyncio.Barrier(10)

        async def handle(request):
            assert request.url.path.endswith("/embeddings")
            inputs = json.loads(request.content)["input"]
            calls.append(inputs)
            await before_embedding.wait()
            assert corpus.pool.checkedout() == 0
            await after_checkout_check.wait()
            return provider_response(inputs)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream, rag_http() as client:
            use_provider(monkeypatch, upstream)
            responses = await asyncio.wait_for(asyncio.gather(*(
                client.post(BATCH, json={
                    "items": [item(i, f"{QUESTION} hồ sơ {user}-{i}?") for i in range(6)],
                    "deadline_ms": 10000, "request_turn_id": f"turn-{user}",
                })
                for user in range(10)
            )), timeout=12)
        assert len(calls) == 10 and all(len(inputs) == 6 for inputs in calls)
        assert len({text for inputs in calls for text in inputs}) == 60
        for user, response in enumerate(responses):
            assert response.status_code == 200, response.text
            data = response.json()["data"]
            assert [row["item_id"] for row in data["items"]] == [str(i) for i in range(6)]
            assert all(row["status"] == "ok" and row["chunks"] for row in data["items"]), data
            assert data["embedding_usage"]["attempts"] == 1
            assert data["embedding_usage"]["request_turn_id"] == f"turn-{user}"

    asyncio.run(run())
    assert 2 <= connections["peak"] <= 4
    assert connections["active"] == corpus.pool.checkedout() == 0


def test_budget_is_applied_per_item_after_expansion(corpus, monkeypatch):
    monkeypatch.setenv("QUERY_EXPANSION_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(search, "expand", lambda text: f"{text} quy định")
    long_query = f"{QUESTION} " * 40 + "?"
    short_queries = [f"{QUESTION} câu {i}?" for i in range(2, 7)]
    calls = []

    def handle(request):
        inputs = json.loads(request.content)["input"]
        calls.append(inputs)
        return provider_response(inputs)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream, rag_http() as client:
            embedder = use_provider(monkeypatch, upstream)
            response = await client.post(BATCH, json={"items": [
                item(1, long_query, embedding_token_budget=20),
                *[item(i, query, embedding_token_budget=40) for i, query in enumerate(short_queries, 2)],
            ]})
        assert response.status_code == 200, response.text
        items = response.json()["data"]["items"]
        assert items[0]["embedding_usage"]["budget_capped"] is True
        assert items[0]["status"] == "degraded"
        assert [row["status"] for row in items[1:]] == ["ok"] * 5
        assert all("vector" in row["chunks"][0]["rank_sources"] for row in items)
        assert set(search._cache_get(embedder, [f"{q} quy định" for q in [long_query, *short_queries]])) == set(range(1, 6))

    asyncio.run(run())
    assert len(calls) == 1 and len(calls[0]) == 6
    assert long_query.startswith(calls[0][0]) and calls[0][0] != long_query
    assert calls[0][1:] == [f"{q} quy định" for q in short_queries]


def test_batch_matches_single_order_and_provenance(corpus, monkeypatch):
    def handle(request):
        return provider_response(json.loads(request.content)["input"])

    async def run():
        transport = httpx.MockTransport(handle)
        with httpx.Client(transport=transport) as sync:
            async with httpx.AsyncClient(transport=transport) as upstream, rag_http() as client:
                use_provider(monkeypatch, upstream, sync)
                # Three protected slots are needed for exact, adjacent, and citation.
                items = [item(i, f"{QUESTION} trường hợp {i}?", top_k=5) for i in range(6)]
                batch = await client.post(BATCH, json={"items": items})
                assert batch.status_code == 200, batch.text
                # Force the single path to obtain fresh vectors as well.
                with search._embed_lock:
                    search._embed_cache.clear()
                for payload, row in zip(items, batch.json()["data"]["items"], strict=True):
                    single = await client.post(SINGLE, json={**payload, "planned": True})
                    assert single.status_code == 200, single.text
                    assert row["status"] == "ok"
                    expected = single.json()["data"]["chunks"]
                    keys = ("id", "rank_sources", "rrf_rank", "effective_date")
                    assert [tuple(chunk.get(key) for key in keys) for chunk in row["chunks"]] == [
                        tuple(chunk.get(key) for key in keys) for chunk in expected
                    ]
                    assert any("adjacent" in chunk["rank_sources"] for chunk in expected)
                    assert any("citation" in chunk["rank_sources"] for chunk in expected)

    asyncio.run(run())


def test_full_queue_rejects_before_starting_db_or_embedding(corpus, monkeypatch):
    monkeypatch.setenv("RAG_DB_CONCURRENCY_LIMIT", "1")
    monkeypatch.setenv("RAG_DB_QUEUE_MAX_WAITING", "1")
    get_settings.cache_clear()
    started = threading.Event()
    release = threading.Event()
    preflights = []
    provider_calls = []
    real_preflight = search._preflight

    def hold_preflight(db, model, query, *other):
        preflights.append(query)
        if query == "held":
            started.set()
            assert release.wait(5)
        return real_preflight(db, model, query, *other)

    def handle(request):
        provider_calls.append(request)
        return provider_response(json.loads(request.content)["input"])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream, rag_http() as client:
            use_provider(monkeypatch, upstream)
            active = asyncio.create_task(client.post(BATCH, json={"items": [item(1, "held")]}))
            waiting = None
            try:
                assert await asyncio.to_thread(started.wait, 2)
                waiting = asyncio.create_task(client.post(BATCH, json={"items": [item(2, "waiting")]}))
                async with asyncio.timeout(2):
                    while not search._batch_db_limiter().saturated():
                        await asyncio.sleep(0.001)
                rejected = await client.post(BATCH, json={"items": [item(3, "rejected")]})
                assert rejected.status_code == 503
                assert rejected.json()["error"]["code"] == "RAG_OVERLOADED"
                assert preflights == ["held"] and provider_calls == []
            finally:
                release.set()
                await asyncio.gather(*(task for task in (active, waiting) if task is not None))

    monkeypatch.setattr(search, "_preflight", hold_preflight)
    asyncio.run(run())
    assert preflights == ["held", "waiting"]


def test_batch_deduplicates_inputs_and_reuses_full_vectors(corpus, monkeypatch):
    calls = []

    def handle(request):
        inputs = json.loads(request.content)["input"]
        calls.append(inputs)
        return provider_response(inputs)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream, rag_http() as client:
            use_provider(monkeypatch, upstream)
            payload = {"items": [item(1, f"{QUESTION}?"), item(2, f"{QUESTION}?")]}
            first = (await client.post(BATCH, json=payload)).json()["data"]
            second = (await client.post(BATCH, json=payload)).json()["data"]
        assert [row["status"] for row in first["items"]] == ["ok", "ok"]
        assert first["embedding_usage"]["attempts"] == 1
        assert first["embedding_usage"]["prompt_tokens"] == 1
        assert second["embedding_usage"]["attempts"] == 0
        assert second["embedding_usage"]["cached_tokens"] > 0

    asyncio.run(run())
    assert calls == [[f"{QUESTION}?"]]


def test_deadline_stops_new_db_work_and_keeps_running_permit(corpus, monkeypatch):
    monkeypatch.setenv("RAG_DB_CONCURRENCY_LIMIT", "1")
    monkeypatch.setenv("RAG_DB_QUEUE_MAX_WAITING", "0")
    get_settings.cache_clear()
    release = threading.Event()
    finished = threading.Event()
    started = []

    def hold_ranking(db, model, item, work, vectors):
        started.append(item.item_id)
        # Hold both the checkout and the permit past the HTTP deadline.
        db.connection()
        release.wait(5)
        finished.set()

    def handle(request):
        return provider_response(json.loads(request.content)["input"])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream, rag_http() as client:
            use_provider(monkeypatch, upstream)
            try:
                response = await client.post(BATCH, json={
                    "items": [item(1, f"{QUESTION}?"), item(2, f"{QUESTION} tiếp?")], "deadline_ms": 1000,
                })
                assert response.status_code == 200
                data = response.json()["data"]
                assert data["embedding_usage"]["attempts"] == 1
                assert [row["error_code"] for row in data["items"]] == ["DEADLINE_EXCEEDED"] * 2
                assert started == ["1"] and corpus.pool.checkedout() == 1
                rejected = await client.post(BATCH, json={"items": [item(3, "rejected")]})
                assert rejected.status_code == 503
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 2)
                async with asyncio.timeout(2):
                    while search._batch_db_limiter().saturated():
                        await asyncio.sleep(0.001)
        assert corpus.pool.checkedout() == 0

    monkeypatch.setattr(search, "_finish_batch_item", hold_ranking)
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["timeout", "index", "dim", "connect"])
def test_embedding_failure_preserves_lexical_results_and_dispatch_state(corpus, monkeypatch, failure):
    def handle(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("after dispatch", request=request)
        if failure == "connect":
            raise httpx.ConnectError("before dispatch", request=request)
        return httpx.Response(200, json={
            "data": [{"index": 1 if failure == "index" else 0, "embedding": VECTOR if failure == "index" else [1.0]}],
        })

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream, rag_http() as client:
            use_provider(monkeypatch, upstream)
            response = await client.post(BATCH, json={"items": [item(1, f"{QUESTION}?")]})
        data = response.json()["data"]
        assert data["items"][0]["status"] == "degraded" and data["items"][0]["chunks"]
        assert data["items"][0]["error_code"] == "EMBEDDING_SERVICE_UNAVAILABLE"
        uncertain = failure != "connect"
        assert data["embedding_usage"]["usage_uncertain"] is uncertain
        assert (data["embedding_usage"]["cost_nano_usd"] > 0) is uncertain
        assert data["embedding_usage"]["attempts"] == int(uncertain)

    asyncio.run(run())
