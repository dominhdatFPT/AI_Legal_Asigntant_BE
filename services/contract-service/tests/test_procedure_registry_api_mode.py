import asyncio
import json
import sys
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qsl
from uuid import UUID, uuid4

import httpx
import pytest
from starlette.datastructures import UploadFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import reference_catalog as rc  # noqa: E402
from app.core.errors import ContractError  # noqa: E402
from app.repositories import reference_uploads  # noqa: E402
from app.services import procedure_registry_client as registry  # noqa: E402

NAME = "Đăng ký thủ tục mới"


def _form(request):
    content_type = request.headers["content-type"]
    raw = request.read()
    if content_type.startswith("application/x-www-form-urlencoded"):
        return dict(parse_qsl(raw.decode())), {}
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\n\r\n".encode() + raw,
    )
    fields, files = {}, {}
    for part in message.iter_parts():
        key = part.get_param("name", header="content-disposition")
        value = part.get_payload(decode=True)
        if part.get_filename() is None:
            fields[key] = value.decode("utf-8")
        else:
            files[key] = (part.get_filename(), value, part.get_content_type())
    return fields, files


def _registered(form, code):
    classification = {
        "key": code, "code": code, "name": form["name"], "domain": form["domain"],
        "kind": "project_procedure", "procedure_id": form["procedure_id"],
    }
    return httpx.Response(201, json={"data": {
        "classification": classification, "procedure_id": form["procedure_id"], "code": code, "created": True,
    }})


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("PROCEDURE_SERVICE_URL", "https://procedure.test/")
    monkeypatch.setenv("INTERNAL_TOKEN", "registry-secret")
    monkeypatch.delenv("PROCEDURE_DATABASE_URL", raising=False)
    procedure_db = Mock(side_effect=AssertionError("Direct Procedure DB connection in API mode"))
    monkeypatch.setattr(rc, "pooled_connection", procedure_db)
    contract_db = Mock()
    contract_db.execute.return_value.fetchall.return_value = [("DD-004",)]

    @contextmanager
    def connection():
        yield contract_db

    monkeypatch.setattr(reference_uploads, "connection", connection)
    return procedure_db, contract_db


def _sql_calls(conn):
    return [call.args for call in conn.execute.call_args_list]


def test_auto_mode_sends_floor_header_and_multipart_and_upserts_returned_code(monkeypatch, api):
    procedure_db, contract_db = api
    template = str(uuid4())
    calls = []

    def handler(request):
        assert request.method == "POST" and request.url.path == "/internal/v1/procedures"
        assert request.headers["X-Internal-Token"] == "registry-secret"
        assert request.headers["X-Request-ID"] == "req-registry"
        form, files = _form(request)
        assert form == {"procedure_id": form["procedure_id"], "name": NAME, "domain": "land",
                        "floor": "4", "form_template_id": template}
        assert files == {"reference_pdf": ("reference.pdf", b"%PDF-data", "application/pdf")}
        calls.append(form["procedure_id"])
        return _registered(form, "DD-008")

    monkeypatch.setattr(registry, "transport", httpx.MockTransport(handler))
    classification, proc_id = rc.create_new_procedure_record(
        NAME, "land", None, template, b"%PDF-data", request_id="req-registry",
    )
    assert calls == [proc_id]
    assert classification["code"] == "DD-008" and classification["procedure_id"] == proc_id
    floor, upsert = _sql_calls(contract_db)
    assert "SELECT code FROM public.reference_classifications" in floor[0]
    assert floor[1] == ("land", "DD-%")
    assert "INSERT INTO public.reference_classifications" in upsert[0]
    assert upsert[1] == ("DD-008", "DD-008", NAME, "land", UUID(proc_id))
    procedure_db.assert_not_called()


