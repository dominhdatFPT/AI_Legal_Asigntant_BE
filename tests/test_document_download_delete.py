"""Task 3: disposable local PostgreSQL, private Storage mock, race/error/security cases."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import threading
from urllib.parse import urlsplit, parse_qs
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from test_document_foundation import foundation, config_env
from test_document_upload import upload_database, admission_tools, MemoryStorage, pdf, image


class DownloadStore(MemoryStorage):
    def __init__(self):
        super().__init__()
        self.reads, self.signs = 0, 0

    def read_verified(self, key, size, digest, media):
        self.reads += 1
        content = self.objects[key]
        assert len(content) == size and hashlib.sha256(content).hexdigest() == digest
        return content

    def signed_download(self, key, filename, *, ttl=300):
        self.signs += 1
        assert ttl == 300
        return "https://example.invalid/storage/v1/object/sign/document-files/" + key + "?token=test-only"


@pytest.fixture
def files(foundation, config_env, upload_database):
    from app.repositories.files import Files
    settings = replace(foundation[1].from_env(), database_url=upload_database)
    storage = DownloadStore()
    return Files(foundation[5](settings), storage), storage


@pytest.fixture
def create_file(files, admission_tools):
    repository, _ = files
    def create(status="failed", *, identity=False, filename=None):
        owner = str(uuid4())
        content = image() if identity else pdf()
        admitted = admission_tools[0](filename or ("a.png" if identity else "a.pdf"),
                                     "image/png" if identity else "application/pdf", content,
                                     "supporting_document", "identity_card" if identity else "other")
        row = repository.upload(owner, uuid4(), admitted)
        if status in {"failed", "completed"}:
            with repository.database.connection() as conn:
                conn.execute("UPDATE public.user_files SET file_status='available',size_bytes=%s,sha256=%s WHERE id=%s",
                             (len(content), hashlib.sha256(content).hexdigest(), row["document_id"]))
                if status == "completed":
                    conn.execute("INSERT INTO public.document_extraction_results (job_id,source_sha256,source_kind,engine_version) VALUES (%s,%s,%s,%s)",
                                 (row["processing_job_id"], hashlib.sha256(content).hexdigest(), "image" if identity else "pdf_text", "test-only"))
                    conn.execute("UPDATE public.document_processing_jobs SET status='completed',completed_at=now() WHERE id=%s", (row["processing_job_id"],))
                else:
                    conn.execute("UPDATE public.document_processing_jobs SET status='failed',error_code='OCR_FAILED',completed_at=now() WHERE id=%s", (row["processing_job_id"],))
        elif status == "processing":
            with repository.database.connection() as conn:
                conn.execute("UPDATE public.document_processing_jobs SET status='processing',started_at=now() WHERE id=%s", (row["processing_job_id"],))
        elif status == "rejected":
            with repository.database.connection() as conn:
                conn.execute("UPDATE public.user_files SET file_status='rejected',error_code='UNSAFE_FILE' WHERE id=%s", (row["document_id"],))
                conn.execute("UPDATE public.document_processing_jobs SET status='failed',error_code='UNSAFE_FILE',completed_at=now() WHERE id=%s", (row["processing_job_id"],))
        return owner, row["document_id"], content
    return create


def test_available_ocr_failed_can_read_download_delete(files, create_file, foundation):
    repository, storage = files
    owner, document_id, content = create_file()
    assert repository.content(owner, document_id)[0] == content
    result = repository.download(owner, document_id)
    ttl = (datetime.fromisoformat(result["expires_at"]) - datetime.now(timezone.utc)).total_seconds()
    assert 290 < ttl <= 300
    assert result["size_bytes"] == len(content)
    assert result["sha256"] == hashlib.sha256(content).hexdigest()
    repository.delete(owner, document_id)
    repository.delete(owner, document_id)  # Idempotent, don't change deleted_at again.
    assert len(storage.objects) == 1  # Soft-delete, not destructive Storage purge.
    assert repository.list(owner, 1, 20)["items"] == []
    for method in [repository.content, repository.download, repository.detail]:
        with pytest.raises(foundation[3]) as exc:
            method(owner, document_id)
        assert exc.value.status == 404
    assert storage.reads == 1 and storage.signs == 1


@pytest.mark.parametrize("method", ["content", "download", "delete"])
def test_owner_and_nonexistent_are_indistinguishable(files, create_file, foundation, method):
    repository, storage = files
    _, document_id, _ = create_file()
    for target in [document_id, uuid4()]:
        with pytest.raises(foundation[3]) as exc:
            getattr(repository, method)(str(uuid4()), target)
        assert exc.value.code == "DOCUMENT_NOT_FOUND"
    assert storage.reads == storage.signs == 0


@pytest.mark.parametrize("status,code", [("queued", "FILE_NOT_READY"), ("processing", "FILE_NOT_READY"), ("rejected", "FILE_REJECTED")])
@pytest.mark.parametrize("method", ["content", "download"])
def test_unsafe_or_pending_is_not_readable(files, create_file, foundation, status, code, method):
    repository, storage = files
    owner, document_id, _ = create_file(status)
    with pytest.raises(foundation[3]) as exc:
        getattr(repository, method)(owner, document_id)
    assert exc.value.status == 409 and exc.value.code == code
    assert storage.reads == storage.signs == 0


@pytest.mark.parametrize("status", ["queued", "processing"])
def test_active_job_prevents_delete(files, create_file, foundation, status):
    repository, _ = files
    owner, document_id, _ = create_file(status)
    with pytest.raises(foundation[3]) as exc:
        repository.delete(owner, document_id)
    assert exc.value.code == "DOCUMENT_BUSY"
    assert repository.detail(owner, document_id)["document_id"] == document_id


def test_active_profile_prevents_delete_until_unlinked(files, create_file, foundation):
    repository, _ = files
    owner, document_id, _ = create_file("completed", identity=True)
    with repository.database.connection() as conn:
        result = conn.execute("SELECT r.id FROM public.document_extraction_results r JOIN public.document_processing_jobs j ON j.id=r.job_id WHERE j.document_id=%s", (document_id,)).fetchone()
        conn.execute("INSERT INTO public.user_identity_profiles (owner_user_id,source_document_id,source_result_id,full_name,confirmed_at) VALUES (%s,%s,%s,'TEST ONLY',now())", (owner, document_id, result["id"]))
    with pytest.raises(foundation[3]) as exc:
        repository.delete(owner, document_id)
    assert exc.value.code == "PROFILE_SOURCE_IN_USE"
    with repository.database.connection() as conn:
        conn.execute("UPDATE public.user_identity_profiles SET status='deleted',deleted_at=now(),revision=revision+1 WHERE owner_user_id=%s", (owner,))
    repository.delete(owner, document_id)


def test_cross_owner_storage_reference_is_rejected(files, admission_tools, foundation):
    repository, storage = files
    owner = str(uuid4())
    value = admission_tools[0]("a.pdf", "application/pdf", pdf(), "supporting_document", "other")
    row = repository.upload(owner, uuid4(), value)
    with repository.database.connection() as conn:
        conn.execute("UPDATE public.user_files SET storage_key=%s,file_status='available',size_bytes=%s,sha256=%s WHERE id=%s", (f"safe/{uuid4()}/{uuid4()}/content", len(value.content), value.uploaded_sha256, row["document_id"]))
    with pytest.raises(foundation[3]) as exc:
        repository.download(owner, row["document_id"])
    assert exc.value.code == "STORAGE_CONFIGURATION_ERROR"
    assert storage.signs == 0


def test_download_and_delete_are_serialized(files, create_file):
    repository, storage = files
    owner, document_id, _ = create_file()
    entered, release, deletion_started = threading.Event(), threading.Event(), threading.Event()
    original = storage.signed_download
    def sign(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    storage.signed_download = sign
    def delete():
        deletion_started.set()
        repository.delete(owner, document_id)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(repository.download, owner, document_id)
        assert entered.wait(5)
        second = executor.submit(delete)
        assert deletion_started.wait(5)
        assert not second.done()
        release.set()
        assert first.result()["document_id"] == document_id
        second.result()


def test_concurrent_delete_is_idempotent(files, create_file):
    repository, storage = files
    owner, document_id, _ = create_file()
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: repository.delete(owner, document_id), range(4)))
    with repository.database.connection(read_only=True) as conn:
        before = conn.execute("SELECT deleted_at,updated_at FROM public.user_files WHERE id=%s", (document_id,)).fetchone()
    repository.delete(owner, document_id)
    with repository.database.connection(read_only=True) as conn:
        after = conn.execute("SELECT deleted_at,updated_at FROM public.user_files WHERE id=%s", (document_id,)).fetchone()
    assert before == after and after["deleted_at"] is not None
    assert len(storage.objects) == 1


def test_rejected_terminal_file_can_be_soft_deleted(files, create_file):
    repository, _ = files
    owner, document_id, _ = create_file("rejected")
    repository.delete(owner, document_id)
    assert repository.list(owner, 1, 20)["items"] == []


@pytest.fixture
def api(foundation, files):
    from app.api.files import get_files
    foundation[0].dependency_overrides[get_files] = lambda: files[0]
    return TestClient(foundation[0], raise_server_exceptions=False)


def test_binary_headers_and_204_body(api, create_file, service_auth):
    owner, document_id, content = create_file(filename='Hợp đồng "mẫu".pdf')
    headers = {"Authorization": "Bearer " + service_auth(sub=owner), "X-Request-ID": "task3-http"}
    response = api.get(f"/api/v1/documents/{document_id}/content", headers=headers)
    assert response.status_code == 200 and response.content == content
    assert response.headers["Content-Type"] == "application/pdf"
    assert int(response.headers["Content-Length"]) == len(content)
    assert response.headers["X-Content-SHA256"] == hashlib.sha256(content).hexdigest()
    assert response.headers["Content-Disposition"].startswith("attachment;")
    assert "filename*=UTF-8''" in response.headers["Content-Disposition"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Request-ID"] == "task3-http"
    download = api.get(f"/api/v1/documents/{document_id}/download", headers=headers)
    assert download.status_code == 200
    from pathlib import Path
    import yaml
    from openapi_schema_validator import OAS30Validator
    from jsonschema import RefResolver
    spec = yaml.safe_load((Path(__file__).resolve().parents[1] / "services/document-service/openapi.yaml").read_text(encoding="utf-8"))
    OAS30Validator(spec["components"]["schemas"]["DownloadResponse"], resolver=RefResolver.from_schema(spec)).validate(download.json())
    response = api.delete(f"/api/v1/documents/{document_id}", headers=headers)
    assert response.status_code == 204 and response.content == b""
    assert api.delete(f"/api/v1/documents/{document_id}", headers=headers).status_code == 204
    assert api.get(f"/api/v1/documents/{document_id}/download", headers=headers).status_code == 404


@pytest.mark.parametrize("method,suffix", [("get", "/content"), ("get", "/download"), ("delete", "")])
def test_admin_has_no_owner_bypass(api, create_file, service_auth, method, suffix):
    _, document_id, _ = create_file()
    headers = {"Authorization": "Bearer " + service_auth(sub=str(uuid4()), role="admin")}
    assert getattr(api, method)(f"/api/v1/documents/{document_id}" + suffix, headers=headers).status_code == 404


def test_cookie_delete_requires_trusted_origin(api, create_file, service_auth):
    owner, document_id, _ = create_file()
    api.cookies.set("access_token", service_auth(sub=owner))
    path = f"/api/v1/documents/{document_id}"
    assert api.delete(path).status_code == 403
    assert api.delete(path, headers={"Origin": "https://evil.example"}).status_code == 403
    assert api.delete(path, headers={"Origin": "http://localhost:3000"}).status_code == 204


def test_upstream_failure_returns_json_before_binary(api, files, create_file, service_auth, foundation):
    owner, document_id, _ = create_file()
    def broken(*args):
        raise foundation[3]("STORAGE_TIMEOUT", "Timeout", 504)
    files[1].read_verified = broken
    response = api.get(f"/api/v1/documents/{document_id}/content", headers={"Authorization": "Bearer " + service_auth(sub=owner)})
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "STORAGE_TIMEOUT"
    assert "X-Content-SHA256" not in response.headers


def test_attachment_sanitization(foundation):
    from app.core.downloads import content_disposition
    value = content_disposition('../\\a"\r\nX-Bad: header\u202e.pdf')
    assert "\r" not in value and "\n" not in value and "\u202e" not in value
    assert value.startswith('attachment; filename="')


@pytest.mark.parametrize("value", ["https://evil.example/object/sign/x?token=secret", "//evil.example/x?token=secret",
    "/object/public/document-files/a?token=secret", "/object/sign/document-files/safe/owner/id/content?token=",
    "/object/sign/document-files/safe/owner/id/content?token=one&token=two",
    "/object/sign/document-files/safe/owner/id/content?token=one&redirect=https://evil.example"])
def test_signed_url_rejects_wrong_host_path_or_query(foundation, config_env, value):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"signedURL": value}))
    with pytest.raises(foundation[3]) as exc:
        foundation[6](foundation[1].from_env(), transport=transport).signed_download("safe/owner/id/content", "a.pdf")
    assert exc.value.code == "STORAGE_BAD_RESPONSE"
    assert "secret" not in str(exc.value)


def test_signed_url_filename_encoding_and_backend_key(foundation, config_env):
    def handler(request):
        assert request.method == "POST"
        assert json.loads(request.content) == {"expiresIn": 300}
        assert request.headers["apikey"] == "sb_secret_test_only"
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"signedURL": "/object/sign/document-files/safe/owner/id/content?token=test"})
    storage = foundation[6](foundation[1].from_env(), transport=httpx.MockTransport(handler))
    url = storage.signed_download("safe/owner/id/content", "Hợp đồng & mẫu.pdf")
    query = parse_qs(urlsplit(url).query)
    assert query == {"token": ["test"], "download": ["Hợp đồng & mẫu.pdf"]}


@pytest.mark.parametrize("mode,code,status", [("hash", "STORAGE_BAD_RESPONSE", 502), ("oversize", "STORAGE_BAD_RESPONSE", 502),
    ("truncated", "STORAGE_BAD_RESPONSE", 502), ("mime", "STORAGE_BAD_RESPONSE", 502),
    ("gzip", "STORAGE_BAD_RESPONSE", 502), ("redirect", "STORAGE_BAD_RESPONSE", 502),
    ("timeout", "STORAGE_TIMEOUT", 504), ("network", "STORAGE_UNAVAILABLE", 503),
    ("missing", "STORAGE_UNAVAILABLE", 503)])
def test_storage_read_failure_bounded_and_sanitized(foundation, config_env, mode, code, status):
    content = b"test-content"
    def handler(request):
        assert request.headers["Accept-Encoding"] == "identity"
        if mode == "timeout": raise httpx.ReadTimeout("secret signed link")
        if mode == "network": raise httpx.ConnectError("secret key")
        if mode == "redirect": return httpx.Response(302, headers={"Location": "https://evil.example"})
        if mode == "missing": return httpx.Response(404)
        headers = {"Content-Type": "image/png" if mode == "mime" else "application/pdf"}
        if mode == "gzip": headers["Content-Encoding"] = "gzip"
        body = b"x" * len(content) if mode == "hash" else content
        if mode == "oversize": body += b"extra"
        if mode == "truncated": body = body[:-1]
        return httpx.Response(200, content=body, headers=headers)
    storage = foundation[6](foundation[1].from_env(), transport=httpx.MockTransport(handler))
    with pytest.raises(foundation[3]) as exc:
        storage.read_verified("safe/owner/id/content", len(content), hashlib.sha256(content).hexdigest(), "application/pdf")
    assert exc.value.code == code and exc.value.status == status
    assert "secret" not in str(exc.value)


def test_storage_verified_read_closes_stream(foundation, config_env):
    content = b"test"
    class Stream(httpx.SyncByteStream):
        closed = False
        def __iter__(self):
            yield content[:2]
            yield content[2:]
        def close(self): self.closed = True
    stream = Stream()
    def handler(request):
        assert request.url.path == "/storage/v1/object/authenticated/document-files/safe/owner/id/content"
        return httpx.Response(200, stream=stream, headers={"Content-Type": "application/pdf"})
    storage = foundation[6](foundation[1].from_env(), transport=httpx.MockTransport(handler))
    assert storage.read_verified("safe/owner/id/content", 4, hashlib.sha256(content).hexdigest(), "application/pdf") == content
    assert stream.closed
