"""Response thật khớp schema trong openapi.yaml (contract first). Bỏ qua nếu chưa cài openapi-schema-validator."""

from pathlib import Path

import pytest
import yaml

SPEC = yaml.safe_load((Path(__file__).resolve().parents[1] / "openapi.yaml").read_text(encoding="utf-8"))
INTERNAL = {"X-Internal-Token": "test-internal-token"}
METHODS = {"get", "post", "put", "patch", "delete"}


def _validate(body, schema_name):
    validator = pytest.importorskip("openapi_schema_validator")
    from jsonschema import RefResolver
    validator.OAS30Validator(SPEC["components"]["schemas"][schema_name],
                             resolver=RefResolver.from_schema(SPEC)).validate(body)


def _ops(status):
    return {(m, p) for p, item in SPEC["paths"].items() for m, op in item.items()
            if m in METHODS and op["x-status"] == status}


def test_operation_da_code_deu_implemented():
    from app.main import app

    planned = {SPEC["paths"][p][m]["operationId"] for m, p in _ops("planned")}
    assert planned == {"listLawyers", "getLawyer", "getMyLawyerStats", "setMyOnlineStatus"}
    routes = {(m, p if p.startswith("/internal/") else p.removeprefix("/api/v1"))
              for p, item in app.openapi()["paths"].items() for m in item}
    assert _ops("implemented") == routes


def _submit(client, upstream, auth, user, bar_card):
    return client.post("/api/v1/lawyers", headers=auth(user), json={
        "bar_card_number": bar_card, "specialization_codes": ["land"], "credentials": upstream.credentials(user)})


def test_response_khop_contract(client, upstream, auth):
    admin = auth(upstream.user(role="admin"), "admin")
    _validate(client.get("/api/v1/lawyers/specializations").json(), "SpecializationListResponse")

    citizen = upstream.user()
    created = _submit(client, upstream, auth, citizen, "1/TP")
    assert created.status_code == 201
    _validate(created.json(), "LawyerProfileResponse")
    _validate(client.get("/api/v1/lawyers/me", headers=auth(citizen)).json(), "LawyerProfileResponse")
    listed = client.get("/api/v1/lawyers/applications", headers=admin)
    assert listed.json()["data"]["items"]
    _validate(listed.json(), "LawyerProfileListResponse")
    approved = client.post(f"/api/v1/lawyers/{created.json()['data']['id']}/approve", headers=admin)
    assert approved.json()["data"]["approval_status"] == "approved"
    _validate(approved.json(), "LawyerProfileResponse")
    _validate(client.get("/api/v1/lawyers/applications?approval_status=approved&order=desc", headers=admin).json(),
              "LawyerProfileListResponse")

    _validate(client.get(f"/internal/v1/lawyers/{citizen}/status", headers=INTERNAL).json(), "LawyerStatusResponse")
    _validate(client.post(f"/internal/v1/lawyers/{citizen}/hide", headers=INTERNAL,
                          json={"reason": "deleted"}).json(), "HideProfileResponse")


def test_tu_choi_khop_contract(client, upstream, auth):
    admin = auth(upstream.user(role="admin"), "admin")
    citizen = upstream.user()
    app_id = _submit(client, upstream, auth, citizen, "2/TP").json()["data"]["id"]
    rejected = client.post(f"/api/v1/lawyers/{app_id}/reject", headers=admin, json={"reason": "Ảnh mờ"})
    assert rejected.json()["data"]["approval_status"] == "rejected"
    _validate(rejected.json(), "LawyerProfileResponse")
    _validate(client.get("/api/v1/lawyers/me", headers=auth(citizen)).json(), "LawyerProfileResponse")


def test_loi_khop_contract(client, upstream, auth):
    _validate(client.get("/api/v1/lawyers/me").json(), "ErrorResponse")
    _validate(client.get("/api/v1/lawyers/me", headers=auth(upstream.user())).json(), "ErrorResponse")
    _validate(client.post("/api/v1/lawyers", headers=auth(upstream.user()), json={}).json(), "ErrorResponse")
