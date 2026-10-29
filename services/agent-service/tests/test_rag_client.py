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


# ── Circuit breaker ────────────────────────────────────────────────────────


class CountingRag:
    """Handler giả đếm số request thực sự gửi đi; `plan` là danh sách kết quả theo thứ tự (lỗi hoặc Response)."""

    def __init__(self, plan):
        self.plan, self.calls = list(plan), 0

    def __call__(self, request: httpx.Request):
        self.calls += 1
        outcome = self.plan.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def ok():
    return httpx.Response(200, json={"data": {"chunks": []}})


def down(kind):
    return {
        "5xx": lambda: httpx.Response(503, json={"error": {"code": "UNAVAILABLE"}}),
        "timeout": lambda: httpx.ReadTimeout("quá chậm"),
        "connect": lambda: httpx.ConnectError("không kết nối được"),
    }[kind]()


@pytest.mark.parametrize("kind", ["5xx", "timeout", "connect"])
def test_breaker_opens_after_three_failures_and_skips_http(kind):
    rag = CountingRag([down(kind) for _ in range(3)] + [ok()])
    for _ in range(3):
        with pytest.raises(rag_client.RAGError):
            run_search(rag)
    assert rag.calls == 3

    with pytest.raises(rag_client.RAGError, match="tạm ngắt"):
        run_search(rag)
    assert rag.calls == 3  # lần thứ 4 không gửi HTTP


def test_breaker_closes_again_after_open_seconds(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(rag_client.time, "monotonic", lambda: now[0])
    rag = CountingRag([down("5xx") for _ in range(3)] + [ok(), ok()])
    for _ in range(3):
        with pytest.raises(rag_client.RAGError):
            run_search(rag)

    now[0] += rag_client._OPEN_SECONDS - 1
    with pytest.raises(rag_client.RAGError, match="tạm ngắt"):
        run_search(rag)
    assert rag.calls == 3

    now[0] += 2  # đã qua 30 giây
    assert run_search(rag) == []
    assert rag.calls == 4


def test_success_in_between_resets_failure_counter():
    rag = CountingRag([down("5xx"), down("timeout"), ok(), down("5xx"), down("connect"), ok()])
    for _ in range(2):
        with pytest.raises(rag_client.RAGError):
            run_search(rag)
    assert run_search(rag) == []  # thành công: bộ đếm về 0
    for _ in range(2):
        with pytest.raises(rag_client.RAGError):
            run_search(rag)
    # Mới 2 lỗi liên tiếp sau lần thành công, breaker chưa mở nên lần thứ 6 vẫn gửi HTTP
    assert run_search(rag) == []
    assert rag.calls == 6


def test_client_errors_do_not_count_as_failures():
    rag = CountingRag([httpx.Response(401, json={"error": {"code": "UNAUTHORIZED"}}) for _ in range(5)])
    for _ in range(5):
        with pytest.raises(rag_client.RAGError, match="401"):
            run_search(rag)
    assert rag.calls == 5
