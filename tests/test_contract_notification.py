"""Test offline: API hiện có và adapter gửi event; không cần DB/GitLab/Docker."""
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("service,prefix", [("contract", "contracts"), ("notification", "notifications")])
def test_health_matches_contract_example(service, prefix):
    directory = ROOT / "services" / f"{service}-service"
    module = load_module(directory / "app/main.py", f"check_{service}")
    spec = yaml.safe_load((directory / "openapi.yaml").read_text(encoding="utf-8"))
    operation = spec["paths"][f"/{prefix}/health"]["get"]
    response = TestClient(module.app).get(f"/api/v1/{prefix}/health")
    assert response.status_code == 200
    assert response.json() == operation["responses"]["200"]["content"]["application/json"]["example"]
    assert operation["x-status"] == "implemented"


def test_demo_templates_matches_contract_example(service_auth):
    directory = ROOT / "services/contract-service"
    module = load_module(directory / "app/main.py", "check_contract_demo")
    spec = yaml.safe_load((directory / "openapi.yaml").read_text(encoding="utf-8"))
    operation = spec["paths"]["/contracts/templates"]["get"]
    response = TestClient(module.app).get(
        "/api/v1/contracts/templates", headers={"X-Request-ID": "req-demo-001", "Authorization": f"Bearer {service_auth()}"}
    )
    assert response.status_code == 200
    assert response.json() == operation["responses"]["200"]["content"]["application/json"]["example"]
    assert operation["x-status"] == "partial"
    # Skeleton chỉ có request_id trong body, không hứa response header chưa có.
    assert "headers" not in operation["responses"]["200"]


@pytest.fixture(params=[("contract", "contracts"), ("notification", "notifications")])
def authenticated_service(request):
    service, prefix = request.param
    directory = ROOT / "services" / f"{service}-service"
    module = load_module(directory / "app/main.py", f"jwt_{service}")
    return module, TestClient(module.app), f"/api/v1/{prefix}"


@pytest.mark.parametrize("role", ["citizen", "lawyer", "admin"])
def test_jwt_valid_returns_identity(authenticated_service, service_auth, role):
    _, client, prefix = authenticated_service
    user_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    response = client.get(prefix + "/me", headers={
        "Authorization": f"Bearer {service_auth(sub=user_id, role=role)}",
        "X-Request-ID": "req-jwt-test",
    })
    assert response.status_code == 200
    assert response.json() == {
        "data": {"user_id": user_id, "role": role},
        "error": None, "request_id": "req-jwt-test",
    }


@pytest.mark.parametrize("case,code", [
    ("missing", "UNAUTHORIZED"), ("malformed", "UNAUTHORIZED"),
    ("expired", "TOKEN_EXPIRED"), ("issuer", "UNAUTHORIZED"),
    ("refresh", "UNAUTHORIZED"), ("signature", "UNAUTHORIZED"),
])
def test_jwt_rejected_with_shared_error(authenticated_service, service_auth, case, code):
    _, client, prefix = authenticated_service
    headers = {"X-Request-ID": "req-rejected"}
    if case != "missing":
        options = {}
        if case == "expired":
            options["exp"] = datetime.now(timezone.utc) - timedelta(minutes=1)
        elif case == "issuer":
            options["iss"] = "not-user-service"
        elif case == "refresh":
            options["type"] = "refresh"
        token = service_auth(**options)
        if case == "malformed":
            token = "not-a-jwt"
        elif case == "signature":
            head, body, signature = token.split(".")
            token = ".".join([head, body, ("A" if signature[0] != "A" else "B") + signature[1:]])
        headers["Authorization"] = f"Bearer {token}"
    response = client.get(prefix + "/me", headers=headers)
    assert response.status_code == 401
    assert response.json()["data"] is None
    assert response.json()["error"]["code"] == code
    assert response.json()["request_id"] == response.headers["X-Request-ID"] == "req-rejected"
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_router_role_guard(authenticated_service, service_auth):
    """Test gắn require_role vào router thật, không thêm endpoint test vào code service."""
    from fastapi import Depends
    from common_auth.fastapi_dep import require_role

    module, _, prefix = authenticated_service

    @module.authenticated_router.get("/admin-test", dependencies=[Depends(require_role("admin"))])
    def admin_only():
        return {"ok": True}

    module.app.include_router(module.authenticated_router)
    client = TestClient(module.app)
    for role, expected in [("citizen", 403), ("lawyer", 403), ("admin", 200)]:
        response = client.get(prefix + "/admin-test", headers={"Authorization": f"Bearer {service_auth(role=role)}"})
        assert response.status_code == expected
        if expected == 403:
            assert response.json()["error"]["code"] == "FORBIDDEN"


