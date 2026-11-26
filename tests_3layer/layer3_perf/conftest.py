"""Shared fixtures and reporting for the Layer 3 performance suites."""

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from tests_3layer.layer2_cross_service.db_bootstrap import E2E_PORT, connect
from tests_3layer.layer2_cross_service.live import seed_user
from tests_3layer.support import auth_headers, eventually, response_data

from .probes import (
    BE, BUDGETS, GATEWAY, ORIGIN, cgroups, check_test_cluster, compose_exec, preflight, quiet_check, run_k6,
)

collect_ignore = []
if os.environ.get("TEST_LAYER3_ENABLED") != "1":
    collect_ignore.append("test_l3_perf_gate.py")
if os.environ.get("TEST_LAYER3_PERF_ENABLED") != "1":
    collect_ignore.append("test_l3_perf_load.py")


@pytest.fixture(scope="session")
def perf_stack(perf_report):
    assert os.environ.get("TEST_LAYER3_ENABLED") == "1", "Run BE/scripts/run_3layer_tests.ps1 -Layer 3"
    preflight()
    check_test_cluster()
    quiet_check()
    resources = cgroups()
    perf_report.details["initial_resources"] = resources
    return resources


@pytest.fixture(scope="session")
def gateway(perf_stack):
    with httpx.Client(base_url=GATEWAY, timeout=10, trust_env=False) as client:
        yield client


@pytest.fixture(scope="session")
def fe_server(perf_stack, tmp_path_factory):
    directory = tmp_path_factory.mktemp("perf-fe")
    env = {
        **os.environ, "NODE_ENV": "production", "PORT": "3000", "BACKEND_URL": GATEWAY,
        "FIREBASE_CONFIG": json.dumps({
            "apiKey": "perf-test-key", "authDomain": "perf.invalid",
            "projectId": "perf-test", "appId": "perf-test-app",
        }),
    }
    with (directory / "node.log").open("w+b") as log:
        process = subprocess.Popen(
            [os.environ["LEGAL_PERF_NODE"], "AI_Legal/src/index.js"],
            cwd=BE.parent / "FE", env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(base_url=ORIGIN, timeout=1, trust_env=False) as client:
                def ready():
                    assert process.poll() is None, (directory / "node.log").read_text(
                        encoding="utf-8", errors="replace",
                    )
                    try:
                        result = client.get("/api/app-config")
                    except httpx.TransportError:
                        return False
                    return result.status_code == 200 and result.json()["apiBaseUrl"] == GATEWAY

                eventually(ready, description="production FE on port 3000", timeout=15)
            yield ORIGIN
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


@pytest.fixture(scope="session")
def users_and_tokens(perf_stack):
    keys = Path(os.environ["LEGAL_TEST_KEYS_DIR"])
    subjects = [str(uuid4()) for _ in range(BUDGETS["load"]["users"])]
    with connect("user_db", E2E_PORT) as db, db.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO public.users (id, email, full_name, role) VALUES (%s, %s, %s, 'citizen')",
            [(subject, f"perf-{subject}@example.test", "Performance Test Citizen") for subject in subjects],
        )
    admin_id = str(seed_user(E2E_PORT, role="admin"))
    return {"citizens": subjects, "admin": admin_id, "keys": keys}


@pytest.fixture(scope="session")
def tokens_file(users_and_tokens, tmp_path_factory):
    # Give the soak and its warm-up enough time even with the nightly override.
    minutes = max(int(os.environ.get("SOAK_MINUTES", BUDGETS["load"]["soak"]["minutes"])) + 30, 45)
    keys = users_and_tokens["keys"]

    def token(subject, role):
        return auth_headers(keys, subject, role, minutes=minutes)["Authorization"].removeprefix("Bearer ")

    path = tmp_path_factory.mktemp("perf-tokens") / "tokens.json"
    path.write_text(json.dumps({
        "citizens": [token(subject, "citizen") for subject in users_and_tokens["citizens"]],
        "admin": token(users_and_tokens["admin"], "admin"),
    }), encoding="utf-8")
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture(scope="session")
def mint(users_and_tokens):
    return lambda subject, role="citizen": auth_headers(users_and_tokens["keys"], subject, role)


