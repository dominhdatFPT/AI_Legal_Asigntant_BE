"""Tier A performance gates, run after the functional Layer 3 suites."""

import asyncio
import gzip
import json
import math
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import aclosing, asynccontextmanager, contextmanager
from datetime import date
from pathlib import Path
from urllib.parse import urljoin
from uuid import uuid4

import httpx
import pytest

from tests_3layer.layer2_cross_service.db_bootstrap import E2E_PORT, connect
from tests_3layer.layer3_docker_e2e.test_l3_gateway_and_containers_e2e import read_sse
from tests_3layer.support import eventually, response_data

from .gateway_conf import CLIENT_MAP, ORIGINAL_ZONE, PERF_ZONE, SOURCE
from .probes import APP_SERVICES, BUDGETS, GATEWAY, ORIGIN, pg_calls, percentile, query_delta

PROCEDURES = "/api/procedures"
CONVERSATIONS = "/api/agent/conversations"
NOTIFICATIONS = "/api/notifications"
SELECTS = r"^[[:space:]]*(select|with)[[:space:]]"
MUTATIONS = r"^[[:space:]]*(insert|update|delete)[[:space:]]"
RAG_SEARCH = r"\m(document_chunks|legal_documents)\M"
# Target tables per endpoint; detail child relations are subqueries of the procedures statement.
TABLES = {
    "catalog": r"\m(procedures|procedure_(domains|requirements|conditions|legal_bases|fees))\M",
    "records": r"\m(land_procedure_records|business_procedure_records)\M",
    "users": r"\m(users|citizen_profiles)\M",
    "notifications": r"\m(notifications|group_notifications|group_notification_reads)\M",
    "consultations": r"\mconsultation(s|_messages|_summaries|_reports|_terms_acceptances)\M",
    "conversations": r"\m(conversations|messages)\M",
}
CHAT_QUESTION = "Điều 9 Luật Đất đai phân loại đất như thế nào?"


def elapsed_ms(start):
    return (time.perf_counter() - start) * 1000


def perf_headers(headers=None, ip=None):
    return {**(headers or {}), "CF-Connecting-IP": ip or f"10.91.{uuid4().int % 256}.{uuid4().int % 256}"}


def test_gateway_config_changes_only_rate_limit_key(perf_stack):
    derived = (Path(os.environ["LEGAL_TEST_KEYS_DIR"]) / "nginx.perf.conf").read_text(encoding="utf-8")
    assert derived.count(PERF_ZONE) == 4 and derived.count(CLIENT_MAP) == 1
    original = SOURCE.read_text(encoding="utf-8")
    assert derived.replace(CLIENT_MAP, "").replace(PERF_ZONE, ORIGINAL_ZONE) == original


@pytest.mark.parametrize("kind", ["procedures", "procedure_detail", "domains", "user", "notification"])
def test_p61_query_budget(kind, gateway, mint, users_and_tokens, perf_report):
    limits = BUDGETS["resources"]["queries"]
    procedure = kind in {"procedures", "procedure_detail"}
    headers = perf_headers(mint(users_and_tokens["citizens"][0]))
    database, tables, path = {
        "procedures": ("procedure_db", TABLES["catalog"], f"{PROCEDURES}?page_size=20&q=perf-{uuid4().hex}"),
        "procedure_detail": ("procedure_db", TABLES["catalog"], f"{PROCEDURES}/DD-009"),
        "domains": ("procedure_db", TABLES["catalog"], f"{PROCEDURES}/domains"),
        "user": ("user_db", TABLES["users"], "/api/users/me"),
        "notification": ("notification_db", TABLES["notifications"], NOTIFICATIONS),
    }[kind]

    def request():
        return response_data(gateway.get(path, headers=headers), 200)

    _, calls, _ = query_delta(database, SELECTS, tables, request)
    perf_report.measure(f"P6.1 {kind} cold queries", calls, limits["procedure_cold" if procedure else kind])
    if procedure:
        perf_report.measure(f"P6.1 {kind} cold queries observed", calls, 1, ">=")
        _, warm_calls, _ = query_delta(database, SELECTS, tables, request)
        perf_report.measure(f"P6.1 {kind} warm queries", warm_calls, limits["procedure_warm"], "==")
    perf_report.assert_passed()


