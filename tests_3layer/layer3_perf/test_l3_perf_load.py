"""Tier B arrival-rate, stream concurrency, browser and restart budgets."""

import asyncio
import json
import os
import re
import time
from collections import Counter
from contextlib import aclosing
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from tests_3layer.layer2_cross_service.db_bootstrap import E2E_PORT, connect
from tests_3layer.support import eventually, response_data

from .probes import (
    APP_SERVICES, BUDGETS, GATEWAY, ConnSampler, cgroup, cgroups, compose, docker, percentile, query_delta, quiet_check,
    redis_info,
)
from .test_l3_perf_gate import (
    CONVERSATIONS, PROCEDURES, SELECTS, TABLES, chat_path, elapsed_ms, notification_events, perf_headers, question,
    sse_events,
)

RESOURCES = BUDGETS["resources"]
LOAD = BUDGETS["load"]


def read_metric(summary, name, key):
    values = summary["metrics"].get(name, {}).get("values", {})
    assert key in values, f"Missing sample: {name}.{key}"
    return values[key]


def check_resources(report, label, sampler, usage, redis):
    report.details[f"{label} connection samples"] = sampler.samples
    for name, value in sampler.peaks.items():
        report.measure(f"{label} {name} peak connections", value, min(
            RESOURCES["connections"][name], RESOURCES["connection_ceiling"],
        ))
    for name, value in sampler.api_peaks.items():
        report.measure(f"{label} {name} API peak connections", value, RESOURCES["api_connections_with_workers"][name])
    for name, value in usage.items():
        if name in APP_SERVICES:
            report.measure(f"{label} {name} peak bytes", value["peak"], RESOURCES["peak_bytes"])
        else:
            report.details[f"{label} {name} memory (report only)"] = value
    report.measure(f"{label} Redis peak bytes", redis["used_memory_peak"], RESOURCES["redis_peak_bytes"])
    report.measure(f"{label} Redis evictions", redis["evicted_keys"], RESOURCES["redis_evictions"], "==")


def container_states():
    ids = compose("ps", "-q").splitlines()
    assert ids, "No Layer 3 containers for lifecycle probe"
    containers = json.loads(docker("inspect", *ids))
    return {
        item["Config"]["Labels"]["com.docker.compose.service"]: {
            "id": item["Id"], "restarts": item["RestartCount"], "started": item["State"]["StartedAt"],
            "health": item["State"].get("Health", {}).get("Status", item["State"]["Status"]),
            "probes": item["State"].get("Health", {}).get("Log") or [],
        }
        for item in containers
    }


def docker_time(value):
    # Docker emits RFC3339Nano (variable fraction digits, "Z"); normalise for datetime.fromisoformat.
    return datetime.fromisoformat(
        re.sub(r"\.(\d+)", lambda match: "." + match[1][:6].ljust(6, "0"), value).replace("Z", "+00:00"),
    )


def failed_probes(states, since):
    # shortcut: interval is 30s, so each container yields only ~2-3 probes per spike (Docker keeps the last 5);
    # a failure between probes is missed. Lower interval for the spike run if P3.3 needs finer evidence.
    return sum(
        probe["ExitCode"] != 0 and docker_time(probe["Start"]) >= since
        for state in states.values() for probe in state["probes"]
    )


def count_unhealthy(since):
    until = str(time.time())
    events = docker(
        "events", "--since", since, "--until", until,
        "--filter", "type=container", "--filter", "label=com.docker.compose.project=legal-test-e2e",
        "--format", "{{json .}}",
    )
    return sum(json.loads(line)["Action"] == "health_status: unhealthy" for line in events.splitlines())


@pytest.fixture(scope="session")
def baseline_measurements():
    return {}


