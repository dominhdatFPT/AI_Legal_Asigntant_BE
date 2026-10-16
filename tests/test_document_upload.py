"""Task 2: real disposable local PostgreSQL + fake private Storage, no Supabase writes."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import threading
from uuid import uuid4
import zipfile

import httpx
import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter

from test_document_foundation import foundation, config_env

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services/document-service"


def pdf(pages=1, encrypted=False):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=300, height=400)
    if encrypted:
        writer.encrypt("local-test-only")
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def image(format="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(stream, format=format)
    return stream.getvalue()


def docx(extra=None, body=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr("word/document.xml", body or '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>')
        if extra:
            archive.writestr(*extra)
    return stream.getvalue()


@pytest.fixture
def admission_tools(foundation):
    from app.services.file_validation import inspect_file, MAX_FILE, DOCX
    return inspect_file, MAX_FILE, DOCX


@pytest.mark.parametrize("filename,mime,content", [("a.pdf", "application/pdf", pdf()),
    ("a.png", "image/png", image()), ("a.jpg", "image/jpeg", image("JPEG")),
    ("a.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", docx())])
def test_supported_content(admission_tools, filename, mime, content):
    inspect, _, _ = admission_tools
    value = inspect(filename, mime, content, "supporting_document", "other")
    assert value.media_type == mime
    assert len(value.request_hash) == 64
    assert "content=" not in repr(value)


@pytest.mark.parametrize("filename,mime,content,purpose,kind,status,code", [
    ("a.pdf", "application/pdf", b"", "supporting_document", "other", 422, "INVALID_FILE"),
    ("../a.pdf", "application/pdf", pdf(), "supporting_document", "other", 422, "VALIDATION_ERROR"),
    ("a\x00.pdf", "application/pdf", pdf(), "supporting_document", "other", 422, "VALIDATION_ERROR"),
    ("a.pdf", "image/png", pdf(), "supporting_document", "other", 415, "UNSUPPORTED_FILE_TYPE"),
    ("a.exe", "application/pdf", pdf(), "supporting_document", "other", 415, "UNSUPPORTED_FILE_TYPE"),
    ("a.pdf", "application/pdf", b"%PDF-broken", "supporting_document", "other", 422, "INVALID_FILE"),
    ("a.pdf", "application/pdf", pdf(encrypted=True), "supporting_document", "other", 422, "ENCRYPTED_PDF"),
    ("a.pdf", "application/pdf", pdf(101), "supporting_document", "other", 422, "TOO_MANY_PAGES"),
    ("a.png", "image/png", image(), "contract_template", "contract", 415, "UNSUPPORTED_FILE_TYPE"),
    ("a.pdf", "application/pdf", pdf(), "contract_template", "other", 415, "UNSUPPORTED_FILE_TYPE"),
    ("a.docx", "application/octet-stream", docx(), "supporting_document", "identity_card", 415, "UNSUPPORTED_FILE_TYPE"),
    ("a.docx", "application/octet-stream", docx(("word/vbaProject.bin", b"macro")), "contract_template", "contract", 422, "UNSAFE_FILE"),
    ("a.docx", "application/octet-stream", docx(("../payload", b"unsafe")), "contract_template", "contract", 422, "UNSAFE_FILE"),
    ("a.docx", "application/octet-stream", docx(body='<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><x>&secret;</x>'), "contract_template", "contract", 422, "INVALID_FILE"),
])
def test_invalid_content(foundation, admission_tools, filename, mime, content, purpose, kind, status, code):
    with pytest.raises(foundation[3]) as exc:
        admission_tools[0](filename, mime, content, purpose, kind)
    assert exc.value.status == status
    assert exc.value.code == code


def test_size_and_fingerprint(admission_tools, foundation):
    inspect, limit, _ = admission_tools
    with pytest.raises(foundation[3]) as exc:
        inspect("a.pdf", "application/pdf", b"x" * (limit + 1), "supporting_document", "other")
    assert exc.value.status == 413
    data = pdf()
    a = inspect("a.pdf", "application/pdf", data, "supporting_document", "other")
    assert a.request_hash == inspect("a.pdf", "application/octet-stream", data, "supporting_document", "other").request_hash
    assert a.request_hash != inspect("b.pdf", "application/pdf", data, "supporting_document", "other").request_hash


class MemoryStorage:
    def __init__(self):
        self.objects = {}
        self.puts = 0
        self.lock = threading.Lock()

    def put_quarantine(self, key, content, media_type):
        with self.lock:
            assert key not in self.objects
            self.objects[key] = content
            self.puts += 1

    def remove_quarantine(self, key):
        self.objects.pop(key, None)


@pytest.fixture(scope="session")
def upload_database():
    url = os.getenv("DOCUMENT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Real SQL tests need a disposable localhost document_schema_test database")
    info = conninfo_to_dict(url)
    assert info.get("host") in {"127.0.0.1", "localhost"} and info.get("dbname") == "document_schema_test"
    with psycopg.connect(url, autocommit=True) as conn:
        for role in ["anon", "authenticated", "service_role"]:
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                conn.execute("CREATE ROLE " + role + (" BYPASSRLS" if role == "service_role" else ""))
        if not conn.execute("SELECT to_regclass('public.user_files')").fetchone()[0]:
            conn.execute((SERVICE / "database/001_document_schema.sql").read_text(encoding="utf-8"))
    return url


@pytest.fixture
def real_files(foundation, config_env, upload_database):
    from app.repositories.files import Files
    settings = replace(foundation[1].from_env(), database_url=upload_database)
    store = MemoryStorage()
    return Files(foundation[5](settings), store), store


def test_real_upload_list_detail_owner_and_replay(real_files, admission_tools, foundation):
    files, storage = real_files
    owner, key = str(uuid4()), uuid4()
    admitted = admission_tools[0]("a.pdf", "application/pdf", pdf(), "supporting_document", "other")
    created = files.upload(owner, key, admitted)
    assert created["file_status"] == "pending_check" and created["processing_status"] == "queued"
    assert created["sha256"] is None and created["size_bytes"] is None
    assert "storage_key" not in created and "request_hash" not in created
    assert files.upload(owner, key, admitted)["document_id"] == created["document_id"]
    assert storage.puts == 1
    assert files.list(owner, 1, 20)["pagination"]["total"] == 1
    assert files.list(owner, 10 ** 30, 20)["items"] == []
    assert files.list(owner, 1, 20, purpose="contract_template")["items"] == []
    stranger = str(uuid4())
    assert files.list(stranger, 1, 20)["items"] == []
    with pytest.raises(foundation[3]) as exc:
        files.detail(stranger, created["document_id"])
    assert exc.value.status == 404
    changed = admission_tools[0]("b.pdf", "application/pdf", admitted.content, "supporting_document", "other")
    with pytest.raises(foundation[3]) as exc:
        files.upload(owner, key, changed)
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"
    assert storage.puts == 1


def test_concurrent_uploads_have_one_job_and_object(real_files, admission_tools):
    files, storage = real_files
    owner, key = str(uuid4()), uuid4()
    admitted = admission_tools[0]("a.pdf", "application/pdf", pdf(), "supporting_document", "other")
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: files.upload(owner, key, admitted), range(4)))
    assert len({row["document_id"] for row in results}) == 1
    assert len({row["processing_job_id"] for row in results}) == 1
    assert storage.puts == 1


def test_storage_failure_rolls_back_metadata(real_files, admission_tools, foundation):
    files, storage = real_files
    def broken(key, content, media_type):
        storage.objects[key] = content  # Network error after object write is ambiguous.
        raise foundation[3]("STORAGE_TIMEOUT", "Test timeout", 504)
    storage.put_quarantine = broken
    owner = str(uuid4())
    admitted = admission_tools[0]("a.pdf", "application/pdf", pdf(), "supporting_document", "other")
    with pytest.raises(foundation[3]):
        files.upload(owner, uuid4(), admitted)
    assert files.list(owner, 1, 20)["pagination"]["total"] == 0
    assert storage.objects == {}


def test_sql_failure_after_storage_cleans_quarantine(real_files, admission_tools, foundation):
    files, storage = real_files
    admitted = admission_tools[0]("a.pdf", "application/pdf", pdf(), "supporting_document", "other")
    owner = str(uuid4())
    # Force a SQL CHECK failure after Storage succeeds; production validator rejects this first.
    with pytest.raises(foundation[3]):
        files.upload(owner, uuid4(), replace(admitted, filename=""))
    assert storage.puts == 1 and storage.objects == {}
    assert files.list(owner, 1, 20)["pagination"]["total"] == 0


def test_ambiguous_commit_never_deletes_committed_object(real_files, admission_tools, foundation):
    from contextlib import contextmanager
    files, storage = real_files
    original = files.database.connection
    first = True
    @contextmanager
    def connection(*, read_only=False):
        nonlocal first
        with original(read_only=read_only) as conn:
            yield conn
        if not read_only and first:
            first = False
            raise foundation[3]("DATABASE_UNAVAILABLE", "Simulated connection lost after commit")
    files.database.connection = connection
    owner, key = str(uuid4()), uuid4()
    admitted = admission_tools[0]("a.pdf", "application/pdf", pdf(), "supporting_document", "other")
    with pytest.raises(foundation[3]):
        files.upload(owner, key, admitted)
    assert len(storage.objects) == 1
    assert files.list(owner, 1, 20)["pagination"]["total"] == 1
    assert files.upload(owner, key, admitted)["file_status"] == "pending_check"
    assert storage.puts == 1


def test_replay_deleted_file_returns_gone(real_files, admission_tools, foundation):
    files, storage = real_files
    owner, key = str(uuid4()), uuid4()
    admitted = admission_tools[0]("a.pdf", "application/pdf", pdf(), "supporting_document", "other")
    row = files.upload(owner, key, admitted)
    with files.database.connection() as conn:
        conn.execute("UPDATE public.document_processing_jobs SET status='failed',error_code='TEST_ONLY',completed_at=now() WHERE id=%s", (row["processing_job_id"],))
        conn.execute("UPDATE public.user_files SET deleted_at=now() WHERE id=%s", (row["document_id"],))
    assert files.list(owner, 1, 20)["items"] == []
    with pytest.raises(foundation[3]) as exc:
        files.upload(owner, key, admitted)
    assert exc.value.status == 410
    assert storage.puts == 1


@pytest.fixture
def api(foundation, real_files):
    from app.api.files import get_files
    app = foundation[0]
    app.dependency_overrides[get_files] = lambda: real_files[0]
    return TestClient(app), real_files[0]


def test_real_http_contract_and_admin_is_not_owner(api, service_auth):
    client, _ = api
    owner = str(uuid4())
    headers = {"Authorization": "Bearer " + service_auth(sub=owner), "Idempotency-Key": str(uuid4())}
    response = client.post("/api/v1/documents/", headers=headers,
                           data={"purpose": "supporting_document", "document_type": "other"}, files={"file": ("a.pdf", pdf(), "application/pdf")})
    assert response.status_code == 202, response.text
    body = response.json()
    from openapi_schema_validator import OAS30Validator
    from jsonschema import RefResolver
    import yaml
    spec = yaml.safe_load((SERVICE / "openapi.yaml").read_text(encoding="utf-8"))
    OAS30Validator(spec["components"]["schemas"]["DocumentResponse"], resolver=RefResolver.from_schema(spec)).validate(body)
    document_id = body["data"]["document_id"]
    assert client.get("/api/v1/documents/" + document_id, headers=headers).status_code == 200
    admin_headers = {"Authorization": "Bearer " + service_auth(sub=str(uuid4()), role="admin")}
    assert client.get("/api/v1/documents/" + document_id, headers=admin_headers).status_code == 404
    assert client.get("/api/v1/documents/", headers=admin_headers).json()["data"]["items"] == []


@pytest.mark.parametrize("case,status", [("no_key", 422), ("extra_owner", 400), ("duplicate_file", 400),
    ("wrong_mime", 415), ("oversize", 413), ("missing_field", 422), ("duplicate_field", 400)])
def test_http_rejects_bad_upload(api, service_auth, case, status):
    client, _ = api
    headers = {"Authorization": "Bearer " + service_auth(), "Idempotency-Key": str(uuid4())}
    data = {"purpose": "supporting_document", "document_type": "other"}
    content, mime = pdf(), "application/pdf"
    if case == "no_key": headers.pop("Idempotency-Key")
    if case == "extra_owner": data["owner_user_id"] = str(uuid4())
    if case == "wrong_mime": mime = "image/png"
    if case == "oversize": content = b"x" * (9437184 + 1)
    if case == "missing_field": data.pop("document_type")
    posted = [("file", ("a.pdf", content, mime))]
    if case == "duplicate_file": posted *= 2
    if case == "duplicate_field": posted.append(("purpose", (None, "supporting_document")))
    response = client.post("/api/v1/documents/", headers=headers, data=data, files=posted)
    assert response.status_code == status, response.text


@pytest.mark.parametrize("query", ["page=0", "page_size=101", "purpose=invalid", "document_type=invalid"])
def test_pagination_validation(api, service_auth, query):
    response = api[0].get("/api/v1/documents/?" + query, headers={"Authorization": "Bearer " + service_auth()})
    assert response.status_code == 422


def test_unauthenticated_upload_never_writes_storage(api, real_files):
    response = api[0].post("/api/v1/documents/", files={"file": ("a.pdf", pdf(), "application/pdf")})
    assert response.status_code == 401
    assert real_files[1].puts == 0


def test_chunked_multipart_limit_and_malformed_body(api, service_auth):
    client, _ = api
    headers = {"Authorization": "Bearer " + service_auth(), "Idempotency-Key": str(uuid4()),
               "Content-Type": "multipart/form-data; boundary=BOUNDARY"}
    def body():
        yield b'--BOUNDARY\r\nContent-Disposition: form-data; name="file"; filename="a.pdf"\r\nContent-Type: application/pdf\r\n\r\n'
        for _ in range(11):
            yield b"x" * (1024 * 1024)
    response = client.post("/api/v1/documents/", headers=headers, content=body())
    assert response.status_code == 413
    response = client.post("/api/v1/documents/", headers=headers, content=b"not-a-multipart-body")
    assert response.status_code in {400, 422}


def test_storage_object_requests_private_paths(foundation, config_env):
    seen = []
    def handler(request):
        seen.append(request)
        assert request.headers["apikey"] == "sb_secret_test_only"
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"ok": True})
    storage = foundation[6](foundation[1].from_env(), transport=httpx.MockTransport(handler))
    key = "quarantine/owner/id/source"
    storage.put_quarantine(key, b"file", "application/pdf")
    storage.remove_quarantine(key)
    assert seen[0].method == "POST" and seen[0].headers["x-upsert"] == "false"
    assert seen[1].method == "DELETE" and key.encode() in seen[1].content