LIST_CASES = {
    "consultation": (
        "/api/consultations?page_size=20", TABLES["consultations"],
        """INSERT INTO consultations (citizen_user_id, lawyer_user_id, source, question,
                  status, close_reason, accepted_at, closed_at)
           SELECT %s::uuid, %s::uuid, 'direct', 'Câu hỏi thử nghiệm', 'closed', 'completed', now(), now()
           FROM generate_series(1, %s)""",
    ),
    "procedure": (
        f"{PROCEDURES}/applications/me?page_size=20", TABLES["records"],
        """INSERT INTO land_procedure_records (user_id, procedure_code, procedure_id, applicant_name)
           SELECT %s::uuid, p.code, p.id, 'Performance Test Citizen'
           FROM procedures p CROSS JOIN generate_series(1, %s) WHERE p.code = 'DD-001'""",
    ),
    "notification": (
        f"{NOTIFICATIONS}?limit=20", TABLES["notifications"],
        """INSERT INTO notifications (id, user_id, event_type, title, message)
           SELECT gen_random_uuid()::text, %s, 'system', 'Performance test',
                  'Thông báo thử nghiệm cho kiểm tra hiệu năng.'
           FROM generate_series(1, %s)""",
    ),
}


@pytest.mark.parametrize("service", LIST_CASES)
def test_p62_one_and_twenty_results_use_same_query_count(service, gateway, mint, users_and_tokens, perf_report):
    limits = BUDGETS["resources"]["queries"]
    database = f"{service}_db"
    path, tables, seed = LIST_CASES[service]
    subject = users_and_tokens["citizens"][1]
    headers = perf_headers(mint(subject))
    # Prime any authentication/caller cache before comparing the two list sizes.
    response_data(gateway.get(path, headers=headers), 200)
    counts = []
    previous = 0
    for size in (limits["small_rows"], limits["large_rows"]):
        params = (subject, str(uuid4()), size - previous) if service == "consultation" else (subject, size - previous)
        with connect(database, E2E_PORT) as db:
            assert db.execute(seed, params).rowcount == size - previous
        result, calls, _ = query_delta(
            database, SELECTS, tables, lambda: response_data(gateway.get(path, headers=headers), 200),
        )
        assert len(result["items"]) == size, result
        counts.append(calls)
        previous = size
    assert min(counts) > 0, f"{service}: missing list query samples"
    perf_report.details[f"P6.2 {service} queries"] = counts
    perf_report.measure(f"P6.2 {service} query count delta", abs(counts[1] - counts[0]), limits["n_plus_one_delta"], "==")
    perf_report.assert_passed()


@pytest.mark.parametrize(("kind", "path"), [
    ("procedures", f"{PROCEDURES}?page_size=20"),
    ("procedure_detail", f"{PROCEDURES}/DD-001"),
    ("user", "/api/users/me"),
    ("notifications", f"{NOTIFICATIONS}?limit=10"),
])
def test_p63_payload_and_gzip(kind, path, gateway, users_and_tokens, mint, perf_report):
    limits = BUDGETS["resources"]
    headers = {**perf_headers(mint(users_and_tokens["citizens"][1])), "Accept-Encoding": "gzip"}
    with gateway.stream("GET", path, headers=headers) as response:
        assert response.status_code == 200
        assert response.headers["Content-Type"].startswith("application/json")
        raw = b"".join(response.iter_raw())
        compressed = response.headers.get("Content-Encoding") == "gzip"
    data = json.loads(gzip.decompress(raw) if compressed else raw)
    assert data["error"] is None
    decoded_bytes = len(gzip.decompress(raw)) if compressed else len(raw)
    perf_report.details[f"P6.3 {kind} decoded bytes"] = decoded_bytes
    perf_report.measure(f"P6.3 {kind} wire bytes", len(raw), limits["payload_bytes"][kind])
    if decoded_bytes >= limits["gzip_min_bytes"]:
        perf_report.measure(f"P6.3 {kind} gzip present", int(compressed), 1, "==")
    perf_report.assert_passed()


def test_p64_idle_container_memory(perf_stack, perf_report):
    assert APP_SERVICES <= perf_stack.keys(), f"Unknown app_services: {sorted(APP_SERVICES - perf_stack.keys())}"
    for service, usage in perf_stack.items():
        if service in APP_SERVICES:
            perf_report.measure(f"P6.4 {service} idle working-set bytes", usage["working_set"], BUDGETS["resources"]["idle_bytes"])
        else:
            perf_report.details[f"P6.4 {service} idle bytes (report only)"] = usage
    perf_report.assert_passed()