@pytest.fixture(scope="session")
def load_data(users_and_tokens):
    subjects = users_and_tokens["citizens"]
    with connect("procedure_db", E2E_PORT) as db:
        result = db.execute("""
            INSERT INTO land_procedure_records (user_id, procedure_code, procedure_id, applicant_name)
            SELECT subject::uuid, p.code, p.id, 'Performance Test Citizen'
            FROM procedures p CROSS JOIN unnest(%s::text[]) AS subject WHERE p.code = 'DD-001'
            """, (subjects,))
        assert result.rowcount == len(subjects), "Missing DD-001 procedure fixture"
    with connect("notification_db", E2E_PORT) as db:
        db.execute("""
            INSERT INTO notifications (id, user_id, event_type, title, message)
            SELECT gen_random_uuid()::text, subject, 'system', 'Performance test', 'Local load fixture.'
            FROM unnest(%s::text[]) AS subject
            """, (subjects,))


def run_load(name, tokens_file, directory, report, extra=None):
    quiet_check()
    policy = LOAD[name]
    seconds = {
        "baseline": LOAD["baseline"]["seconds"],
        "domains": LOAD["domains"]["seconds"],
        "stress": LOAD["stress"]["seconds"],
        "spike": sum(value for key, value in LOAD["spike"].items() if key.endswith("_seconds")),
        "soak": 60 * int(os.environ.get("SOAK_MINUTES", LOAD["soak"]["minutes"])),
    }[name]
    before = cgroups() if name in {"baseline", "soak"} else None
    lifecycle = container_states() if name == "spike" else None
    started = str(time.time())
    started_at = datetime.now(timezone.utc)
    with ConnSampler() as sampler:
        result = report.k6(name, "api_load.js", {
            "SCENARIO": name, "PERF_TOKENS": str(tokens_file), **(extra or {}),
        }, directory, timeout=seconds + LOAD["warmup_seconds"] + 60)
    if lifecycle is not None:
        final_states = container_states()
        assert final_states.keys() == lifecycle.keys(), "Layer 3 containers changed during spike"
        changed = sum(
            final_states[key]["id"] != item["id"]
            or final_states[key]["started"] != item["started"]
            or final_states[key]["restarts"] != item["restarts"]
            for key, item in lifecycle.items()
        )
        unhealthy = count_unhealthy(started) + sum(state["health"] != "healthy" for state in final_states.values())
        report.measure("P3.3 restarted containers", changed, policy["restarts"], "==")
        report.measure("P3.3 unhealthy containers or events", unhealthy, policy["unhealthy"], "==")
        # retries x interval (~90s) outlasts the spike, so status alone cannot turn unhealthy in time.
        report.measure("P3.3 failed health probes", failed_probes(final_states, started_at), policy["unhealthy"], "==")
    after = cgroups(before)
    check_resources(report, name, sampler, after, redis_info())
    code, summary, threshold_failures = result
    return before, after, summary, code, threshold_failures


def assert_load_result(code, threshold_failures, perf_report):
    problems = [*threshold_failures, *perf_report.failures]
    perf_report.failures.clear()
    assert code == 0 and not problems, "\n".join(problems) or f"k6 exited {code}"


CPU_SERVICES = ("procedure", "user", "notification", "consultation", "lawyer", "contract")


def idle_cpu_usec_per_second():
    """Background CPU (outbox poll, healthchecks) of each measured service on a quiet stack."""
    quiet_check()
    names = [f"{service}-service" for service in CPU_SERVICES]
    start = cgroups(names)
    time.sleep(RESOURCES["cpu_idle_seconds"])
    end = cgroups(names)
    return {
        name: max(0, end[name]["cpu_usec"] - start[name]["cpu_usec"]) / (end[name]["at"] - start[name]["at"])
        for name in names
    }


