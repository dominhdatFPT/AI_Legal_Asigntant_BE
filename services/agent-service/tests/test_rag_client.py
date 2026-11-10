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


# ── Cache kết quả tìm luật (RAG_CACHE_ENABLED) ─────────────────────────────

class Rag:
    """Handler giả luôn trả chunk mẫu, đếm số request thực sự gửi đi và ghi lại body."""

    def __init__(self, chunk, outcomes=None):
        self.chunk, self.calls, self.bodies, self.outcomes = chunk, 0, [], list(outcomes or [])

    def __call__(self, request: httpx.Request):
        self.calls += 1
        self.bodies.append(request.read().decode())
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if outcome == "5xx":
                return httpx.Response(500, json={"error": {"code": "INTERNAL_ERROR"}})
            if outcome == "empty":
                return httpx.Response(200, json={"data": {"chunks": []}})
        return httpx.Response(200, json={"data": {"chunks": [dict(self.chunk)]}})


@pytest.fixture(autouse=True)
def clean_cache():
    """Cache là trạng thái cấp module: mỗi test bắt đầu và kết thúc với cache trống."""
    rag_client.clear_cache()
    yield
    rag_client.clear_cache()


def set_cache(monkeypatch, on=True, **env):
    from app.core.config import get_settings

    monkeypatch.setenv("RAG_CACHE_ENABLED", "true" if on else "false")
    for name, value in env.items():
        monkeypatch.setenv(name, str(value))
    get_settings.cache_clear()
    rag_client.clear_cache()


def test_cache_off_by_default_calls_rag_every_time(chunk):
    rag = Rag(chunk)
    run_search(rag)
    run_search(rag)
    assert rag.calls == 2


def test_cache_explicitly_off_calls_rag_every_time_and_never_touches_cache(chunk, monkeypatch):
    set_cache(monkeypatch, on=False)

    def boom():
        raise AssertionError("cờ tắt thì không được đụng tới cache")

    monkeypatch.setattr(rag_client, "_get_cache", boom)
    rag = Rag(chunk)
    run_search(rag)
    run_search(rag)
    assert rag.calls == 2


def test_cache_on_same_question_in_different_forms_calls_rag_once(chunk, monkeypatch):
    set_cache(monkeypatch)
    rag = Rag(chunk)
    first = run_search(rag, query="Sang tên sổ đỏ cần gì?", top_k=4)
    second = run_search(rag, query="  sang TÊN   sổ đỏ\ncần gì ", top_k=4)
    third = run_search(rag, query="sang tên sổ đỏ cần gì?!", top_k=4)
    assert rag.calls == 1
    assert first == second == third == [chunk]


def test_cache_on_different_top_k_calls_rag_twice(chunk, monkeypatch):
    set_cache(monkeypatch)
    rag = Rag(chunk)
    run_search(rag, top_k=4)
    run_search(rag, top_k=5)
    assert rag.calls == 2
    run_search(rag, top_k=4)
    run_search(rag, top_k=5)
    assert rag.calls == 2  # mỗi top_k một mục riêng


def test_cache_on_different_question_calls_rag_again(chunk, monkeypatch):
    set_cache(monkeypatch)
    rag = Rag(chunk)
    run_search(rag, query="đất")
    run_search(rag, query="dat")  # giữ dấu tiếng Việt: khác khoá
    assert rag.calls == 2


def test_cache_does_not_store_failures(chunk, monkeypatch):
    set_cache(monkeypatch)
    rag = Rag(chunk, outcomes=["5xx"])
    with pytest.raises(rag_client.RAGError):
        run_search(rag)
    assert len(rag_client._get_cache()) == 0
    assert run_search(rag) == [chunk]  # lần sau vẫn gọi rag
    assert rag.calls == 2


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(200, text="not json"),
    lambda r: httpx.Response(200, json={"data": {"chunks": "x"}}),
    lambda r: httpx.Response(401, json={"error": {"code": "UNAUTHORIZED"}}),
])
def test_cache_does_not_store_invalid_responses(handler, monkeypatch):
    set_cache(monkeypatch)
    with pytest.raises(rag_client.RAGError):
        run_search(handler)
    assert len(rag_client._get_cache()) == 0


def test_cache_does_not_store_empty_result(chunk, monkeypatch):
    set_cache(monkeypatch)
    rag = Rag(chunk, outcomes=["empty"])
    assert run_search(rag) == []
    assert len(rag_client._get_cache()) == 0
    assert run_search(rag) == [chunk]
    assert rag.calls == 2


def test_cache_mutating_first_result_does_not_change_second(chunk, monkeypatch):
    set_cache(monkeypatch)
    rag = Rag(chunk)
    first = run_search(rag)
    first.append({"id": "rác"})
    first[0]["content"] = "đã bị sửa"
    first[0]["section_info"]["dieu"] = "999"
    second = run_search(rag)
    assert rag.calls == 1
    assert second == [chunk]
    second[0]["document"]["title"] = "sửa tiếp"
    assert run_search(rag) == [chunk]


