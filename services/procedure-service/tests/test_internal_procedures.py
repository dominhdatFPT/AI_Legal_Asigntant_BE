import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Lock
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from starlette.requests import Request

from app.api import internal_procedures
from app.main import app
from app.services import procedure_registration

BASE = "/internal/v1/procedures"
TOKEN = "test-registry-token"
HEADERS = {"X-Internal-Token": TOKEN, "X-Request-ID": "req-procedure-test"}
client = TestClient(app)


@pytest.fixture(autouse=True)
def registry_token(monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", TOKEN)


def _form(**changes):
    return {"procedure_id": str(uuid4()), "name": "Đăng ký thủ tục mới", "domain": "land", **changes}


@pytest.mark.parametrize(("expected", "given"), [(TOKEN, None), (TOKEN, "wrong"), ("", TOKEN)])
@pytest.mark.parametrize("register", [False, True])
def test_requires_a_configured_matching_token(monkeypatch, expected, given, register):
    monkeypatch.setenv("INTERNAL_TOKEN", expected)
    headers = {"X-Internal-Token": given} if given else {}
    response = (client.post(BASE, data=_form(), headers=headers) if register
                else client.get(BASE + "/next-code", params={"domain": "land"}, headers=headers))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize(("domain", "floor", "code", "number"), [
    ("land", 0, "DD-003", 3), ("land", 7, "DD-008", 8), ("business", 0, "HKD-003", 3),
])
def test_next_code_uses_seeded_codes_and_contract_floor(domain, floor, code, number):
    response = client.get(BASE + "/next-code", params={"domain": domain, "floor": floor}, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "data": {"code": code, "number": number}, "error": None, "request_id": HEADERS["X-Request-ID"],
    }
    assert response.headers["X-Request-ID"] == HEADERS["X-Request-ID"]


def test_next_code_uses_existing_validation_and_db_error_envelopes(monkeypatch):
    invalid = client.get(BASE + "/next-code", params={"domain": "land", "floor": -1}, headers=HEADERS)
    assert (invalid.status_code, invalid.json()["error"]["code"]) == (422, "VALIDATION_ERROR")
    monkeypatch.setattr(procedure_registration, "next_code", Mock(side_effect=procedure_registration.ProcedureDbUnavailable))
    unavailable = client.get(BASE + "/next-code", params={"domain": "land"}, headers=HEADERS)
    assert (unavailable.status_code, unavailable.json()["error"]["code"]) == (503, "PROCEDURE_DB_UNAVAILABLE")


def test_register_writes_both_rows_and_is_idempotent(db_engine, monkeypatch):
    invalidate = Mock()
    monkeypatch.setattr(internal_procedures, "invalidate_procedure_caches", invalidate)
    form = _form(floor=4)
    response = client.post(BASE, data=form, headers=HEADERS)
    assert response.status_code == 201, response.text
    body = response.json()["data"]
    assert body == {
        "procedure_id": form["procedure_id"], "code": "DD-005", "created": True,
        "classification": {"key": "DD-005", "code": "DD-005", "name": form["name"],
                           "domain": "land", "kind": "project_procedure", "procedure_id": form["procedure_id"]},
    }
    invalidate.assert_called_once_with()

    repeat = client.post(BASE, data={**form, "code": "DD-009", "name": "Một tên khác"}, headers=HEADERS)
    assert repeat.status_code == 200, repeat.text
    assert repeat.json()["data"] == {**body, "created": False}
    with db_engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT code, name, domain, description, status, processing_days, fee
            FROM public.procedures WHERE id = :id
        """), {"id": form["procedure_id"]}).all()
        assert rows == [("DD-005", form["name"], "land", form["name"], "active", 3, 0)]
        classification = conn.execute(text("""
            SELECT key, code, name, domain, kind, procedure_id FROM public.reference_classifications
        """)).mappings().one()
        assert dict(classification) == body["classification"]
        assert conn.execute(text("SELECT count(*) FROM public.procedures")).scalar_one() == 5


def test_manual_code_rejects_duplicate_and_normalizes_new_code():
    duplicate = client.post(BASE, data=_form(code="DD-001"), headers=HEADERS)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"] == {
        "code": "PROCEDURE_CODE_EXISTS", "message": "Mã thủ tục DD-001 đã tồn tại.",
    }
    created = client.post(BASE, data=_form(code="dd-9"), headers=HEADERS)
    assert created.status_code == 201, created.text
    assert created.json()["data"]["code"] == "DD-009"


def test_register_links_form_and_pdf(db_engine):
    template_id = str(uuid4())
    with db_engine.begin() as conn:
        conn.execute(text("INSERT INTO public.custom_form_templates (id) VALUES (:id)"), {"id": template_id})
    form = _form(form_template_id=template_id)
    pdf = b"%PDF-1.7 sample"
    response = client.post(BASE, data=form, headers=HEADERS, files={"reference_pdf": ("ref.pdf", pdf, "application/pdf")})
    assert response.status_code == 201, response.text
    with db_engine.connect() as conn:
        row = conn.execute(text("""
            SELECT procedure_id, reference_pdf, reference_pdf_filename, updated_at
            FROM public.custom_form_templates WHERE id = :id
        """), {"id": template_id}).one()
        assert tuple(row[:3]) == (form["procedure_id"], pdf, "ref.pdf")
        assert row.updated_at is not None


def test_register_rejects_oversized_pdf():
    response = client.post(BASE, data=_form(), headers=HEADERS,
                           files={"reference_pdf": ("large.pdf", b"x" * (10 * 1024 * 1024 + 1), "application/pdf")})
    assert (response.status_code, response.json()["error"]["code"]) == (422, "PDF_TOO_LARGE")


def test_integrity_error_from_auto_code_is_a_conflict(monkeypatch):
    monkeypatch.setattr(procedure_registration, "_max_number", lambda conn, domain: 0)
    response = client.post(BASE, data=_form(), headers=HEADERS)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "PROCEDURE_CODE_CONFLICT")


def test_large_floor_is_supported_by_next_code_and_register():
    floor = 123456
    preview = client.get(BASE + "/next-code", params={"domain": "land", "floor": floor}, headers=HEADERS)
    assert preview.json()["data"] == {"code": "DD-123457", "number": 123457}
    registered = client.post(BASE, data=_form(floor=floor), headers=HEADERS)
    assert registered.status_code == 201 and registered.json()["data"]["code"] == "DD-123457"


def test_same_id_with_different_domain_wins_over_code_conflict():
    form = _form()
    first = client.post(BASE, data=form, headers=HEADERS)
    assert first.status_code == 201
    second = client.post(BASE, data={**form, "domain": "business", "code": "HKD-001"}, headers=HEADERS)
    assert second.status_code == 200
    assert second.json()["data"] == {**first.json()["data"], "created": False}


def test_concurrent_domains_take_the_same_uuid_lock_before_domain_lock(monkeypatch):
    start = Barrier(2)
    id_lock = Lock()
    rows, lock_calls = {}, []

    @contextmanager
    def connection(write=False):
        held = False
        domain_locked = False

        def execute(statement, params):
            nonlocal held, domain_locked
            sql = str(statement)
            if "pg_advisory_xact_lock" in sql:
                lock_calls.append((params["ns"], params["key"]))
                if params["ns"] == procedure_registration.PROC_ID_LOCK_NS:
                    start.wait(timeout=3)
                    held = id_lock.acquire(timeout=3)
                    assert held and not domain_locked
                else:
                    assert held
                    domain_locked = True
                return None
            assert held
            if "SELECT code, name, domain" in sql:
                return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: rows.get(params["id"])))
            assert domain_locked and "INSERT INTO public.procedures" in sql
            rows[params["id"]] = {key: params[key] for key in ("code", "name", "domain")}

        try:
            yield SimpleNamespace(dialect=SimpleNamespace(name="postgresql"), execute=execute)
        finally:
            if held:
                id_lock.release()

    monkeypatch.setattr(procedure_registration, "db_connection", connection)
    monkeypatch.setattr(procedure_registration, "_allocate_code", lambda conn, domain, floor: (domain + "-003", 3))
    monkeypatch.setattr(procedure_registration, "_upsert_classification", Mock())
    procedure_id = str(uuid4())

    def register(domain):
        return procedure_registration.register(
            procedure_id=procedure_id, name="New procedure", domain=domain, code=None, floor=0,
            form_template_id=None, pdf_data=None, pdf_filename=None,
        )

    with ThreadPoolExecutor(2) as pool:
        first, second = pool.map(register, ("land", "business"))
    assert {first["created"], second["created"]} == {True, False}
    assert first["classification"] == second["classification"]
    uuid_locks = [item for item in lock_calls if item[0] == procedure_registration.PROC_ID_LOCK_NS]
    assert len(lock_calls) == 3 and len(uuid_locks) == 2 and uuid_locks[0] == uuid_locks[1]


@pytest.mark.parametrize("state", ["missing", "deleted", "archived", "already_linked"])
def test_unavailable_template_leaves_no_procedure_or_classification(db_engine, state):
    template_id = str(uuid4())
    old_owner = str(uuid4())
    if state != "missing":
        with db_engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO public.custom_form_templates (id, deleted_at, status, procedure_id)
                VALUES (:id, :deleted, :status, :owner)
            """), {"id": template_id, "deleted": "2026-01-01" if state == "deleted" else None,
                   "status": "archived" if state == "archived" else "draft",
                   "owner": old_owner if state == "already_linked" else None})
    form = _form(form_template_id=template_id)
    response = client.post(BASE, data=form, headers=HEADERS)
    assert (response.status_code, response.json()["error"]["code"]) == (422, "FORM_TEMPLATE_UNAVAILABLE")
    with db_engine.connect() as conn:
        for table, field in [("procedures", "id"), ("reference_classifications", "procedure_id")]:
            assert conn.execute(
                text(f"SELECT 1 FROM public.{table} WHERE {field} = :id"), {"id": form["procedure_id"]},
            ).first() is None
        if state == "already_linked":
            assert conn.execute(text("SELECT procedure_id FROM public.custom_form_templates WHERE id = :id"),
                                {"id": template_id}).scalar_one() == old_owner