@pytest.fixture(scope="session")
def rag_corpus(gateway, mint, users_and_tokens, perf_report):
    headers = mint(users_and_tokens["admin"], "admin")
    path = "/api/rag/documents"
    document = response_data(gateway.post(path, headers=headers, json={
        "title": "Luật Đất đai 2024, Điều 9 — Phân loại đất",
        "doc_number": f"31/2024/QH15-perf-{uuid4().hex[:8]}",
        "doc_type": "luat",
        "issuing_body": "Quốc hội",
        "issued_date": "2024-01-18",
        "content": (
            "Điều 9. Phân loại đất\n"
            "1. Căn cứ vào mục đích sử dụng, đất đai được phân loại bao gồm nhóm đất nông nghiệp, "
            "nhóm đất phi nông nghiệp, nhóm đất chưa sử dụng.\n"
            "2. Nhóm đất nông nghiệp bao gồm các loại đất sau đây:\n"
            "a) Đất trồng cây hằng năm, gồm đất trồng lúa và đất trồng cây hằng năm khác.\n"
            "b) Đất trồng cây lâu năm.\n"
            "c) Đất lâm nghiệp, gồm đất rừng đặc dụng, đất rừng phòng hộ, đất rừng sản xuất.\n"
            "d) Đất nuôi trồng thủy sản.\n"
            "đ) Đất chăn nuôi tập trung.\n"
            "e) Đất làm muối.\n"
            "g) Đất nông nghiệp khác."
        ),
    }), 201)
    path = f"{path}/{document['id']}"
    response_data(gateway.post(f"{path}/approve", headers=headers), 202)

    def indexed():
        item = response_data(gateway.get(path, headers=headers), 200)
        assert item["ingest_status"] != "failed", item.get("ingest_error")
        return item if item["ingest_status"] == "done" else None

    item = eventually(indexed, description="rag-worker to index the legal corpus", timeout=60)
    assert item["status"] == "active" and item["is_processed"] and item["chunk_count"] > 0, item
    # The public /api/rag/search is a demo response. Probe actual retrieval from inside the stack.
    output = compose_exec("agent-service", "python", "-c", """
import json
import os
from urllib.request import Request, urlopen
url = os.environ["RAG_SERVICE_URL"] + "/internal/v1/rag/search"
body = json.dumps({"query": "Phân loại đất theo Điều 9 Luật Đất đai", "top_k": 20}).encode()
req = Request(url, data=body, headers={"Content-Type": "application/json",
                                       "X-Internal-Token": os.environ["INTERNAL_TOKEN"]})
with urlopen(req, timeout=10) as response:
    print(response.read().decode("utf-8"))
""")
    results = json.loads(output)["data"]["chunks"]
    assert results, "Fake embedding returned no searchable RAG chunks"
    perf_report.details["rag"] = {"indexed_chunks": item["chunk_count"], "retrieved_chunks": len(results)}
    return item


class PerfReport:
    def __init__(self):
        self.measurements = {}
        self.details = {}
        self.failures = []

    def measure(self, name, value, limit, relation="<="):
        passed = {"<=": value <= limit, "<": value < limit, "==": value == limit, ">=": value >= limit}[relation]
        self.measurements[name] = {"actual": value, "limit": limit, "relation": relation, "passed": passed}
        if not passed:
            self.failures.append(f"{name}: {value:.4g} {relation} {limit:.4g}")
        return passed

    def assert_passed(self):
        failures, self.failures = self.failures, []
        assert not failures, "\n".join(failures)

    def k6(self, name, script, env, directory, timeout):
        code, summary = run_k6(script, env, summary_path=directory / f"{name}.json", timeout=timeout)
        self.details[name] = summary
        failures = []
        for metric, entry in summary["metrics"].items():
            for expression, status in entry.get("thresholds", {}).items():
                if not status["ok"]:
                    failures.append(f"{metric}: {expression}; {entry['values']}")
        self.details[f"{name}_exit"] = code
        return code, summary, failures


