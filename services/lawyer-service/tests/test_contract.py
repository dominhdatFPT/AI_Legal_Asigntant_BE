"""Response thật khớp schema trong openapi.yaml (contract first). Bỏ qua nếu chưa cài openapi-schema-validator."""

from pathlib import Path

import pytest
import yaml

from app.models import LawyerProfile, LawyerProfileSpecialization

SPEC = yaml.safe_load((Path(__file__).resolve().parents[1] / "openapi.yaml").read_text(encoding="utf-8"))
INTERNAL = {"X-Internal-Token": "test-internal-token"}


def _validate(body, schema_name):
    validator = pytest.importorskip("openapi_schema_validator")
    from jsonschema import RefResolver
    validator.OAS30Validator(SPEC["components"]["schemas"][schema_name],
                             resolver=RefResolver.from_schema(SPEC)).validate(body)


def test_operation_da_code_deu_implemented():
    planned = {op["operationId"] for item in SPEC["paths"].values() for m, op in item.items()
               if m in {"get", "post", "put", "patch", "delete"} and op["x-status"] == "planned"}
    assert planned == {"listLawyers", "getLawyer", "getMyLawyerStats"}


def test_response_khop_contract(client, db, upstream, auth):
    citizen = upstream.user()
    admin = auth(upstream.user(role="admin"), "admin")
    card = upstream.card(citizen)
    _validate(client.get("/api/v1/lawyers/specializations").json(), "SpecializationListResponse")

    created = client.post("/api/v1/lawyers/me/applications", headers=auth(citizen), json={
        "type": "new", "bar_card_number": "1/TP", "specialization_codes": ["land"], "card_document_id": str(card)})
    _validate(created.json(), "LawyerApplicationResponse")
    app_id = created.json()["data"]["id"]
    _validate(client.post(f"/api/v1/lawyers/me/applications/{app_id}/submit", headers=auth(citizen)).json(),
              "LawyerApplicationResponse")
    _validate(client.get("/api/v1/lawyers/me/applications", headers=auth(citizen)).json(),
              "LawyerApplicationListResponse")
    _validate(client.get("/api/v1/lawyers/applications", headers=admin).json(), "LawyerApplicationListResponse")
    _validate(client.get("/api/v1/lawyers/applications/summary", headers=admin).json(), "ApplicationSummaryResponse")
    _validate(client.get(f"/api/v1/lawyers/applications/{app_id}", headers=admin).json(), "ApplicationReviewResponse")
    _validate(client.post(f"/api/v1/lawyers/applications/{app_id}/approve", headers=admin).json(),
              "LawyerApplicationResponse")
    _validate(client.get("/api/v1/lawyers/me", headers=auth(citizen, "lawyer")).json(), "LawyerProfileResponse")
    _validate(client.get(f"/internal/v1/lawyers/{citizen}/status", headers=INTERNAL).json(), "LawyerStatusResponse")
    _validate(client.post(f"/internal/v1/lawyers/{citizen}/hide", headers=INTERNAL,
                          json={"reason": "deleted"}).json(), "HideProfileResponse")

    account = client.post("/api/v1/lawyers/accounts", headers=admin,
                          json={"email": "a@b.vn", "password": "matkhau123", "full_name": "A"})
    _validate(account.json(), "LawyerAccountResponse")
    _validate(client.get("/api/v1/lawyers/me").json(), "ErrorResponse")


def test_ho_so_chua_hoan_thien_khop_contract(client, db, upstream, auth):
    uid = upstream.user(cccd=False, role="lawyer")
    profile = LawyerProfile(user_id=uid, source="admin_created", status="incomplete")
    profile.specializations = [LawyerProfileSpecialization(specialization_code="business")]
    db.add(profile)
    db.commit()
    _validate(client.get("/api/v1/lawyers/me", headers=auth(uid, "lawyer")).json(), "LawyerProfileResponse")