def test_non_code_integrity_error_rolls_back_template_link(db_engine):
    template_id = str(uuid4())
    with db_engine.begin() as conn:
        conn.execute(text("INSERT INTO public.custom_form_templates (id) VALUES (:id)"), {"id": template_id})
        conn.execute(text("""
            CREATE TRIGGER public.reject_procedure BEFORE INSERT ON procedures
            BEGIN SELECT RAISE(ABORT, 'procedure rejected by constraint'); END
        """))
    form = _form(form_template_id=template_id)
    response = client.post(BASE, data=form, headers=HEADERS)
    assert (response.status_code, response.json()["error"]["code"]) == (503, "PROCEDURE_DB_UNAVAILABLE")
    with db_engine.connect() as conn:
        assert conn.execute(text("SELECT procedure_id FROM public.custom_form_templates WHERE id = :id"),
                            {"id": template_id}).scalar_one() is None


@pytest.mark.parametrize("path", ["", "/register"])
@pytest.mark.parametrize("token", [None, b"non-ascii-\xe9"])
def test_invalid_token_is_rejected_before_parsing_multipart(monkeypatch, path, token):
    parse_form = Mock(side_effect=AssertionError("unauthenticated multipart was parsed"))
    monkeypatch.setattr(Request, "_get_form", parse_form)
    headers = [(b"content-type", b"multipart/form-data; boundary=invalid")]
    if token:
        headers.append((b"x-internal-token", token))
    response = client.post(BASE + path, content=b"malformed multipart", headers=headers)
    assert response.status_code == 401
    parse_form.assert_not_called()