def test_p31_p65_p66_baseline(perf_stack, load_data, tokens_file, tmp_path, perf_report, baseline_measurements):
    idle = idle_cpu_usec_per_second()
    perf_report.details["P6.5 idle CPU usec/s"] = idle
    before, after, summary, code, failures = run_load("baseline", tokens_file, tmp_path, perf_report)
    baseline_measurements["p95"] = read_metric(summary, "perf_latency{phase:measure}", "p(95)")
    projections = {}
    for service in CPU_SERVICES:
        # The cgroup interval includes warm-up. Count its requests as well so
        # the numerator and denominator cover the same traffic.
        count = sum(read_metric(summary, f"perf_requests{{phase:{phase},service:{service}}}", "count")
                    for phase in ("warmup", "measure"))
        container = f"{service}-service"
        used = after[container]["cpu_usec"] - before[container]["cpu_usec"]
        background = idle[container] * (after[container]["at"] - before[container]["at"])
        assert count > 0 and used > background, (
            f"{container}: load CPU {used} usec does not exceed idle background {background:.0f} usec"
        )
        cpu_ms = (used - background) / (count * 1000)
        group = "cached" if service == "contract" else "db_read"
        perf_report.measure(f"P6.5 {service} CPU ms/request", cpu_ms, RESOURCES["cpu_ms"][group])
        capacity = RESOURCES["render_cpu_ms_per_second"] * RESOURCES["render_utilization"] / cpu_ms
        projections[service] = {"rps": capacity, "gap_to_peak_rps": RESOURCES["target_rps"] - capacity}
    perf_report.details["P6.5 Render projections"] = projections
    assert_load_result(code, failures, perf_report)


def test_p35_domains_pool_saturation(perf_stack, tokens_file, tmp_path, perf_report):
    _, _, _, code, failures = run_load("domains", tokens_file, tmp_path, perf_report)
    assert_load_result(code, failures, perf_report)


def test_p32_stress_ramp(perf_stack, tokens_file, tmp_path, perf_report):
    _, _, summary, code, failures = run_load("stress", tokens_file, tmp_path, perf_report)
    policy = LOAD["stress"]
    stages = {
        target: read_metric(summary, f"perf_latency{{phase:measure,window:{target}}}", "p(95)")
        for target in policy["targets"]
    }
    knee = next((target for target, p95 in stages.items() if p95 > policy["knee_ms"]), None)
    perf_report.details["P3.2 stress stages"] = {"p95_ms": stages, "first_over_budget_rps": knee}
    perf_report.measure("P3.2 stress knee or highest stage rps", knee or policy["targets"][-1], policy["min_knee_rps"], ">=")
    assert_load_result(code, failures, perf_report)


def test_p33_spike_and_recovery(perf_stack, tokens_file, tmp_path, perf_report, baseline_measurements):
    assert "p95" in baseline_measurements, "Baseline p95 is required for the spike recovery comparison"
    _, _, _, code, failures = run_load("spike", tokens_file, tmp_path, perf_report, {
        "PERF_BASELINE_P95": str(baseline_measurements["p95"]),
    })
    assert_load_result(code, failures, perf_report)


async def run_chat_round(subjects, mint):
    limits = httpx.Limits(max_connections=len(subjects), max_keepalive_connections=len(subjects))
    async with httpx.AsyncClient(base_url=GATEWAY, trust_env=False, timeout=30, limits=limits) as client:
        headers = [perf_headers(mint(subject), f"10.93.1.{index + 1}") for index, subject in enumerate(subjects)]
        conversations = await asyncio.gather(*(
            client.post(CONVERSATIONS, json={}, headers=header) for header in headers
        ))
        paths = [chat_path(response_data(response, 201)["id"]) for response in conversations]
        start = asyncio.Event()

        async def stream(path, header):
            await start.wait()
            sent = time.perf_counter()
            first = None
            done = 0
            last = None
            async with client.stream("POST", path, headers=header, json={"content": question()}) as response:
                assert response.status_code == 200, await response.aread()
                assert response.headers["Content-Type"].startswith("text/event-stream")
                assert "Content-Encoding" not in response.headers
                async with aclosing(sse_events(response)) as events:
                    async for event, payload in events:
                        last = event
                        assert event in {"status", "delta", "done"}, (event, payload)
                        if event == "delta" and first is None and payload["text"]:
                            first = elapsed_ms(sent)
                        if event == "done":
                            done += 1
                            assert payload["assistant_message"]["metadata"]["llm_bypass"]
            assert first is not None and done == 1 and last == "done", "Incomplete chat stream"
            return first

        tasks = [asyncio.create_task(stream(path, header)) for path, header in zip(paths, headers)]
        start.set()
        return await asyncio.gather(*tasks, return_exceptions=True)


