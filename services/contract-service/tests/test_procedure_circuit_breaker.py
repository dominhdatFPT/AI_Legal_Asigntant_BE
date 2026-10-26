"""Circuit breaker của client gọi procedure-service (DraftReferences), procedure-service giả lập bằng MockTransport."""
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

SERVICE = Path(__file__).resolve().parents[1]
OWNER = str(uuid4())
PROCEDURE = str(uuid4())


@pytest.fixture
def refs():
    # Nhiều service cùng đặt tên package `app`: nạp bản của Contract rồi khôi phục như các test khác trong repo.
    previous = {name: module for name, module in list(sys.modules.items()) if name == "app" or name.startswith("app.")}
    for name in previous:
        del sys.modules[name]
    sys.path.insert(0, str(SERVICE))
    try:
        from app.core.errors import ContractError
        from app.services import draft_references

        draft_references._fails, draft_references._open_until = 0, 0.0
        yield SimpleNamespace(module=draft_references, ContractError=ContractError)
    finally:
        sys.path.remove(str(SERVICE))
        for name in list(sys.modules):
            if name == "app" or name.startswith("app."):
                del sys.modules[name]
        sys.modules.update(previous)


class CountingProcedure:
    """Đếm số request thực sự gửi đi; `plan` là danh sách kết quả theo thứ tự (lỗi hoặc Response)."""

    def __init__(self, plan):
        self.plan, self.calls = list(plan), 0

    def __call__(self, request: httpx.Request):
        assert request.url.path == "/api/v1/procedures/" + PROCEDURE
        self.calls += 1
        outcome = self.plan.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def ok():
    return httpx.Response(200, json={"data": {"id": PROCEDURE, "name": "Sang tên sổ đỏ", "requirements": []},
                                     "error": None, "request_id": "req-procedure"})


def down(kind):
    return {
        "5xx": lambda: httpx.Response(503, json={"error": {"code": "UNAVAILABLE"}}),
        "timeout": lambda: httpx.ReadTimeout("quá chậm"),
        "connect": lambda: httpx.ConnectError("không kết nối được"),
    }[kind]()


def call(refs, handler):
    client = refs.module.DraftReferences("", "http://procedure-service:8000", transport=httpx.MockTransport(handler))
    return client.ensure(OWNER, "token", "req-test", [], PROCEDURE)


def assert_unavailable(refs, handler):
    with pytest.raises(refs.ContractError) as exc:
        call(refs, handler)
    assert exc.value.code == "PROCEDURE_SERVICE_UNAVAILABLE" and exc.value.status == 503


@pytest.mark.parametrize("kind", ["5xx", "timeout", "connect"])
def test_breaker_opens_after_three_failures_and_skips_http(refs, kind):
    handler = CountingProcedure([down(kind) for _ in range(3)] + [ok()])
    for _ in range(3):
        assert_unavailable(refs, handler)
    assert handler.calls == 3

    assert_unavailable(refs, handler)  # cùng lỗi cũ, không phải lỗi mới
    assert handler.calls == 3  # lần thứ 4 không gửi HTTP


def test_breaker_closes_again_after_open_seconds(refs, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(refs.module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    handler = CountingProcedure([down("5xx") for _ in range(3)] + [ok()])
    for _ in range(3):
        assert_unavailable(refs, handler)

    now[0] += refs.module._OPEN_SECONDS - 1
    assert_unavailable(refs, handler)
    assert handler.calls == 3

    now[0] += 2  # đã qua 30 giây
    assert call(refs, handler) is None  # ensure thành công trả None
    assert handler.calls == 4


def test_success_in_between_resets_failure_counter(refs):
    handler = CountingProcedure([down("5xx"), down("timeout"), ok(), down("5xx"), down("connect"), ok()])
    for _ in range(2):
        assert_unavailable(refs, handler)
    assert call(refs, handler) is None  # thành công: bộ đếm về 0
    for _ in range(2):
        assert_unavailable(refs, handler)
    # Mới 2 lỗi liên tiếp sau lần thành công, breaker chưa mở nên lần thứ 6 vẫn gửi HTTP
    assert call(refs, handler) is None
    assert handler.calls == 6


def test_breaker_does_not_affect_document_calls(refs):
    for _ in range(3):
        assert_unavailable(refs, CountingProcedure([down("5xx")]))
    document_calls = []

    def document(request: httpx.Request):
        document_calls.append(request)
        return httpx.Response(503)

    client = refs.module.DraftReferences("http://document-service:8000", "", transport=httpx.MockTransport(document))
    for _ in range(4):
        with pytest.raises(refs.ContractError) as exc:
            client.ensure(OWNER, "token", "req-test", [uuid4()])
        assert exc.value.code == "DOCUMENT_SERVICE_UNAVAILABLE"
    assert len(document_calls) == 4
