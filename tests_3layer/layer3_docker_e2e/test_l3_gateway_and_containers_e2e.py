import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import httpx
import pytest

from tests_3layer.layer2_cross_service.db_bootstrap import E2E_PORT
from tests_3layer.layer2_cross_service.live import seed_user
from tests_3layer.support import BE, INTERNAL_TOKEN, auth_headers, eventually, reference_pdf, response_data

GATEWAY = "http://127.0.0.1:18080"
ORIGIN = "http://localhost:3000"
CATALOG = "/api/contracts/reference-catalog"
CONVERSATIONS = "/api/agent/conversations"
pytestmark = pytest.mark.skipif(os.getenv("TEST_LAYER3_ENABLED") != "1", reason="Run the disposable stack with -Layer 3")


@pytest.fixture
def gateway():
    with httpx.Client(base_url=GATEWAY, timeout=20, trust_env=False) as client:
        yield client


@pytest.fixture
def mint():
    return lambda uid, role: auth_headers(Path(os.environ["LEGAL_TEST_KEYS_DIR"]), uid, role)


def compose_exec(service, *command):
    result = subprocess.run([
        "docker", "compose", "--env-file", os.environ["LEGAL_TEST_COMPOSE_ENV_FILE"],
        "-p", "legal-test-e2e", "-f", str(BE / "docker-compose.test-e2e.yml"),
        "exec", "-T", service, *command,
    ], cwd=BE, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=50)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.mark.parametrize("service", [
    "agent", "users", "lawyers", "procedures", "rag", "contracts", "documents",
    "locations", "consultations", "notifications",
])
def test_all_gateway_health_routes(gateway, service):
    response = gateway.get(f"/api/{service}/health")
    assert response.status_code == 200, response.text
    assert "application/json" in response.headers["Content-Type"]


@pytest.mark.parametrize("route", [
    "/internal/v1/users/lookup",
    "/internal/v1/procedures/register",
    "/api/users/internal/v1/users/lookup",
    "/api/procedures/internal/v1/procedures/register",
])
def test_gateway_keeps_internal_routes_unreachable(gateway, route):
    response = gateway.post(route, headers={"X-Internal-Token": INTERNAL_TOKEN}, json={"user_ids": []})
    assert response.status_code in (403, 404), response.text


def test_gateway_roundtrips_a_valid_request_id(gateway):
    request_id = "BrowserBrowser.test_15:span"
    response = gateway.get("/api/contracts/me", headers={"X-Request-ID": request_id})
    assert response.status_code == 401, response.text
    assert response.headers["X-Request-ID"] == request_id
    assert response.json()["request_id"] == request_id


@pytest.mark.parametrize("value", ["before?<bad>\tafter", "synthetic-" + "h" * 5000])
def test_gateway_returns_safe_request_id_for_dirty_input(gateway, value):
    response = gateway.get("/api/contracts/me", headers={"X-Request-ID": value})
    assert response.status_code == 401, response.text
    cleaned = response.headers["X-Request-ID"]
    assert re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", cleaned)
    assert response.json().get("request_id") == cleaned


def test_gateway_cors_preflight_has_exact_origin_and_chat_headers(gateway):
    response = gateway.options(f"{CONVERSATIONS}/11111111-1111-4111-8111-111111111111/messages/stream", headers={
        "Origin": ORIGIN, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization, idempotency-key, content-type, x-request-id",
    })
    assert response.status_code == 204 and not response.content
    assert response.headers.get_list("Access-Control-Allow-Origin") == [ORIGIN]
    assert response.headers.get_list("Access-Control-Allow-Credentials") == ["true"]
    assert "POST" in response.headers["Access-Control-Allow-Methods"].split(", ")
    assert {"content-type", "authorization", "idempotency-key", "x-request-id"} <= {
        header.strip().lower() for header in response.headers["Access-Control-Allow-Headers"].split(",")
    }
    assert response.headers["Access-Control-Max-Age"] == "7200"


@pytest.mark.parametrize("path,status", [("/api/contracts/me", 401), (f"{CATALOG}?page=0", 422)])
def test_gateway_cors_is_present_on_errors(gateway, path, status):
    response = gateway.get(path, headers={"Origin": ORIGIN})
    assert response.status_code == status, response.text
    assert response.headers.get_list("Access-Control-Allow-Origin") == [ORIGIN]
    assert response.headers.get_list("Access-Control-Allow-Credentials") == ["true"]
    assert "origin" in response.headers["Vary"].lower()


