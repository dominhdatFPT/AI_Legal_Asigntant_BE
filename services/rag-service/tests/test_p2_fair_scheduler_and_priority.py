import asyncio
import json
import threading
import time

import httpx

from app.core.config import get_settings
from app.schemas.search import EmbeddingUsage
from app.services import search
from app.services.db_scheduler import FairDBScheduler
from app.services.embedding import OpenRouterEmbedder, embed_batch_async

from .test_p1_batch_search_and_limiter import (
    BATCH, QUESTION, SINGLE, VECTOR, corpus, item, provider_response, rag_http, use_provider,
)


async def until(condition):
    async with asyncio.timeout(5):
        while not condition():
            await asyncio.sleep(0.001)


def test_interactive_http_search_gets_first_freed_slot(corpus, monkeypatch):
    real_preflight = search._preflight
    lock = threading.Lock()
    first_slot = threading.Event()
    other_slots = threading.Event()
    starts = []
    completes = []

    def heavy_preflight(db, model, query, *args):
        with lock:
            index = len(starts)
            starts.append(query)
        if index < 4:
            assert (first_slot if index == 0 else other_slots).wait(5)
        if query != "simple?":
            time.sleep(0.04)
        return real_preflight(db, model, query, *args)

    monkeypatch.setattr(search, "_preflight", heavy_preflight)

    def embed(request):
        return provider_response(json.loads(request.content)["input"])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(embed)) as upstream, rag_http() as client:
            use_provider(monkeypatch, upstream)

            async def batch(tenant):
                response = await client.post(BATCH, headers={"X-Fairness-Key": tenant}, json={
                    "items": [item(i, f"{tenant} {i}?") for i in range(6)],
                })
                completes.append(tenant)
                assert response.status_code == 200
                assert [row["status"] for row in response.json()["data"]["items"]] == ["ok"] * 6

            async def single():
                response = await client.post(SINGLE, headers={"X-Fairness-Key": "simple"}, json={
                    "query": "simple?", "priority": "interactive",
                })
                completes.append("simple")
                assert response.status_code == 200

            tasks = [asyncio.create_task(batch(tenant)) for tenant in ("complexA", "complexB")]
            try:
                await until(lambda: len(starts) == 4 and search._batch_db_limiter()._waiting >= 2)
                tasks.append(asyncio.create_task(single()))
                await until(lambda: "simple" in search._batch_db_limiter()._lanes["interactive"])
                first_slot.set()
                await until(lambda: len(starts) >= 5)
                assert starts[4] == "simple?"
                await asyncio.wait_for(asyncio.shield(tasks[-1]), 2)
                assert completes == ["simple"]
            finally:
                first_slot.set()
                other_slots.set()
                await asyncio.gather(*tasks)
            assert set(completes) == {"complexA", "complexB", "simple"}

    asyncio.run(run())


def test_interactive_burst_yields_to_waiting_batch():
    starts = []
    started = threading.Event()
    release = threading.Event()

    def park():
        started.set()
        assert release.wait(5)

    async def run():
        scheduler = FairDBScheduler(1, 96, 5, interactive_burst=3)
        deadline = time.monotonic() + 10
        blocker = asyncio.create_task(scheduler.run(deadline, park, tenant_key="park", priority="batch"))
        tasks = []
        try:
            await until(started.is_set)
            for n in range(18):
                tasks.append(asyncio.create_task(scheduler.run(
                    deadline, lambda: starts.append("interactive"), tenant_key=f"S{n}", priority="interactive",
                )))
            for n in range(6):
                tasks.append(asyncio.create_task(scheduler.run(
                    deadline, lambda: starts.append("batch"), tenant_key=f"B{n}", priority="batch",
                )))
            await until(lambda: scheduler._waiting == 24)
        finally:
            release.set()
            await asyncio.gather(blocker, *tasks)
        assert starts == ["interactive", "interactive", "interactive", "batch"] * 6
        assert not scheduler._active and not scheduler._waiting and not any(scheduler._lanes.values())

    asyncio.run(run())