def test_internal_request_id_is_sanitized_in_context_and_envelope(monkeypatch):
    from common_auth.context import request_id_var

    def next_code(domain, floor):
        assert request_id_var.get() == "req-safe" + "a" * 120
        return "DD-003", 3

    monkeypatch.setattr(procedure_registration, "next_code", next_code)
    headers = [(b"x-internal-token", TOKEN.encode()), (b"x-request-id", b"req-\r\n\x01safe" + b"a" * 200)]
    response = client.get(BASE + "/next-code", params={"domain": "land"}, headers=headers)
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == response.json()["request_id"] == "req-safe" + "a" * 120
    assert request_id_var.get() is None


def test_registration_rejects_oversized_declared_body_before_parsing(monkeypatch):
    parse_form = Mock(side_effect=AssertionError("oversized multipart was parsed"))
    monkeypatch.setattr(Request, "_get_form", parse_form)
    response = client.post(BASE, content=b"prefix", headers={
        **HEADERS, "Content-Type": "multipart/form-data; boundary=x",
        "Content-Length": str(internal_procedures._MAX_REGISTER_BODY_BYTES + 1),
    })
    assert (response.status_code, response.json()["error"]["code"]) == (422, "PDF_TOO_LARGE")
    parse_form.assert_not_called()


def test_oversized_chunked_body_is_rejected_with_no_content_length(monkeypatch):
    monkeypatch.setattr(internal_procedures, "_MAX_REGISTER_BODY_BYTES", 256)
    register = Mock()
    monkeypatch.setattr(procedure_registration, "register", register)
    chunks = iter([
        b'--b\r\nContent-Disposition: form-data; name="reference_pdf"; filename="test.pdf"\r\n'
        b'Content-Type: application/pdf\r\n\r\n%PDF',
        b'x' * 200,
        b'\r\n--b--\r\n',
    ])
    messages = []
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "scheme": "http",
        "http_version": "1.1", "method": "POST", "path": BASE, "root_path": "", "query_string": b"",
        "headers": [(b"content-type", b"multipart/form-data; boundary=b"), (b"x-internal-token", TOKEN.encode())],
    }

    async def receive():
        chunk = next(chunks, None)
        if chunk is None:
            return {"type": "http.disconnect"}
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    assert messages[0]["status"] == 422
    body = json.loads(b"".join(message["body"] for message in messages[1:]))
    assert body["error"]["code"] == "PDF_TOO_LARGE"
    register.assert_not_called()