async def request_burst(origin, policy, *, distinct_addresses=False):
    size = policy["requests"]
    limits = httpx.Limits(max_connections=size, max_keepalive_connections=size)
    async with httpx.AsyncClient(base_url=origin, limits=limits, timeout=10, trust_env=False) as client:
        addr = perf_headers()["CF-Connecting-IP"]
        start = asyncio.Event()

        async def request(index):
            await start.wait()
            at = time.perf_counter()
            headers = {"Origin": ORIGIN, "CF-Connecting-IP": f"10.92.1.{index + 1}" if distinct_addresses else addr}
            kwargs = {"json": {}} if policy["accepted_status"] == 422 else {}
            method = "POST" if kwargs else "GET"
            response = await client.request(method, policy["path"], headers=headers, **kwargs)
            return response, elapsed_ms(at)

        async def edge_request(index):
            at = time.perf_counter()
            headers = {"Origin": ORIGIN, "CF-Connecting-IP": f"10.92.2.{index + 1}" if distinct_addresses else addr}
            kwargs = {"json": {}} if policy["accepted_status"] == 422 else {}
            method = "POST" if kwargs else "GET"
            response = await client.request(method, policy["path"], headers=headers, **kwargs)
            return response, elapsed_ms(at)

        # Open the keep-alive pool first so TCP handshakes through the Docker Desktop
        # port proxy stay outside the 429 latency window. `GET /` is answered by Nginx
        # itself and sits outside every limit_req zone, so no rate-limit tokens are spent.
        for _ in range(2):
            warm = await asyncio.gather(*(client.get("/", headers={"Origin": ORIGIN}) for _ in range(size)))
            assert all(response.status_code == 200 for response in warm), "Gateway pre-connect failed"
        jobs = [asyncio.create_task(request(index)) for index in range(size)]
        started = time.perf_counter()
        start.set()
        results = await asyncio.gather(*jobs)
        elapsed = time.perf_counter() - started
        # Measure edge 429 rejection latency sequentially on an already-warm keepalive socket
        # against the saturated bucket so Windows wslrelay port-proxy doesn't multiplex 429s behind 200s.
        edge_probe = [await edge_request(index) for index in range(10)]
        return results, elapsed, edge_probe


def assert_burst(policy, results, elapsed, edge_probe, perf_report, name):
    limits = BUDGETS["rate_limit"]
    rejected = [(response, ms) for response, ms in results if response.status_code == limits["rejection_status"]]
    edge_rejected = [(response, ms) for response, ms in edge_probe if response.status_code == limits["rejection_status"]]
    counts = Counter(response.status_code for response, _ in results)
    perf_report.details[f"{name} status codes"] = dict(counts)
    accepted = counts[policy["accepted_status"]]
    first_burst = policy["burst"] + 1
    replenished = math.ceil(policy["rate_per_second"] * elapsed) + 1
    if policy["rate_per_second"] < 1 and elapsed < 1 / policy["rate_per_second"]:
        replenished = 0
    perf_report.measure(f"{name} accepted minimum", accepted, first_burst, ">=")
    perf_report.measure(f"{name} accepted maximum", accepted, first_burst + replenished)
    perf_report.measure(f"{name} unexpected statuses", len(results) - accepted - len(rejected), 0, "==")
    assert rejected, f"{name}: no edge rejects, statuses={dict(counts)}, elapsed={elapsed:.3f}s"
    # Concurrent-burst 429s queue behind 200s in the Windows wslrelay port proxy, so they are evidence, not a gate.
    perf_report.details[f"{name} burst 429 p95 ms (report only)"] = percentile([ms for _, ms in rejected], 95)
    edge_counts = Counter(response.status_code for response, _ in edge_probe)
    perf_report.details[f"{name} edge probe status codes"] = dict(edge_counts)
    assert edge_counts.keys() <= {policy["accepted_status"], limits["rejection_status"]}, (
        f"{name}: unexpected edge probe statuses {dict(edge_counts)}"
    )
    assert len(edge_rejected) >= 5, f"{name}: saturated follow-up produced {len(edge_rejected)} 429s, need >= 5"
    perf_report.measure(f"{name} 429 p95 ms", percentile([ms for _, ms in edge_rejected], 95), limits["p95_429_ms"])
    missing_cors = sum(response.headers.get("Access-Control-Allow-Origin") != ORIGIN for response, _ in [*rejected, *edge_rejected])
    perf_report.measure(f"{name} 429 missing CORS", missing_cors, 0, "==")
    perf_report.assert_passed()