def test_batch_uses_idle_db_slots_for_one_tenant(corpus, monkeypatch):
    real_preflight = search._preflight
    lock = threading.Lock()
    counts = {"active": 0, "peak": 0}

    def heavy_preflight(db, *args):
        with lock:
            counts["active"] += 1
            counts["peak"] = max(counts["peak"], counts["active"])
        try:
            time.sleep(0.04)
            return real_preflight(db, *args)
        finally:
            with lock:
                counts["active"] -= 1

    def embed(request):
        return provider_response(json.loads(request.content)["input"])

    monkeypatch.setattr(search, "_preflight", heavy_preflight)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(embed)) as upstream, rag_http() as client:
            use_provider(monkeypatch, upstream)
            response = await client.post(BATCH, json={"items": [item(i, f"{QUESTION} {i}?") for i in range(6)]})
        assert response.status_code == 200
        assert [row["item_id"] for row in response.json()["data"]["items"]] == [str(i) for i in range(6)]

    asyncio.run(run())
    assert 1 < counts["peak"] <= 4
    assert counts["active"] == corpus.pool.checkedout() == 0


def test_batch_tenants_round_robin_at_four_slot_capacity():
    started = [threading.Event() for _ in range(4)]
    releases = [threading.Event() for _ in range(4)]
    starts = []

    def park(index):
        started[index].set()
        assert releases[index].wait(5)

    async def run():
        scheduler = FairDBScheduler(4, 96, 5)
        deadline = time.monotonic() + 10
        blockers = [asyncio.create_task(scheduler.run(
            deadline, lambda n=n: park(n), tenant_key=f"park{n}", priority="batch",
        )) for n in range(4)]
        tasks = []
        try:
            await until(lambda: all(event.is_set() for event in started))
            for tenant in ("U1", "U2", "U3", "U4"):
                for _ in range(6):
                    tasks.append(asyncio.create_task(scheduler.run(
                        deadline, lambda tenant=tenant: starts.append(tenant), tenant_key=tenant, priority="batch",
                    )))
            await until(lambda: scheduler._waiting == 24)
            releases[0].set()
            await asyncio.gather(*tasks)
        finally:
            for release in releases:
                release.set()
            await asyncio.gather(*blockers, *tasks)
        assert starts == ["U1", "U2", "U3", "U4"] * 6
        assert not scheduler._active and not scheduler._waiting and not any(scheduler._lanes.values())

    asyncio.run(run())


def test_concurrent_tenants_each_get_a_slot_before_any_second_grant():
    release = threading.Event()
    starts = []
    lock = threading.Lock()

    def work(tenant):
        with lock:
            starts.append(tenant)
        assert release.wait(5)

    async def run():
        scheduler = FairDBScheduler(4, 96, 5, per_tenant_burst=4)
        deadline = time.monotonic() + 10
        tasks = [asyncio.create_task(scheduler.run(
            deadline, lambda tenant=tenant: work(tenant), tenant_key=tenant, priority="batch",
        )) for tenant in ("U1", "U2", "U3", "U4") for _ in range(6)]
        try:
            await until(lambda: len(starts) == 4)
            assert set(starts) == {"U1", "U2", "U3", "U4"}
        finally:
            release.set()
            await asyncio.gather(*tasks)

    asyncio.run(run())


def test_embedding_single_flight_survives_owner_deadline():
    async def run():
        calls = []
        release = asyncio.Event()

        async def provider(request):
            inputs = json.loads(request.content)["input"]
            calls.append(inputs)
            await release.wait()
            return provider_response(inputs)

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
            embedder = OpenRouterEmbedder(api_key="test", async_client=upstream)
            usages = [EmbeddingUsage(request_turn_id=str(n)) for n in range(8)]
            started = time.monotonic()
            # Schedule the short-deadline owner first so it is responsible for dispatch.
            leader = asyncio.create_task(embed_batch_async(embedder, [QUESTION], started + 0.1, usages[0]))
            await until(lambda: bool(calls))
            followers = [asyncio.create_task(
                embed_batch_async(embedder, [QUESTION], started + 5, usage)
            ) for usage in usages[1:]]
            try:
                assert await leader == {}
                assert calls == [[QUESTION]]
                assert usages[0].attempts == 1 and usages[0].usage_uncertain
            finally:
                release.set()
                results = await asyncio.gather(*followers)
            assert results == [{QUESTION: tuple(VECTOR)}] * 7
            assert all(u.attempts == u.cost_nano_usd == 0 and u.cached_tokens > 0 for u in usages[1:])
            assert calls == [[QUESTION]]

    asyncio.run(run())


