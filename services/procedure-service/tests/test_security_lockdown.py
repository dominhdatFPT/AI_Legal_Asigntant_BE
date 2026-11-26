from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import uuid4

import jwt
import pytest
import requests
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.api import office_editor, procedures
from app.core.exceptions import ApplicationLockedError, ApplicationNotFoundError
from app.main import app
from app.repositories import procedure_record_repository as records
from app.repositories import procedure_repository as catalog
from app.repositories.procedure_repository import ProcedureDbUnavailable
from app.services.procedure_application_service import ProcedureApplicationService
from form_samples import valid_form

BASE = "/api/v1/procedures"
USER_A = "11111111-1111-4111-8111-111111111111"
USER_B = "22222222-2222-4222-8222-222222222222"
ADMIN = "99999999-9999-4999-8999-999999999999"
RECORD_ID = "4bd458c4-b3cf-4294-a228-ff7f2ddc8b4e"
OFFICE_TEST_SECRET = "office-test-key-" + "a" * 32
INTERNAL_DETAIL = "private-diagnostic-sentinel"

client = TestClient(app, raise_server_exceptions=False)


def _insert_record(engine, table, code, owner, status="draft"):
    with engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO public.{table} "
                "(id, user_id, procedure_code, status, form_data) "
                "VALUES (:id, :owner, :code, :status, :form)"
            ),
            {"id": RECORD_ID, "owner": owner, "code": code, "status": status, "form": "{}"},
        )


@pytest.mark.parametrize(
    "domain,table,code",
    [
        ("land", "land_procedure_records", "DD-001"),
        ("business", "business_procedure_records", "HKD-001"),
    ],
)
def test_non_admin_cannot_access_unowned_record(db_engine, auth, domain, table, code):
    _insert_record(db_engine, table, code, None)
    path = f"{BASE}/applications/{domain}/{RECORD_ID}"
    headers = auth(USER_A)

    for response in (
        client.get(path, headers=headers),
        client.get(f"{BASE}/applications/lookup/{RECORD_ID}", headers=headers),
        client.put(path, headers=headers, json={"form_data": {"applicant_name": "Updated"}}),
        client.delete(path, headers=headers),
        client.post(f"{path}/submit", headers=headers),
    ):
        assert response.status_code in (403, 404), response.text
        assert response.json()["data"] is None

    admin = client.get(path, headers=auth(ADMIN, "admin"))
    assert admin.status_code == 200
    assert admin.json()["data"]["user_id"] is None
    assert admin.json()["data"]["status"] == "draft"
    assert admin.json()["data"]["form_data"] == {}


@pytest.mark.parametrize("operation", ["update", "delete", "submit"])
@pytest.mark.parametrize(
    "actual_owner,actual_status,expected_error",
    [
        (USER_B, "draft", ApplicationNotFoundError),
        (USER_A, "submitted", ApplicationLockedError),
    ],
)
def test_sql_mutation_matches_only_owned_drafts(
    db_engine, monkeypatch, operation, actual_owner, actual_status, expected_error,
):
    _insert_record(db_engine, "land_procedure_records", "DD-001", actual_owner, actual_status)
    # Simulate a preflight result that no longer reflects the stored row.
    monkeypatch.setattr(records, "_read_owned_draft", lambda *args: {
        "user_id": USER_A, "status": "draft", "form_data": {}, "extra_data": {},
    })

    with pytest.raises(expected_error):
        if operation == "update":
            records.update_procedure_record(
                "land", RECORD_ID, USER_A, form_data={"applicant_name": "Updated"},
            )
        elif operation == "delete":
            records.delete_procedure_record("land", RECORD_ID, USER_A)
        else:
            records.submit_procedure_record("land", RECORD_ID, USER_A, validate=lambda row: None)

    stored = records.get_procedure_record_by_id("land", RECORD_ID, owner_id=actual_owner)
    assert stored["user_id"] == actual_owner
    assert stored["status"] == actual_status
    assert stored["form_data"] == {}


@pytest.fixture
def unavailable_database(monkeypatch):
    engine = Mock()
    for method in (engine.connect, engine.begin):
        method.side_effect = OperationalError(None, None, RuntimeError(INTERNAL_DETAIL))
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)

    no_http = Mock(side_effect=AssertionError("Unexpected outbound HTTP request"))
    no_session = Mock(side_effect=AssertionError("Unexpected PostgREST session"))
    monkeypatch.setattr(requests.sessions.Session, "request", no_http)
    monkeypatch.setattr(records, "_session", no_session, raising=False)
    yield no_http, no_session
    no_http.assert_not_called()
    no_session.assert_not_called()