def test_explicit_code_skips_floor_and_omits_pdf_without_template(monkeypatch, api):
    procedure_db, contract_db = api

    def handler(request):
        form, files = _form(request)
        assert form["floor"] == "0" and form["code"] == "DD-009"
        assert "form_template_id" not in form and files == {}
        return _registered(form, "DD-009")

    monkeypatch.setattr(registry, "transport", httpx.MockTransport(handler))
    rc.create_new_procedure_record(NAME, "land", "DD-009", pdf_data=b"%PDF-data")
    (statement, _), = _sql_calls(contract_db)
    assert "INSERT INTO public.reference_classifications" in statement
    procedure_db.assert_not_called()


@pytest.mark.parametrize("code", ["PROCEDURE_CODE_EXISTS", "PROCEDURE_CODE_CONFLICT"])
def test_conflict_is_forwarded_without_contract_write(monkeypatch, api, code):
    procedure_db, contract_db = api
    monkeypatch.setattr(registry, "transport", httpx.MockTransport(
        lambda req: httpx.Response(409, json={"error": {"code": code, "message": "Trùng mã"}}),
    ))
    with pytest.raises(ContractError) as exc:
        rc.create_new_procedure_record(NAME, "land", "DD-001")
    assert (exc.value.status, exc.value.code, exc.value.message) == (409, code, "Trùng mã")
    contract_db.execute.assert_not_called()
    procedure_db.assert_not_called()


@pytest.mark.parametrize("missing_endpoint", [False, True])
def test_old_endpoint_or_explicit_db_mode_uses_advisory_lock(monkeypatch, api, missing_endpoint):
    _, contract_db = api
    monkeypatch.setenv("PROCEDURE_DATABASE_URL", "postgresql://fake/proc")
    proc_conn = Mock()
    proc_conn.execute.return_value.fetchall.return_value = []

    @contextmanager
    def pooled(*args, **kwargs):
        yield proc_conn

    monkeypatch.setattr(rc, "pooled_connection", pooled)
    upstream = Mock(return_value=httpx.Response(404))
    monkeypatch.setattr(registry, "transport", httpx.MockTransport(upstream))
    if not missing_endpoint:
        monkeypatch.setenv("PROCEDURE_WRITE_MODE", "db")

    classification, _ = rc.create_new_procedure_record(NAME, "land", "DD-009")
    assert classification["code"] == "DD-009"
    lock, insert, _ = _sql_calls(proc_conn)
    assert "pg_advisory_xact_lock" in lock[0] and lock[1] == (rc._PROC_CODE_LOCK_NS, 1)
    assert "INSERT INTO public.procedures" in insert[0]
    assert len(_sql_calls(contract_db)) == 1
    assert upstream.call_count == int(missing_endpoint)


@pytest.mark.parametrize("failure", [404, 405, 503, 401, "connect", "malformed"])
def test_registry_failure_has_503_envelope(monkeypatch, api, failure):
    procedure_db, contract_db = api
    if failure not in (404, 405):
        monkeypatch.setenv("PROCEDURE_DATABASE_URL", "postgresql://fake/proc")

    def handler(request):
        if failure == "connect":
            raise httpx.ConnectError("unreachable", request=request)
        if failure == "malformed":
            return httpx.Response(201, json={"data": {"classification": {"code": "DD-009"}}})
        return httpx.Response(failure)

    monkeypatch.setattr(registry, "transport", httpx.MockTransport(handler))
    with pytest.raises(ContractError) as exc:
        rc.create_new_procedure_record(NAME, "land", "DD-009")
    assert (exc.value.status, exc.value.code) == (503, "PROCEDURE_SERVICE_UNAVAILABLE")
    contract_db.execute.assert_not_called()
    procedure_db.assert_not_called()


def test_register_retries_timeout_once_with_same_procedure_id(monkeypatch, api):
    calls = []

    def handler(request):
        form, _ = _form(request)
        calls.append(form)
        if len(calls) == 1:
            raise httpx.ReadTimeout("cold start", request=request)
        return _registered(form, "DD-009")

    monkeypatch.setattr(registry, "transport", httpx.MockTransport(handler))
    _, proc_id = rc.create_new_procedure_record(NAME, "land", "DD-009")
    assert len(calls) == 2
    assert calls[0] == calls[1] and calls[0]["procedure_id"] == proc_id