def test_pdf_upload_registers_procedure_and_purge_removes_catalog_entries(gateway, mint):
    admin = mint(seed_user(E2E_PORT, role="admin"), "admin")
    next_code = response_data(gateway.get(f"{CATALOG}/next-code", params={"domain": "land"}, headers=admin), 200)
    assert next_code["domain"] == "land" and re.fullmatch(r"DD-\d{3}", next_code["code"])
    pdf = reference_pdf("Gateway PDF with visible text")
    item = response_data(gateway.post(CATALOG, headers=admin,
                                      data={"name": "Gateway registration PDF", "domain": "land"},
                                      files={"file": ("sample.pdf", pdf, "application/pdf")}), 201)
    procedure_id, item_id, code = item["procedure_id"], item["id"], item["procedure_code"]
    assert code == next_code["code"]
    spec = response_data(gateway.get(f"{CATALOG}/{item_id}"), 200)
    assert spec["procedure_id"] == procedure_id and spec["page_count"] == 1
    asset = f"/api/contracts/reference-assets/{item_id}"
    original = gateway.get(f"{asset}.pdf")
    assert original.status_code == 200 and original.content == pdf
    rendered_page = gateway.get(f"{asset}-page-1.png")
    assert rendered_page.status_code == 200 and rendered_page.content.startswith(b"\x89PNG\r\n\x1a\n")

    deleted = gateway.delete(f"/api/procedures/admin/{procedure_id}", headers=admin)
    assert deleted.status_code == 200, deleted.text

    def removed():
        detail = gateway.get(f"{CATALOG}/{item_id}")
        assert detail.status_code in (200, 404), detail.text
        return detail.status_code == 404

    eventually(removed, description="procedure.purged reaches the Contract catalog", timeout=10)
    catalog = response_data(gateway.get(CATALOG, params={"domain": "land", "procedure": code}), 200)
    assert catalog["items"] == []
    assert all(entry.get("procedure_id") != procedure_id for entry in catalog["classifications"])
    assert gateway.get(f"{asset}.pdf").status_code == 404
    assert gateway.get(f"{asset}-page-1.png").status_code == 404


@pytest.mark.parametrize("method", [
    "test_system_pdf_fills_a_blank_area",
    "test_system_pdf_replaces_reviewed_dots_without_erasing_label",
])
def test_contract_system_pdf_regressions_run_with_linux_font(method):
    script = (
        "from pathlib import Path; from app.services.personal_renderer import FONT; "
        "assert Path(FONT).is_file(), FONT; print(FONT)"
    )
    assert "LiberationSerif-Regular.ttf" in compose_exec("contract-service", "python", "-c", script)
    compose_exec(
        "contract-service", "python", "-m", "unittest", "-v",
        f"test_system_pdf_source.SystemPdfSourceTests.{method}",
    )


def read_sse(client, path, headers, content):
    frames = []
    fields = {}
    with client.stream("POST", path, headers=headers, json={"content": content}) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["Content-Type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if not line:
                if fields:
                    assert set(fields) == {"event", "data"}, fields
                    frames.append((fields["event"], json.loads(fields["data"])))
                    fields = {}
                continue
            if line.startswith(":"):
                continue
            key, separator, value = line.partition(":")
            assert separator and key not in fields, line
            fields[key] = value.removeprefix(" ")
    assert not fields, "Stream ended without a complete SSE frame"
    assert frames and frames[-1][0] == "done", frames
    assert sum(event == "done" for event, _ in frames) == 1
    assert all(event in {"delta", "status", "done"} for event, _ in frames)
    return frames


def test_agent_sse_frames_and_idempotency_survive_gateway_and_redis(gateway, mint):
    subject = seed_user(E2E_PORT)
    headers = {**mint(subject, "citizen"), "Idempotency-Key": "sse-replay-key"}
    cid = response_data(gateway.post(CONVERSATIONS, json={}, headers=headers), 201)["id"]
    route = f"{CONVERSATIONS}/{cid}/messages/stream"
    question = "Thủ tục sang tên sổ đỏ cần những giấy tờ gì?"
    original = read_sse(gateway, route, headers, question)
    assert any(event == "delta" and data["text"] for event, data in original)
    assistant_id = original[-1][1]["assistant_message"]["id"]
    replay = read_sse(gateway, route, headers, question)
    assert replay[-1][1]["assistant_message"]["id"] == assistant_id
    assert len(response_data(gateway.get(f"{CONVERSATIONS}/{cid}", headers=headers), 200)["messages"]) == 2
    # Verify the real Redis record through the test stack, without publishing a Redis port.
    slot = json.dumps([str(subject), cid, headers["Idempotency-Key"]], ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"))
    redis_key = f"legal:idempotency:{hashlib.sha256(slot.encode()).hexdigest()}"
    cached = compose_exec("redis", "redis-cli", "--raw", "GET", redis_key).strip()
    assert cached, "Redis has no SSE idempotency record"
    entry = json.loads(cached)
    assert entry["data"]["assistant_message"]["id"] == assistant_id


def test_user_block_event_revokes_an_unexpired_jwt_in_three_services(gateway, mint):
    subject = seed_user(E2E_PORT)
    headers = mint(subject, "citizen")
    admin = mint(seed_user(E2E_PORT, role="admin"), "admin")
    probes = {
        "/api/rag/documents": 403,  # RAG authorizes this route only for admins.
        "/api/contracts/me": 200,
        "/api/documents/me": 200,
    }
    for route, expected in probes.items():
        response = gateway.get(route, headers=headers)
        assert response.status_code == expected, (route, response.text)
    response_data(gateway.post(f"/api/users/{subject}/lock", headers=admin,
                               json={"reason": "Synthetic administrative lock", "duration_days": 1}), 200)

    last = {}

    def revoked():
        for route in probes:
            last[route] = gateway.get(route, headers=headers)
        return all(response.status_code == 401 for response in last.values())

    try:
        eventually(revoked, description="the unexpired JWT is rejected by RAG, Contract and Document", timeout=5)
    except AssertionError as error:
        raise AssertionError({path: (res.status_code, res.text) for path, res in last.items()}) from error
    for response in last.values():
        assert response.json()["error"]["code"] == "UNAUTHORIZED"
