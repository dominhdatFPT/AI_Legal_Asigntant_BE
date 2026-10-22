"""Test document_client với document-service giả lập (httpx.MockTransport), không cần mạng."""
import asyncio
import uuid

import httpx
import pytest

from app.services import document_client

DOC_ID = uuid.UUID("88888888-8888-4888-8888-888888888888")
OK_BODY = {
    "data": {"document_id": str(DOC_ID), "source_kind": "docx", "text": "Bên A: Nguyễn Văn An", "pages": [], "warnings": []},
    "error": None, "request_id": "req-1",
}


def run_get(handler, **kwargs):
    return asyncio.run(document_client.get_extraction(
        DOC_ID, "user-token", transport=httpx.MockTransport(handler), **kwargs))


def error_body(code):
    return {"data": None, "error": {"code": code, "message": "..."}, "request_id": "req-1"}


def test_get_extraction_forwards_user_token_and_returns_text():
    seen = {}

    def handler(request: httpx.Request):
        seen["request"] = request
        return httpx.Response(200, json=OK_BODY)

    result = run_get(handler, request_id="req-1")

    request = seen["request"]
    assert str(request.url) == f"http://document-test:8000/api/v1/documents/{DOC_ID}/extraction"
    assert request.headers["Authorization"] == "Bearer user-token"
    assert request.headers["X-Request-ID"] == "req-1"
    assert "X-Internal-Token" not in request.headers
    assert result == {"document_id": str(DOC_ID), "source_kind": "docx", "text": "Bên A: Nguyễn Văn An"}


@pytest.mark.parametrize("status", [401, 403, 404])
def test_not_owner_or_missing_is_document_not_found(status):
    with pytest.raises(document_client.DocumentNotFound):
        run_get(lambda r: httpx.Response(status, json=error_body("DOCUMENT_NOT_FOUND")))


def test_not_ready_says_still_processing():
    with pytest.raises(document_client.DocumentNotReady) as e:
        run_get(lambda r: httpx.Response(409, json=error_body("EXTRACTION_NOT_READY")))
    assert "đang xử lý" in str(e.value)


@pytest.mark.parametrize("code", ["EXTRACTION_FAILED", "FILE_REJECTED"])
def test_failed_or_rejected_says_not_readable_instead_of_retry(code):
    with pytest.raises(document_client.DocumentNotReady) as e:
        run_get(lambda r: httpx.Response(409, json=error_body(code)))
    assert "đang xử lý" not in str(e.value)
    assert "tải tài liệu khác" in str(e.value)


def test_409_without_json_body_is_treated_as_processing():
    with pytest.raises(document_client.DocumentNotReady) as e:
        run_get(lambda r: httpx.Response(409, text="conflict"))
    assert "đang xử lý" in str(e.value)


def test_timeout_and_connection_error_raise_service_error():
    def timeout(request):
        raise httpx.ReadTimeout("timeout", request=request)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    for handler in (timeout, refused):
        with pytest.raises(document_client.DocumentServiceError):
            run_get(handler)


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(500, json=error_body("INTERNAL_ERROR")),
    lambda r: httpx.Response(502, json=error_body("EXTRACTION_BAD_RESPONSE")),
    lambda r: httpx.Response(503, json=error_body("DATABASE_UNAVAILABLE")),
    lambda r: httpx.Response(200, text="not json"),
    lambda r: httpx.Response(200, json={"data": {"source_kind": "docx"}}),  # thiếu text
    lambda r: httpx.Response(200, json={"data": {"source_kind": "docx", "text": 123}}),
])
def test_server_error_or_bad_response_raises_service_error(handler):
    with pytest.raises(document_client.DocumentServiceError):
        run_get(handler)


def test_get_extractions_keeps_order_and_forwards_token():
    ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    tokens = []

    def handler(request: httpx.Request):
        tokens.append(request.headers["Authorization"])
        doc_id = request.url.path.split("/")[-2]
        return httpx.Response(200, json={"data": {"source_kind": "pdf_text", "text": f"chữ của {doc_id}"}})

    results = asyncio.run(document_client.get_extractions(
        ids, "user-token", "req-1", transport=httpx.MockTransport(handler)))

    assert [r["document_id"] for r in results] == [str(i) for i in ids]
    assert [r["text"] for r in results] == [f"chữ của {i}" for i in ids]
    assert tokens == ["Bearer user-token"] * 3


def test_get_extractions_fails_when_any_document_fails():
    ids = [uuid.uuid4(), uuid.uuid4()]

    def handler(request: httpx.Request):
        if str(ids[1]) in request.url.path:
            return httpx.Response(404, json=error_body("DOCUMENT_NOT_FOUND"))
        return httpx.Response(200, json={"data": {"source_kind": "docx", "text": "ok"}})

    with pytest.raises(document_client.DocumentNotFound):
        asyncio.run(document_client.get_extractions(ids, "t", transport=httpx.MockTransport(handler)))