def test_next_code_uses_api(monkeypatch, api):
    procedure_db, contract_db = api

    def handler(request):
        assert request.method == "GET" and request.url.path == "/internal/v1/procedures/next-code"
        assert dict(request.url.params) == {"domain": "land", "floor": "4"}
        assert request.headers["X-Request-ID"] == "req-next"
        return httpx.Response(200, json={"data": {"code": "DD-005", "number": 5}})

    monkeypatch.setattr(registry, "transport", httpx.MockTransport(handler))
    assert rc.get_next_procedure_code("land", request_id="req-next") == "DD-005"
    contract_db.execute.assert_called_once()
    procedure_db.assert_not_called()


def test_db_mode_without_url_raises_contract_error(monkeypatch, api):
    monkeypatch.setenv("PROCEDURE_WRITE_MODE", "db")
    with pytest.raises(ContractError) as exc:
        rc.get_next_procedure_code("land")
    assert (exc.value.status, exc.value.code) == (503, "PROCEDURE_SERVICE_UNAVAILABLE")
    with pytest.raises(ContractError) as exc:
        rc.create_new_procedure_record(NAME, "land", "DD-009")
    assert (exc.value.status, exc.value.code) == (503, "PROCEDURE_SERVICE_UNAVAILABLE")


@pytest.mark.parametrize(("section", "field", "value"), [
    ("data", "procedure_id", str(uuid4())),
    ("data", "created", "true"),
    ("data", "created", 1),
    ("data", "code", "DD-999"),
    ("classification", "procedure_id", str(uuid4())),
    ("classification", "domain", "business"),
    ("classification", "kind", "official"),
    ("classification", "code", ""),
    ("classification", "key", " "),
    ("classification", "name", None),
])
def test_registration_rejects_mismatched_or_invalid_response(monkeypatch, api, section, field, value):
    _, contract_db = api

    def handler(request):
        form, _ = _form(request)
        body = json.loads(_registered(form, "DD-009").content)
        data = body["data"]
        target = data if section == "data" else data["classification"]
        target[field] = value
        return httpx.Response(201, json=body)

    monkeypatch.setattr(registry, "transport", httpx.MockTransport(handler))
    with pytest.raises(ContractError) as exc:
        rc.create_new_procedure_record(NAME, "land", "DD-009")
    assert (exc.value.status, exc.value.code) == (503, "PROCEDURE_SERVICE_UNAVAILABLE")
    contract_db.execute.assert_not_called()


@pytest.mark.parametrize(("code", "number"), [
    ("HKD-005", 5), ("DD-005", 6), ("DD-5", 5), ("DD-005", "5"), ("DD-004", 4), ("DD-001", True),
])
def test_next_code_requires_consistent_domain_format_number_and_floor(monkeypatch, api, code, number):
    monkeypatch.setattr(registry, "transport", httpx.MockTransport(
        lambda _: httpx.Response(200, json={"data": {"code": code, "number": number}}),
    ))
    with pytest.raises(ContractError) as exc:
        rc.get_next_procedure_code("land")
    assert (exc.value.status, exc.value.code) == (503, "PROCEDURE_SERVICE_UNAVAILABLE")


def test_next_code_forwards_large_floor_and_sanitizes_request_id(monkeypatch, api):
    _, contract_db = api
    contract_db.execute.return_value.fetchall.return_value = [("DD-100004",)]

    def handler(request):
        assert request.url.params["floor"] == "100004"
        assert request.headers["X-Request-ID"] == "req-safe"
        return httpx.Response(200, json={"data": {"code": "DD-100005", "number": 100005}})

    monkeypatch.setattr(registry, "transport", httpx.MockTransport(handler))
    assert rc.get_next_procedure_code("land", request_id="req-\r\n\x01safe") == "DD-100005"