def test_p54_fifty_chat_streams(rag_corpus, users_and_tokens, mint, perf_report):
    policy = BUDGETS["sse"]["chat"]
    quiet_check()
    count = policy["streams"]
    samples = []
    errors = []
    with ConnSampler() as sampler:
        for index in range(policy["rounds"]):
            subjects = users_and_tokens["citizens"][index * count:(index + 1) * count]
            assert len(subjects) == count
            results = asyncio.run(run_chat_round(subjects, mint))
            successful = [value for value in results if not isinstance(value, BaseException)]
            samples.extend(successful)
            errors.extend(repr(value) for value in results if isinstance(value, BaseException))
            perf_report.details[f"P5.4 round {index + 1} TTFD ms"] = successful
    check_resources(perf_report, "chat SSE", sampler, cgroups(), redis_info())
    perf_report.details["P5.4 errors"] = errors
    perf_report.measure("P5.4 chat stream errors", len(errors), policy["errors"], "==")
    if samples:
        perf_report.measure("P5.4 chat TTFD p95 ms", percentile(samples, 95), policy["p95_ms"])
        perf_report.measure("P5.4 chat TTFD p99 ms", percentile(samples, 99), policy["p99_ms"])
    perf_report.assert_passed()


async def run_notification_streams(users_and_tokens, mint):
    policy = BUDGETS["sse"]["notification"]
    count = policy["streams"]
    participants = users_and_tokens["citizens"][:count]
    assert len(participants) == count
    title = f"Performance broadcast {uuid4().hex}"
    connected = asyncio.Queue()
    broadcast_at = None
    hold_until = None
    limits = httpx.Limits(max_connections=count + 32, max_keepalive_connections=32)
    timeout = httpx.Timeout(10, read=35)
    async with httpx.AsyncClient(base_url=GATEWAY, trust_env=False, timeout=timeout, limits=limits) as client:
        async def subscriber(index, subject):
            headers = perf_headers(mint(subject), f"10.94.1.{index + 1}")
            started = time.perf_counter()
            first = None
            keepalives = 0
            async with notification_events(client, headers) as events:
                event, payload = await anext(events)
                assert event == "connected" and payload["user_id"] == subject, (event, payload)
                established = elapsed_ms(started)
                connected.put_nowait(established)
                while True:
                    wait = max(0, hold_until - time.perf_counter()) if hold_until else 35
                    if not wait:
                        break
                    try:
                        event, payload = await asyncio.wait_for(anext(events), timeout=wait)
                    except asyncio.TimeoutError:
                        assert hold_until is not None and time.perf_counter() >= hold_until, "SSE keep-alive timed out"
                        break
                    except StopAsyncIteration:
                        raise AssertionError(f"Notification stream for {subject} ended early") from None
                    if event == "keep-alive":
                        keepalives += 1
                    if event == "notification" and payload.get("title") == title and first is None:
                        first = elapsed_ms(broadcast_at)
            return first, keepalives

        async def side_traffic():
            async def call(index):
                at = time.perf_counter()
                headers = perf_headers(ip=f"10.94.2.{index % 200 + 1}")
                response = await client.get(f"{PROCEDURES}/domains", headers=headers)
                result = response_data(response, 200)
                assert isinstance(result, list) and result
                return elapsed_ms(at)

            origin = time.perf_counter()
            tasks = []
            for index in range(policy["hold_seconds"] * policy["api_rps"]):
                scheduled = origin + index / policy["api_rps"]
                await asyncio.sleep(max(0, scheduled - time.perf_counter()))
                tasks.append(asyncio.create_task(call(index)))
            return await asyncio.gather(*tasks, return_exceptions=True)

        listeners = [asyncio.create_task(subscriber(index, user)) for index, user in enumerate(participants)]
        side = None
        resource_sample = None
        try:
            async def wait_for_connections():
                values = []
                while len(values) < count:
                    # Surface connection failures without waiting for the queue deadline.
                    for listener in listeners:
                        if listener.done():
                            listener.result()
                    try:
                        values.append(await asyncio.wait_for(connected.get(), timeout=0.25))
                    except asyncio.TimeoutError:
                        continue
                return values

            established = await asyncio.wait_for(wait_for_connections(), timeout=15)
            hold_until = time.perf_counter() + policy["hold_seconds"]
            side = asyncio.create_task(side_traffic())
            broadcast_at = time.perf_counter()
            response_data(await client.post("/api/admin/broadcasts", headers=perf_headers(
                mint(users_and_tokens["admin"], "admin"),
            ), json={
                "title": title, "content": "Local notification stream performance check.",
                "audience": "citizen", "channels": ["in_app"],
            }), 202)
            resource_sample = asyncio.create_task(asyncio.to_thread(cgroup, "notification-service"))
            streams, calls, memory = await asyncio.gather(
                asyncio.gather(*listeners, return_exceptions=True), side, resource_sample,
            )
            return established, streams, calls, memory
        finally:
            pending = listeners + [task for task in (side, resource_sample) if task is not None]
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


