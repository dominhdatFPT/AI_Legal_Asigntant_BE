"""Response thật khớp schema trong openapi.yaml (contract first). Bỏ qua nếu chưa cài openapi-schema-validator."""

import uuid
from pathlib import Path

import pytest
import yaml

SPEC = yaml.safe_load((Path(__file__).resolve().parents[1] / "openapi.yaml").read_text(encoding="utf-8"))
METHODS = {"get", "post", "put", "patch", "delete"}
BASE = "/api/v1/consultations"


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
    assert planned == {"listPreviousLawyers", "cancelConsultation", "reportConsultation", "listReports", "resolveReport", "getMyConsultationStats",
                       "closeConsultationsByUserInternal"}
    routes = {(m, p if p.startswith("/internal/") else p.removeprefix("/api/v1"))
              for p, item in app.openapi()["paths"].items() for m in item}
    assert _ops("implemented") == routes
    # OpenAPI không mô tả WebSocket nên app.openapi() không có route này; kiểm tra riêng với x-websocket.
    assert SPEC["x-websocket"]["x-status"] == "implemented"
    from app.api import ws

    # Gateway đổi /ws/consultations/{channel} thành /ws/v1/consultations/{channel}.
    assert [r.path for r in ws.router.routes] == ["/ws/v1/consultations/{channel}"]
    assert SPEC["x-websocket"]["url"].endswith("/ws/consultations/{channel}")


def test_response_khop_contract(client, auth, api, upstream):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    mine, lawyer_h = auth(citizen), auth(lawyer, "lawyer")

    _validate(client.get(BASE + "/terms", headers=mine).json(), "TermsResponse")
    api.agree(citizen)
    api.agree(lawyer, "lawyer")
    accepted = client.post(BASE + "/terms/accept", headers=mine, json={"terms_version": "2026-11-01"})
    _validate(accepted.json(), "TermsResponse")

    created = api.request(citizen)
    assert created.status_code == 201
    _validate(created.json(), "ConsultationResponse")
    cid = created.json()["data"]["id"]
    _validate(client.get(BASE + "/lobby", headers=lawyer_h).json(), "ConsultationListResponse")
    _validate(client.get(f"{BASE}/{cid}", headers=lawyer_h).json(), "ConsultationResponse")

    taken = client.post(f"{BASE}/{cid}/accept", headers=lawyer_h)
    assert taken.json()["data"]["status"] == "in_progress"
    _validate(taken.json(), "ConsultationResponse")

    sent = api.send(citizen, cid, "Chào luật sư")
    assert sent.status_code == 201
    _validate(sent.json(), "MessageResponse")
    messages = client.get(f"{BASE}/{cid}/messages", headers=lawyer_h)
    assert messages.json()["data"]["items"]
    _validate(messages.json(), "MessageListResponse")
    listed = client.get(BASE, headers=lawyer_h)
    assert listed.json()["data"]["items"][0]["last_message"]
    _validate(listed.json(), "ConsultationListResponse")

    read = client.post(f"{BASE}/{cid}/read", headers=lawyer_h)
    assert read.status_code == 200
    _validate(read.json(), "ReadResponse")

    closed = client.post(f"{BASE}/{cid}/close", headers=mine)
    assert closed.json()["data"]["status"] == "closed"
    _validate(closed.json(), "ConsultationResponse")


def test_loi_khop_contract(client, auth, api, upstream):
    _validate(client.get(BASE).json(), "ErrorResponse")
    _validate(client.get(f"{BASE}/{uuid.uuid4()}", headers=auth(uuid.uuid4())).json(), "ErrorResponse")
    _validate(client.post(BASE, headers=auth(uuid.uuid4()), json={}).json(), "ErrorResponse")
    _validate(api.request(uuid.uuid4(), agreed=False).json(), "ErrorResponse")
    _validate(client.get(BASE + "/lobby", headers=auth(uuid.uuid4())).json(), "ErrorResponse")