def test_missing_template_error_does_not_fall_back_to_legacy_db(monkeypatch, api):
    procedure_db, contract_db = api
    monkeypatch.setattr(registry, "transport", httpx.MockTransport(lambda _: httpx.Response(
        422, json={"error": {"code": "FORM_TEMPLATE_UNAVAILABLE", "message": "Không thể gắn biểu mẫu."}},
    )))
    with pytest.raises(ContractError) as exc:
        rc.create_new_procedure_record(NAME, "land", "DD-009", form_template_id=str(uuid4()))
    assert (exc.value.status, exc.value.code) == (422, "FORM_TEMPLATE_UNAVAILABLE")
    procedure_db.assert_not_called()
    contract_db.execute.assert_not_called()


@pytest.mark.parametrize("fails_at", ["classification", "upload", "purged", None])
def test_reference_upload_writes_both_rows_in_one_transaction(monkeypatch, api, fails_at):
    procedure_db, _ = api
    monkeypatch.setenv("CONTRACT_DATABASE_URL", "postgresql://mock@localhost/contract")
    procedure_ids, committed, staged = [], [], []

    def handler(request):
        if request.method == "POST":
            form, _ = _form(request)
            procedure_ids.append(form["procedure_id"])
            return _registered(form, "DD-009")
        proc_id = procedure_ids[-1]
        assert request.url.path == "/internal/v1/procedures/" + proc_id
        if fails_at == "purged":
            return httpx.Response(404, json={"error": {"code": "PROCEDURE_NOT_FOUND", "message": "Gone"}})
        return httpx.Response(200, json={"data": {"procedure_id": proc_id}})

    conn = Mock()

    def execute(sql, params):
        if "INSERT INTO public.reference_classifications" in sql:
            if fails_at == "classification" and len(procedure_ids) == 1:
                raise ContractError("REFERENCE_UPLOAD_UNAVAILABLE", "Rejected")
            assert params[-1] == UUID(procedure_ids[-1])
            staged.append("classification")
        elif "INSERT INTO public.contract_reference_uploads" in sql:
            if fails_at == "upload" and len(procedure_ids) == 1:
                raise ContractError("REFERENCE_UPLOAD_UNAVAILABLE", "Rejected")
            assert params[4].obj["procedure_id"] == procedure_ids[-1]
            staged.append("upload")
        return Mock()

    conn.execute.side_effect = execute

    @contextmanager
    def connection():
        try:
            yield conn
        except Exception:
            staged.clear()
            raise
        else:
            committed.extend(staged)
            staged.clear()

    monkeypatch.setattr(reference_uploads, "connection", connection)
    monkeypatch.setattr(registry, "transport", httpx.MockTransport(handler))
    monkeypatch.setattr(rc, "snapshot", lambda: {"classifications": []})
    monkeypatch.setattr(rc, "prepare_reference_pdf", lambda *args: ({
        "id": str(uuid4()), "source_sha256": "digest", "domain": "land",
    }, []))
    owner = SimpleNamespace(id=str(uuid4()))
    request = SimpleNamespace(headers={"Idempotency-Key": "stable"}, state=SimpleNamespace(request_id="req-upload"))

    def upload():
        return asyncio.run(rc.upload_reference(
            request, file=UploadFile(BytesIO(b"%PDF"), filename="test.pdf"), name=NAME,
            domain="land", procedure=None, code="DD-009", form_template_id=None, user=owner,
        ))

    if fails_at:
        with pytest.raises(ContractError):
            upload()
        assert committed == []
        if fails_at == "purged":
            assert not any("INSERT" in sql for sql, _ in _sql_calls(conn))
            return
    response = upload()
    assert response["data"]["procedure_id"] == procedure_ids[-1]
    assert len(set(procedure_ids)) == 1
    assert committed == ["classification", "upload"]
    procedure_db.assert_not_called()
