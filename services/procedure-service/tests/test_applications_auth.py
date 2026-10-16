from fastapi.testclient import TestClient
from form_samples import valid_form

from app.main import app

USER_A = "11111111-1111-4111-8111-111111111111"
USER_B = "22222222-2222-4222-8222-222222222222"
ADMIN = "99999999-9999-4999-8999-999999999999"

client = TestClient(app)

LAND = {"procedure_code": "DD-001", "status": "submitted",
        "form_data": valid_form("DD-001", applicant_name="Lê Văn C", applicant_id_number="079090123456", land_area=250.0)}


def submit(headers, payload=LAND):
    return client.post("/api/v1/procedures/applications", json=payload, headers=headers)


def test_submit_requires_token():
    res = submit({})
    assert res.status_code == 401
    assert res.json()["data"] is None


def test_submit_uses_token_owner_and_rejects_client_user_id(auth):
    res = submit(auth(USER_A), {**LAND, "user_id": USER_B})
    assert res.status_code == 422

    res = submit(auth(USER_A))
    assert res.status_code == 200
    assert res.json()["data"]["user_id"] == USER_A


def test_get_other_users_record_404(auth):
    record_id = submit(auth(USER_A)).json()["data"]["id"]
    res = client.get(f"/api/v1/procedures/applications/land/{record_id}", headers=auth(USER_B))
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "APPLICATION_NOT_FOUND"

    own = client.get(f"/api/v1/procedures/applications/land/{record_id}", headers=auth(USER_A))
    assert own.status_code == 200
    assert own.json()["data"]["applicant_name"] == "Lê Văn C"


def test_admin_can_get_any_record(auth):
    record_id = submit(auth(USER_A)).json()["data"]["id"]
    res = client.get(f"/api/v1/procedures/applications/land/{record_id}", headers=auth(ADMIN, "admin"))
    assert res.status_code == 200


def test_get_record_requires_token(auth):
    record_id = submit(auth(USER_A)).json()["data"]["id"]
    assert client.get(f"/api/v1/procedures/applications/land/{record_id}").status_code == 401


def test_submit_invalid_status_422(auth):
    res = submit(auth(USER_A), {**LAND, "status": "completed"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_submit_unknown_procedure_404(auth):
    res = submit(auth(USER_A), {**LAND, "procedure_code": "DD-999"})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "PROCEDURE_NOT_FOUND"


def test_bad_value_rejected_by_db_is_422_not_503(auth, monkeypatch):
    # Postgres báo DataError khi giá trị vượt VARCHAR/NUMERIC; đó là lỗi dữ liệu người dùng, không phải DB sập.
    from sqlalchemy.exc import DataError

    from app.repositories import procedure_record_repository

    def too_long(*_):
        raise DataError("INSERT ...", {}, Exception("value too long for type character varying(20)"))

    monkeypatch.setattr(procedure_record_repository, "get_procedure_id_by_code", too_long)
    res = submit(auth(USER_A))
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