def test_cache_hit_skips_rag_even_when_breaker_is_open(chunk, monkeypatch):
    set_cache(monkeypatch)
    rag = Rag(chunk)
    run_search(rag, query="câu đã có trong cache")
    rag.outcomes = ["5xx"] * 3
    for i in range(3):  # ba lỗi liên tiếp ở câu khác: breaker ngắt
        with pytest.raises(rag_client.RAGError):
            run_search(rag, query=f"câu khác số {i}")
    with pytest.raises(rag_client.RAGError, match="tạm ngắt"):
        run_search(rag, query="câu chưa có trong cache")
    calls = rag.calls
    assert run_search(rag, query="Câu đã có trong cache?") == [chunk]  # trúng cache: vẫn trả được khi rag ngắt
    assert rag.calls == calls


def test_cache_hit_logs_request_id_and_top_k_but_not_the_question(chunk, monkeypatch, caplog):
    set_cache(monkeypatch)
    rag = Rag(chunk)
    run_search(rag, query="Câu hỏi riêng tư ABC", top_k=3, request_id="req-77")
    with caplog.at_level("INFO", logger=rag_client.logger.name):
        run_search(rag, query="Câu hỏi riêng tư ABC", top_k=3, request_id="req-77")
    hits = [r.getMessage() for r in caplog.records if "rag cache hit" in r.getMessage()]
    assert hits == ["rag cache hit request_id=req-77 top_k=3"]
    assert "riêng tư" not in caplog.text


def test_cache_key_uses_query_truncated_to_max_length(chunk, monkeypatch):
    set_cache(monkeypatch)
    rag = Rag(chunk)
    base = "a" * rag_client.MAX_QUERY_LENGTH
    run_search(rag, query=base + "đuôi một")
    run_search(rag, query=base + "đuôi hai")  # sau khi cắt 1000 ký tự thì giống nhau
    assert rag.calls == 1


def test_cache_expires_after_ttl(chunk, monkeypatch):
    set_cache(monkeypatch, RAG_CACHE_TTL_SECONDS=60)
    now = [5000.0]
    monkeypatch.setattr(rag_client.time, "monotonic", lambda: now[0])
    rag = Rag(chunk)
    run_search(rag)
    now[0] += 59
    run_search(rag)
    assert rag.calls == 1
    now[0] += 2
    run_search(rag)
    assert rag.calls == 2


def test_cache_evicts_oldest_when_full(chunk, monkeypatch):
    set_cache(monkeypatch, RAG_CACHE_MAX_ITEMS=2)
    rag = Rag(chunk)
    for q in ("một", "hai", "ba"):
        run_search(rag, query=q)
    assert rag.calls == 3 and len(rag_client._get_cache()) == 2
    run_search(rag, query="ba")
    assert rag.calls == 3
    run_search(rag, query="một")  # đã bị bỏ vì cũ nhất
    assert rag.calls == 4


@pytest.mark.parametrize("env", [{"RAG_CACHE_MAX_ITEMS": 0}, {"RAG_CACHE_TTL_SECONDS": 0}])
def test_cache_on_but_zero_size_or_ttl_stores_nothing(chunk, monkeypatch, env):
    set_cache(monkeypatch, **env)
    rag = Rag(chunk)
    run_search(rag)
    run_search(rag)
    assert rag.calls == 2


def test_cache_read_error_falls_back_to_rag(chunk, monkeypatch, caplog):
    set_cache(monkeypatch)

    class Broken:
        def get(self, key):
            raise RuntimeError("cache hỏng")

        def set(self, key, value):
            raise RuntimeError("cache hỏng")

    monkeypatch.setattr(rag_client, "_get_cache", lambda: Broken())
    rag = Rag(chunk)
    with caplog.at_level("WARNING", logger=rag_client.logger.name):
        assert run_search(rag, request_id="req-1") == [chunk]
        assert run_search(rag) == [chunk]
    assert rag.calls == 2
    assert "rag cache lỗi khi đọc" in caplog.text


def test_cache_write_error_still_returns_result(chunk, monkeypatch, caplog):
    set_cache(monkeypatch)

    def broken_set(self, key, value):
        raise RuntimeError("không ghi được")

    monkeypatch.setattr(rag_client.cache.TTLCache, "set", broken_set)
    rag = Rag(chunk)
    with caplog.at_level("WARNING", logger=rag_client.logger.name):
        assert run_search(rag) == [chunk]
    assert "rag cache lỗi khi ghi" in caplog.text


def test_cache_flag_default_values():
    from app.core.config import get_settings

    s = get_settings()
    assert (s.rag_cache_enabled, s.rag_cache_ttl_seconds, s.rag_cache_max_items) == (False, 86400, 1000)