def test_templates_require_jwt():
    module = load_module(ROOT / "services/contract-service/app/main.py", "contract_no_token")
    response = TestClient(module.app).get("/api/v1/contracts/templates")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_templates_spec_requires_jwt():
    spec = yaml.safe_load((ROOT / "services/contract-service/openapi.yaml").read_text(encoding="utf-8"))
    operation = spec["paths"]["/contracts/templates"]["get"]
    assert operation.get("security", spec["security"]) == [{"BearerAuth": []}]
    assert "401" in operation["responses"]


@pytest.mark.parametrize("service", ["contract-service", "notification-service"])
def test_docker_only_mounts_public_key(service):
    config = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    volumes = config["services"][service]["volumes"]
    assert len(volumes) == 1
    assert volumes[0]["source"].endswith("/jwt_public.pem")
    assert volumes[0]["target"] == "/run/keys/jwt_public.pem"
    assert volumes[0]["read_only"] is True
    assert volumes[0]["bind"]["create_host_path"] is False


def test_internal_event_not_using_gateway_or_user_jwt():
    spec = yaml.safe_load((ROOT / "services/notification-service/openapi.yaml").read_text(encoding="utf-8"))
    operation = spec["paths"]["/internal/v1/notifications/events"]["post"]
    assert operation["servers"][0]["url"] == "http://notification-service:8000"
    assert operation["security"] == [{"InternalToken": []}]
    assert operation["x-status"] == "planned"
    for branch in spec["components"]["schemas"]["Event"]["oneOf"]:
        payload = branch["properties"]["payload"]
        if "required" in payload:
            assert payload["maxProperties"] == len(payload["required"])


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_SERVICE_URL", "http://notification-service:8000/")
    monkeypatch.setenv("INTERNAL_TOKEN", "fake-test-token")
    return load_module(ROOT / "contracts/examples/send_notification_event.py", "notification_adapter")


@pytest.mark.parametrize("scenario", ["success", "duplicate", "timeout_then_ok", "500_then_ok", "429_then_ok", "conflict", "unauthorized", "timeout", "bad_response"])
def test_event_delivery(adapter, scenario):
    event = json.loads((ROOT / "contracts/notification-event.example.json").read_text(encoding="utf-8"))
    original = copy.deepcopy(event)
    requests = []
    waits = []

    def handle(request):
        requests.append(request)
        assert str(request.url) == "http://notification-service:8000/internal/v1/notifications/events"
        assert request.headers["X-Internal-Token"] == "fake-test-token"
        assert request.headers["X-Request-ID"] == "req-test"
        assert json.loads(request.content) == original
        if scenario == "timeout" or (scenario == "timeout_then_ok" and len(requests) == 1):
            raise httpx.ReadTimeout("test timeout", request=request)
        if scenario in {"500_then_ok", "429_then_ok"} and len(requests) == 1:
            return httpx.Response(int(scenario.split("_")[0]), json={"error": "temporary"})
        if scenario in {"conflict", "unauthorized"}:
            return httpx.Response(409 if scenario == "conflict" else 401, json={"error": "test"})
        if scenario == "bad_response":
            return httpx.Response(200, json={"data": None})
        return httpx.Response(200, json={
            "data": {"event_id": event["event_id"], "is_duplicate": scenario == "duplicate", "created_count": 0 if scenario == "duplicate" else 1},
            "error": None, "request_id": "req-test",
        })

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        if scenario in {"conflict", "unauthorized", "timeout", "bad_response"}:
            with pytest.raises(adapter.NotificationDeliveryError) as caught:
                adapter.send_notification_event(event, "req-test", client=client, wait=waits.append)
            assert caught.value.retryable == (scenario in {"timeout", "bad_response"})
            assert len(requests) == (3 if caught.value.retryable else 1)
        else:
            result = adapter.send_notification_event(event, "req-test", client=client, wait=waits.append)
            assert result["data"]["event_id"] == event["event_id"]
            assert result["data"]["is_duplicate"] == (scenario == "duplicate")
            assert len(requests) == (2 if scenario.endswith("then_ok") else 1)
    assert event == original
    assert waits == [0.5, 1.0][:len(requests) - 1]


def test_event_config_required(adapter, monkeypatch):
    monkeypatch.delenv("INTERNAL_TOKEN")
    with pytest.raises(adapter.NotificationDeliveryError) as caught:
        adapter.send_notification_event({"event_id": "fake"}, "req-test")
    assert caught.value.code == "MISSING_NOTIFICATION_CONFIG"
    assert caught.value.retryable is False