@pytest.fixture(scope="session", autouse=True)
def perf_report(pytestconfig):
    report = PerfReport()
    pytestconfig._layer3_performance_report = report
    return report


def pytest_runtest_teardown(item):
    report = getattr(item.config, "_layer3_performance_report", None)
    if report is not None:
        report.failures.clear()


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    report = getattr(config, "_layer3_performance_report", None)
    if report is None:
        return
    terminalreporter.write_sep("=", "Layer 3 performance measurements")
    for name, row in report.measurements.items():
        terminalreporter.write_line(
            f"{name:48} {row['actual']:12.5g} {row['relation']:>2} {row['limit']:<10.5g} "
            f"{'PASS' if row['passed'] else 'FAIL'}"
        )
    for name, detail in report.details.items():
        if not isinstance(detail, dict):
            continue
        for metric, entry in detail.get("metrics", {}).items():
            for expression, status in entry.get("thresholds", {}).items():
                terminalreporter.write_line(
                    f"{name} {metric} {expression} {'PASS' if status['ok'] else 'FAIL'} {entry['values']}"
                )
    for service, estimate in report.details.get("P6.5 Render projections", {}).items():
        terminalreporter.write_line(
            f"P6.5 {service} Render free estimate {estimate['rps']:.1f} rps, "
            f"gap to target {estimate['gap_to_peak_rps']:.1f} rps"
        )
    directory = BE / ".perf-reports"
    directory.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc)
    run_id = os.environ.get("LEGAL_PERF_RUN_ID", now.strftime("%Y%m%dT%H%M%S%fZ"))
    assert re.fullmatch(r"\d{8}T\d{12,13}Z", run_id), "Invalid performance report timestamp"
    path = directory / f"{run_id}.json"
    tier = "load" if any(str(arg).endswith("test_l3_perf_load.py") for arg in config.args) else "gate"
    combined = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"timestamp": now.isoformat()}
    combined[tier] = {"exit": int(exitstatus), "metrics": report.measurements, "details": report.details}

    for previous in sorted(directory.glob("*.json"), reverse=True):
        if previous == path:
            continue
        try:
            prior_tier = json.loads(previous.read_text(encoding="utf-8")).get(tier, {})
        except (ValueError, OSError):
            continue
        if not prior_tier:
            continue
        observations = {key: row["actual"] for key, row in report.measurements.items()}
        prior = {key: row["actual"] for key, row in prior_tier["metrics"].items()}
        for name, detail in report.details.items():
            if isinstance(detail, dict):
                for metric, row in detail.get("metrics", {}).items():
                    if "p(95)" in row.get("values", {}):
                        name_metric = f"{name}.{metric}.p95"
                        observations[name_metric] = row["values"]["p(95)"]
                        previous_value = prior_tier["details"].get(name, {}).get("metrics", {}).get(
                            metric, {},
                        ).get("values", {}).get("p(95)")
                        if previous_value is not None:
                            prior[name_metric] = previous_value
        fraction = BUDGETS["resources"]["report_drift_fraction"]
        warnings = [
            f"{key}: {prior[key]:.5g} -> {value:.5g} compared with {previous.name}"
            for key, value in observations.items()
            if key in prior and prior[key] and value > prior[key] * (1 + fraction)
        ]
        combined[tier]["warnings"] = warnings
        for warning in warnings:
            terminalreporter.write_line(f"WARN {warning}")
        break

    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(combined, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)
    terminalreporter.write_line(str(path))