@pytest.mark.parametrize(
    "method,path,body,role",
    [
        ("POST", "/applications", "new", "citizen"),
        ("GET", f"/applications/land/{RECORD_ID}", None, "citizen"),
        ("GET", f"/applications/lookup/{RECORD_ID}", None, "citizen"),
        ("PUT", f"/applications/land/{RECORD_ID}", {}, "citizen"),
        ("DELETE", f"/applications/land/{RECORD_ID}", None, "citizen"),
        ("POST", f"/applications/land/{RECORD_ID}/submit", None, "citizen"),
        ("GET", "/applications/me", None, "citizen"),
        ("GET", "/applications", None, "admin"),
        ("GET", "/applications/summary", None, "admin"),
    ],
)
def test_record_endpoints_fail_closed_during_db_outage(unavailable_database, auth, method, path, body, role):
    if body == "new":
        body = {"procedure_code": "DD-001", "form_data": valid_form("DD-001")}
    kwargs = {"json": body} if body is not None else {}
    response = client.request(method, BASE + path, headers=auth(USER_A, role), **kwargs)
    assert response.status_code == 503, response.text
    assert response.json()["data"] is None
    assert response.json()["error"]["code"] == "PROCEDURE_DB_UNAVAILABLE"
    assert INTERNAL_DETAIL not in response.text


def test_delete_repository_raises_on_db_outage(unavailable_database):
    with pytest.raises(ProcedureDbUnavailable):
        records.delete_procedure_record("land", RECORD_ID, USER_A)


@pytest.mark.parametrize(
    "method,path,target,name,body,error_code",
    [
        (
            "GET", "/DD-001/form-schema", procedures.procedure_form_service,
            "get_procedure_form_schema", None, "INTERNAL_SERVER_ERROR",
        ),
        (
            "PUT", f"/applications/land/{RECORD_ID}", ProcedureApplicationService,
            "update_application", {}, "APPLICATION_UPDATE_ERROR",
        ),
        (
            "DELETE", f"/applications/land/{RECORD_ID}", ProcedureApplicationService,
            "delete_application", None, "APPLICATION_DELETE_ERROR",
        ),
        (
            "POST", f"/applications/land/{RECORD_ID}/submit", ProcedureApplicationService,
            "submit_application_by_id", None, "APPLICATION_SUBMIT_ERROR",
        ),
    ],
)
def test_500_responses_hide_exception_details(monkeypatch, auth, method, path, target, name, body, error_code):
    monkeypatch.setattr(target, name, Mock(side_effect=RuntimeError(INTERNAL_DETAIL)))
    kwargs = {"json": body} if body is not None else {}
    response = client.request(method, BASE + path, headers=auth(USER_A), **kwargs)
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == error_code
    assert INTERNAL_DETAIL not in response.text


def test_catalog_health_hides_exception_details(unavailable_database):
    health = catalog.ProcedureRepository.check_database_health()
    assert health["connected"] is False
    assert INTERNAL_DETAIL not in health["message"]


@pytest.mark.parametrize("configured_secret", [None, "too-short"])
def test_office_requires_dedicated_secret(monkeypatch, configured_secret):
    monkeypatch.setenv("JWT_SECRET", "legacy-test-secret-" + "x" * 32)
    if configured_secret is None:
        monkeypatch.delenv("ONLYOFFICE_JWT_SECRET", raising=False)
    else:
        monkeypatch.setenv("ONLYOFFICE_JWT_SECRET", configured_secret)

    with pytest.raises(HTTPException) as exc:
        office_editor.secret()
    assert exc.value.status_code == 503
    assert exc.value.detail == "Cấu hình ONLYOFFICE chưa sẵn sàng."


@pytest.mark.parametrize("ticket_kind", ["wrong_signature", "wrong_purpose", "wrong_template"])
def test_office_rejects_invalid_ticket(monkeypatch, ticket_kind):
    monkeypatch.setenv("ONLYOFFICE_JWT_SECRET", OFFICE_TEST_SECRET)
    template_id = str(uuid4())
    claims = {
        "template_id": template_id,
        "sid": uuid4().hex,
        "purpose": "source",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }
    signing_secret = OFFICE_TEST_SECRET
    if ticket_kind == "wrong_signature":
        signing_secret = "another-test-key-" + "b" * 32
    elif ticket_kind == "wrong_purpose":
        claims["purpose"] = "callback"
    else:
        claims["template_id"] = str(uuid4())

    session_lookup = Mock(side_effect=AssertionError("Invalid ticket reached session lookup"))
    monkeypatch.setattr(office_editor, "session_for", session_lookup)
    ticket = jwt.encode(claims, signing_secret, algorithm="HS256")
    response = client.get(f"{BASE}/office/{template_id}/source", params={"ticket": ticket})
    assert response.status_code == 401, response.text
    session_lookup.assert_not_called()