@pytest.mark.parametrize("zone", ["general", "heavy", "auth", "chat"])
def test_p41_p42_p43_rate_limits_and_cors(zone, perf_stack, perf_report):
    policy = BUDGETS["rate_limit"][zone]
    results, elapsed, edge_probe = asyncio.run(request_burst(GATEWAY, policy))
    assert_burst(policy, results, elapsed, edge_probe, perf_report, f"P4 {zone}")


def new_conversation(client, headers):
    return response_data(client.post(CONVERSATIONS, headers=headers, json={}), 201)["id"]


def chat_path(conversation):
    return f"{CONVERSATIONS}/{conversation}/messages/stream"


def question():
    return f"{CHAT_QUESTION.rstrip('?')} (mã thử nghiệm {uuid4().hex})?"


def timed_chat(client, path, headers, content):
    first_delta = None
    started = time.perf_counter()

    class TimedStream:
        @contextmanager
        def stream(self, *args, **kwargs):
            with client.stream(*args, **kwargs) as response:
                assert "Content-Encoding" not in response.headers
                original_lines = response.iter_lines

                def lines():
                    nonlocal first_delta
                    event = None
                    for line in original_lines():
                        if line.startswith("event:"):
                            event = line.partition(":")[2].strip()
                        if not line:
                            if event == "delta" and first_delta is None:
                                first_delta = elapsed_ms(started)
                            event = None
                        yield line

                response.iter_lines = lines
                yield response

    frames = read_sse(TimedStream(), path, headers, content)
    assert first_delta is not None and any(event == "delta" and data["text"] for event, data in frames), frames
    return frames[-1][1]["assistant_message"], first_delta


def test_p51_chat_cold_and_warm_gateway_stream(gateway, rag_corpus, mint, users_and_tokens, perf_report):
    # Known product bug (Phase 0 finding, see budgets.json sse.multi_part_cache_bug): a multi-part question is split by
    # rag-service split_question (search.py:261), the original plus its parts exceed the per-item embedding token budget
    # and budgeted_inputs sets budget_capped=True (embedding.py:493),
    # and agent rag_client._cache_store (rag_client.py:225-233) then refuses to cache, so warm RAG calls stay > 0.
    # question() therefore stays one sentence; do not append a second clause to make this gate pass or fail.
    limits = BUDGETS["sse"]
    headers = perf_headers(mint(users_and_tokens["citizens"][2]))
    content = question()
    measurements = []
    for phase in ("cold", "warm"):
        path = chat_path(new_conversation(gateway, headers))
        (message, ms), calls, _ = query_delta(
            "rag_db", SELECTS, RAG_SEARCH, lambda: timed_chat(gateway, path, headers, content), control=False,
        )
        assert message["metadata"]["llm_bypass"] and message["metadata"]["rag_chunk_ids"], message
        measurements.append(message["metadata"]["rag_chunk_ids"])
        perf_report.measure(f"P5.1 {phase} TTFD ms", ms, limits[f"{phase}_ms"])
        perf_report.measure(
            f"P5.1 {phase} RAG queries", calls, 1 if phase == "cold" else limits["warm_rag_calls"],
            ">=" if phase == "cold" else "==",
        )
    assert measurements[0] == measurements[1]
    perf_report.assert_passed()