def test_overlapping_embedding_batches_bill_only_new_inputs():
    async def run():
        release = asyncio.Event()
        calls = []

        async def provider(request):
            inputs = json.loads(request.content)["input"]
            calls.append(inputs)
            await release.wait()
            return provider_response(inputs)

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
            embedder = OpenRouterEmbedder(api_key="test", async_client=upstream)
            deadline = time.monotonic() + 5
            first, second = EmbeddingUsage(), EmbeddingUsage()
            first_task = asyncio.create_task(embed_batch_async(embedder, ["one", "two"], deadline, first))
            second_task = asyncio.create_task(embed_batch_async(embedder, ["two", "three"], deadline, second))
            try:
                await until(lambda: len(calls) == 2)
                assert calls == [["one", "two"], ["three"]]
            finally:
                release.set()
                first_vectors, second_vectors = await asyncio.gather(first_task, second_task)
            assert list(first_vectors) == ["one", "two"] and list(second_vectors) == ["two", "three"]
            assert first.prompt_tokens == 2 and second.prompt_tokens == 1
            assert second.cached_tokens > 0 and first.attempts == second.attempts == 1

    asyncio.run(run())


def test_results_match_sync_baseline_alone_and_at_20_user_burst(corpus, monkeypatch):
    monkeypatch.setenv("RAG_DB_QUEUE_MAX_WAITING", "96")
    get_settings.cache_clear()
    batches = [[item(i, f"{QUESTION} hồ sơ {user}-{i}?", top_k=5) for i in range(6)] for user in range(10)]
    singles = [item(i, f"{QUESTION} câu ngắn {i}?", top_k=5) for i in range(10)]

    def embed(request):
        return provider_response(json.loads(request.content)["input"])

    def clear_vectors():
        with search._embed_lock:
            search._embed_cache.clear()

    async def run():
        transport = httpx.MockTransport(embed)
        with httpx.Client(transport=transport) as sync:
            async with httpx.AsyncClient(transport=transport) as upstream, rag_http() as client:
                embedder = use_provider(monkeypatch, upstream, sync)
                baseline = {}
                for payload in singles + [payload for batch in batches for payload in batch]:
                    with search.get_session() as db:
                        chunks = search.search_chunks(
                            db, embedder, payload["query"], payload["top_k"], planned=True,
                            embedding_token_budget=payload["embedding_token_budget"], exact_refs=payload["exact_refs"],
                        )
                    assert chunks and chunks[0]["effective_date"] and chunks[0]["retrieval_sources"]
                    baseline[payload["query"]] = list(chunks)

                async def single(payload):
                    response = await client.post(SINGLE, json={**payload, "planned": True})
                    assert response.status_code == 200, response.text
                    assert response.json()["data"]["chunks"] == baseline[payload["query"]]

                async def batch(payloads, tenant):
                    response = await client.post(BATCH, json={"items": payloads, "fairness_key": tenant})
                    assert response.status_code == 200, response.text
                    rows = response.json()["data"]["items"]
                    for payload, row in zip(payloads, rows, strict=True):
                        assert row["item_id"] == payload["item_id"] and row["status"] == "ok", row
                        expected = baseline[payload["query"]]
                        # The P1 batch schema emits nulls for absent optional chunk fields.
                        optional = {"retrieval_sources", "rank_sources", "rrf_rank", "document_version", "effective_date"}
                        assert row["chunks"] == [{**dict.fromkeys(optional), **chunk} for chunk in expected]

                clear_vectors()
                await single(singles[0])
                await batch(batches[0], "alone")
                clear_vectors()
                await asyncio.wait_for(asyncio.gather(
                    *(batch(payloads, f"B{i}") for i, payloads in enumerate(batches)),
                    *(single(payload) for payload in singles),
                ), 15)

    asyncio.run(run())