def test_p55_two_hundred_notification_streams(perf_stack, users_and_tokens, mint, perf_report):
    policy = BUDGETS["sse"]["notification"]
    quiet_check()
    initial = cgroup("notification-service")
    with ConnSampler() as sampler:
        connected, streams, calls, memory = asyncio.run(run_notification_streams(users_and_tokens, mint))
    usage = cgroups()
    check_resources(perf_report, "notification SSE", sampler, usage, redis_info())
    fanout = [value[0] for value in streams if not isinstance(value, BaseException) and value[0] is not None]
    losses = [repr(value) for value in streams if isinstance(value, BaseException)]
    heartbeat_counts = Counter(value[1] for value in streams if not isinstance(value, BaseException))
    side_errors = [repr(value) for value in calls if isinstance(value, BaseException)]
    perf_report.details["P5.5 stream diagnostics"] = {
        "dropped": losses, "keepalive_counts": dict(heartbeat_counts), "side_api_errors": side_errors,
    }
    perf_report.measure("P5.5 connected p95 ms", percentile(connected, 95), policy["connect_p95_ms"])
    perf_report.measure("P5.5 fan-out fraction", len(fanout) / policy["streams"], policy["fanout_fraction"], "==")
    if fanout:
        perf_report.measure("P5.5 fan-out p95 ms", percentile(fanout, 95), policy["fanout_p95_ms"])
    perf_report.measure("P5.5 dropped streams", len(losses), policy["drops"], "==")
    perf_report.measure("P5.5 streams missing keep-alives", sum(
        count for observed, count in heartbeat_counts.items() if observed < policy["keepalives"]
    ), 0, "==")
    perf_report.measure("P5.5 side API errors", len(side_errors), 0, "==")
    successful = [value for value in calls if not isinstance(value, BaseException)]
    if successful:
        perf_report.measure("P5.5 side API p95 ms", percentile(successful, 95), policy["api_p95_ms"])
    growth = max(
        0, memory["working_set"] - initial["working_set"], usage["notification-service"]["peak"] - initial["peak"],
    )
    perf_report.measure("P5.5 notification memory growth bytes/stream", growth / policy["streams"], policy["memory_bytes_per_stream"])
    perf_report.assert_passed()