def test_p52_idempotency_replay(gateway, rag_corpus, mint, users_and_tokens, perf_report):
    limits = BUDGETS["sse"]
    headers = {**perf_headers(mint(users_and_tokens["citizens"][3])), "Idempotency-Key": uuid4().hex}
    cid = new_conversation(gateway, headers)
    path, content = chat_path(cid), question()
    original, _ = timed_chat(gateway, path, headers, content)
    conversations = TABLES["conversations"]
    reads_before = pg_calls("agent_db", SELECTS, conversations)
    rag_before = pg_calls("rag_db", SELECTS, RAG_SEARCH)
    (replayed, ms), writes, _ = query_delta(
        "agent_db", MUTATIONS, conversations, lambda: timed_chat(gateway, path, headers, content), control=False,
    )
    # replay_debt ends on replay_debt_expires; from then on the authorized replay must not touch the DB.
    debt_open = date.today() <= date.fromisoformat(limits["replay_debt_expires"])
    reads = pg_calls("agent_db", SELECTS, conversations) - reads_before
    perf_report.measure("P5.2 authorized replay reads", reads, limits["replay_reads"] if debt_open else 0)
    perf_report.measure("P5.2 replay writes", writes, limits["replay_writes"], "==")
    perf_report.measure(
        "P5.2 replay RAG queries", pg_calls("rag_db", SELECTS, RAG_SEARCH) - rag_before, limits["replay_rag_calls"], "==",
    )
    perf_report.measure("P5.2 replay TTFD ms", ms, limits["replay_ms"])
    assert replayed["id"] == original["id"]
    assert len(response_data(gateway.get(f"{CONVERSATIONS}/{cid}", headers=headers), 200)["messages"]) == 2
    perf_report.assert_passed()


def test_p52_only_one_inflight_chat_per_user(gateway, rag_corpus, mint, users_and_tokens, perf_report):
    headers = perf_headers(mint(users_and_tokens["citizens"][4]))
    first, second = [chat_path(new_conversation(gateway, headers)) for _ in range(2)]
    # Hold the first cold RAG query briefly so the second request hits the live
    # Redis in-flight slot even on a fast host. Always release on assertion failure.
    with ThreadPoolExecutor(max_workers=1) as executor, connect("rag_db", E2E_PORT) as blocker:
        blocker.execute("LOCK TABLE document_chunks IN ACCESS EXCLUSIVE MODE")
        job = executor.submit(timed_chat, gateway, first, headers, question())
        try:
            with connect("postgres", E2E_PORT, autocommit=True) as observer:
                def waiting():
                    if job.done():
                        job.result()
                        raise AssertionError("First stream completed before it waited for RAG")
                    return observer.execute("""
                        SELECT 1 FROM pg_stat_activity
                        WHERE datname = 'rag_db' AND wait_event_type = 'Lock' AND query ~* 'document_chunks'
                        """).fetchone()

                eventually(waiting, description="first stream waiting on its cold RAG query")
            started = time.perf_counter()
            duplicate = gateway.post(second, headers=headers, json={"content": question()})
            rejected_ms = elapsed_ms(started)
        finally:
            blocker.rollback()
        job.result()
    assert duplicate.status_code == 429, duplicate.text
    payload = duplicate.json()
    assert (payload.get("error") or payload.get("detail"))["code"] == "AI_REQUEST_IN_PROGRESS", payload
    perf_report.measure("P5.2 in-flight rejection ms", rejected_ms, BUDGETS["sse"]["duplicate_ms"])
    perf_report.assert_passed()


async def sse_events(response):
    fields = {}
    async for line in response.aiter_lines():
        if not line:
            if fields:
                assert "event" in fields and "data" in fields, fields
                yield fields["event"], json.loads(fields["data"])
                fields.clear()
        elif line.startswith(":"):
            if line == ": keep-alive":
                yield "keep-alive", None
        else:
            key, separator, value = line.partition(":")
            assert separator and key not in fields, line
            fields[key] = value.removeprefix(" ")
    assert not fields, "Stream ended within an SSE frame"


@asynccontextmanager
async def notification_events(client, headers):
    async with client.stream("GET", f"{NOTIFICATIONS}/stream", headers=headers) as response:
        assert response.status_code == 200, await response.aread()
        assert response.headers["Content-Type"].startswith("text/event-stream")
        assert "Content-Encoding" not in response.headers
        async with aclosing(sse_events(response)) as events:
            yield events


def test_p53_notification_connected_without_buffering_or_gzip(mint, users_and_tokens, perf_report):
    subject = users_and_tokens["citizens"][5]
    headers = perf_headers(mint(subject))

    async def connect_stream():
        async with httpx.AsyncClient(base_url=GATEWAY, timeout=5, trust_env=False) as client:
            start = time.perf_counter()
            async with notification_events(client, headers) as frames:
                event, payload = await anext(frames)
                ms = elapsed_ms(start)
                assert event == "connected" and payload["user_id"] == subject, (event, payload)
                return ms

    perf_report.measure("P5.3 notification connected ms", asyncio.run(connect_stream()), BUDGETS["sse"]["connected_ms"])
    perf_report.assert_passed()


