"""Test rag_client với rag-service giả lập (httpx.MockTransport), không cần mạng."""
import asyncio

import httpx
import pytest

from app.services import rag_client


def run_search(handler, query="Sang tên sổ đỏ cần gì?", **kwargs):
    return asyncio.run(
        rag_client.search(query, transport=httpx.MockTransport(handler), **kwargs)
    )


def test_search_sends_contract_request_and_returns_chunks(chunk):
    seen = {}

    def handler(request: httpx.Request):
        seen["request"] = request
        return httpx.Response(200, json={
            "data": {"chunks": [chunk]}, "error": None, "request_id": "req-1",
        })

    chunks = run_search(handler, top_k=3, request_id="req-1")

    request = seen["request"]
    assert str(request.url) == "http://rag-test:8000/internal/v1/rag/search"
    assert request.headers["X-Internal-Token"] == "test-internal-token"
    assert request.headers["X-Request-ID"] == "req-1"
    assert request.read() == httpx.Request(
        "POST", "/", json={"query": "Sang tên sổ đỏ cần gì?", "top_k": 3}
    ).read()
    assert chunks == [chunk]


def test_search_truncates_long_query():
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"data": {"chunks": []}})

    run_search(handler, query="a" * 1500)

    assert '"' + "a" * 1000 + '"' in seen["body"]
    assert "a" * 1001 not in seen["body"]


def test_search_empty_result():
    chunks = run_search(lambda r: httpx.Response(200, json={"data": {"chunks": []}}))
    assert chunks == []


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(500, json={"error": {"code": "INTERNAL_ERROR"}}),
    lambda r: httpx.Response(401, json={"error": {"code": "UNAUTHORIZED"}}),
    lambda r: httpx.Response(200, text="not json"),
    lambda r: httpx.Response(200, json={"data": {"results": []}}),  # sai hợp đồng
    lambda r: httpx.Response(200, json={"data": {"chunks": "x"}}),
])
def test_search_bad_responses_raise_rag_error(handler):
    with pytest.raises(rag_client.RAGError):
        run_search(handler)


def test_search_connection_error_raises_rag_error():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(rag_client.RAGError):
        run_search(handler)


def test_search_timeout_raises_rag_error():
    def handler(request):
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(rag_client.RAGError):
        run_search(handler)