def test_p34_soak_drift_and_memory(perf_stack, tokens_file, tmp_path, perf_report):
    before, after, summary, code, failures = run_load("soak", tokens_file, tmp_path, perf_report)
    policy = LOAD["soak"]
    first, last = [read_metric(summary, f"perf_latency{{phase:measure,window:{window}}}", "p(95)")
                   for window in ("first", "last")]
    assert first > 0, f"Missing first-minute p95: {first}"
    perf_report.measure("P3.4 soak p95 drift fraction", (last - first) / first, policy["p95_drift_fraction"])
    for service, usage in before.items():
        if service not in APP_SERVICES:
            perf_report.details[f"P3.4 {service} memory (report only)"] = {"before": usage, "after": after[service]}
            continue
        growth = (after[service]["working_set"] - usage["working_set"]) / usage["working_set"]
        perf_report.measure(f"P3.4 {service} memory growth fraction", growth, policy["memory_growth_fraction"])
    assert_load_result(code, failures, perf_report)


def check_browser(report, name, env, directory, samples, timeout):
    code, summary, failures = report.k6(name, "browser_pages.js", env, directory, timeout)
    assert not summary["failures"], "\n".join(summary["failures"])
    assert len(summary["pages"]) == samples, "Missing browser samples"
    assert code == 0 and not failures, "\n".join(failures) or f"k6 exited {code}; see {directory / (name + '.log')}"


def test_p12_p13_p14_mobile_browser(fe_server, tokens_file, tmp_path, perf_report):
    profile = BUDGETS["browser"]["mobile"]
    expected = profile["runs"] * len(BUDGETS["browser"]["pages"]) * 3
    quiet_check()
    check_browser(perf_report, "mobile", {
        "PROFILE": "mobile", "RUNS": str(profile["runs"]), "PERF_TOKENS": str(tokens_file),
    }, tmp_path, samples=expected, timeout=int(profile["max_duration"].removesuffix("m")) * 60 + 60)


def test_p15_lazy_chat_and_first_visible_answer(fe_server, rag_corpus, tokens_file, tmp_path, perf_report):
    check_browser(perf_report, "chat_journey", {
        "PROFILE": "desktop", "RUNS": "1", "PERF_JOURNEY": "1", "PERF_TOKENS": str(tokens_file),
    }, tmp_path, samples=1, timeout=90)


def test_p67_procedure_cold_start_runs_last(gateway, perf_report):
    limits = RESOURCES["cold_start"]
    started = time.perf_counter()
    compose("restart", "procedure-service", timeout=45)

    def healthy():
        container = compose("ps", "-q", "procedure-service").strip()
        return container and docker("inspect", "--format", "{{.State.Health.Status}}", container).strip() == "healthy"

    eventually(healthy, description="procedure-service to become healthy after restart", timeout=60)
    perf_report.measure("P6.7 procedure healthy ms", elapsed_ms(started), limits["healthy_ms"])
    path = f"{PROCEDURES}?page_size=20&domain=land"
    headers = perf_headers()

    def request():
        return response_data(gateway.get(path, headers=headers), 200)

    first, cold_queries, cold_ms = query_delta("procedure_db", SELECTS, TABLES["catalog"], request, control=False)
    second, warm_queries, warm_ms = query_delta("procedure_db", SELECTS, TABLES["catalog"], request, control=False)
    assert first["items"] and first == second
    perf_report.measure("P6.7 cold queries observed", cold_queries, 1, ">=")
    perf_report.measure("P6.7 cold queries", cold_queries, RESOURCES["queries"]["procedure_cold"])
    perf_report.measure("P6.7 warm queries", warm_queries, RESOURCES["queries"]["procedure_warm"], "==")
    perf_report.measure("P6.7 first request ms", cold_ms, limits["first_ms"])
    perf_report.measure("P6.7 second request ms", warm_ms, limits["warm_ms"])
    perf_report.assert_passed()