# Quoted or url(...) references that are root-relative, ./ or ../ relative, or absolute. Bare names such as
# "(auth-decor.js)" in comments are skipped on purpose. data: never matches; other origins are dropped after urljoin.
ASSET_REF = re.compile(
    r"""["'`(]((?:https?:)?\.{0,2}/[^"'`()\s?#${}]+\.(?:js|mjs|css|png|jpe?g|gif|webp|avif|svg|ico|woff2?))(?=[?#"'`)\s,])"""
)


def asset_refs(text, base):
    # shortcut: regex scan, not a parser; misses srcset candidates after the first and URLs built at runtime,
    # switch to a browser-collected asset list if FE starts composing asset URLs dynamically.
    resolved = {urljoin(base, ref) for ref in ASSET_REF.findall(text)}
    return {url.removeprefix(ORIGIN) for url in resolved if url.startswith(ORIGIN + "/")}


def test_p24_static_asset_caching(fe_server, perf_report):
    with httpx.Client(base_url=fe_server, timeout=5, trust_env=False) as client:
        refs = set()
        for spec in BUDGETS["browser"]["pages"].values():
            page = client.get(spec["path"])
            assert page.status_code == 200, (spec["path"], page.status_code)
            refs |= asset_refs(page.text, ORIGIN + spec["path"])
        assert refs, "No static assets found in FE HTML"
        responses = {asset: client.get(asset) for asset in sorted(refs)}
        # One level only: assets named by HTML-referenced JS/CSS (drum PNG, dynamic imports, CSS url()).
        nested = set().union(*(
            asset_refs(response.text, ORIGIN + asset) for asset, response in responses.items()
            if asset.endswith((".js", ".mjs", ".css")) and response.status_code == 200
        ))
        responses |= {asset: client.get(asset) for asset in sorted(nested - responses.keys())}
        assets = responses.keys()
        perf_report.details["P2.4 scanned assets"] = sorted(assets)
        if debt := BUDGETS["browser"].get("image_debt"):
            assert debt["path"] in assets, f"P2.4 scan missed {debt['path']}; asset discovery is incomplete"
        revalidated = 0
        for asset, first in responses.items():
            assert first.status_code == 200, (asset, first.status_code)
            cc = first.headers.get("cache-control", "").lower()
            max_age = re.search(r"max-age=(\d+)", cc)
            assert "public" in cc and "no-store" not in cc and max_age and int(max_age[1]) > 0, (asset, cc)
            cond = {}
            if etag := first.headers.get("etag"):
                cond["If-None-Match"] = etag
            if lm := first.headers.get("last-modified"):
                cond["If-Modified-Since"] = lm
            assert cond, f"{asset}: missing ETag and Last-Modified"
            second = client.get(asset, headers=cond)
            assert second.status_code == 304, (asset, second.status_code)
            revalidated += 1
    perf_report.measure("P2.4 static assets cacheable & 304 fraction", revalidated / len(assets), 1.0, "==")
    perf_report.assert_passed()


def test_p11_p2_desktop_browser(fe_server, tokens_file, tmp_path, perf_report):
    code, summary, failures = perf_report.k6("desktop", "browser_pages.js", {
        "PROFILE": "desktop", "RUNS": str(BUDGETS["browser"]["desktop"]["runs"]), "PERF_TOKENS": str(tokens_file),
    }, tmp_path, timeout=180)
    assert not summary["failures"], "\n".join(summary["failures"])
    assert len(summary["pages"]) == 2 * len(BUDGETS["browser"]["pages"]), "Missing cold or warm page samples"
    assert code == 0 and not failures, "\n".join(failures) or f"k6 exited {code}; see {tmp_path / 'desktop.log'}"


def test_p44_production_gateway_uses_socket_address_for_rate_limit(perf_stack, perf_report):
    policy = BUDGETS["rate_limit"]["general"]
    results, elapsed, edge_probe = asyncio.run(request_burst("http://127.0.0.1:18080", policy, distinct_addresses=True))
    assert_burst(policy, results, elapsed, edge_probe, perf_report, "P4.4 real gateway")
