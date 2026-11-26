from types import SimpleNamespace

import httpx
import pytest
from common_auth import breaker

from app.core.responses import ApiError
from app.services import http_client


@pytest.mark.parametrize(("kind", "method", "status", "attempts"), [
    ("5xx", "GET", 502, 3),
    ("timeout", "PUT", 504, 1),
])
def test_five_failed_calls_open_the_breaker_and_allow_a_later_probe(monkeypatch, kind, method, status, attempts):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(breaker, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(http_client, "sleep", lambda _: None)
    requests = []

    def upstream(request):
        requests.append(request)
        if clock.now >= 30.0:
            return httpx.Response(200, json={"data": {"ok": True}})
        if kind == "timeout":
            raise httpx.ReadTimeout("upstream timeout", request=request)
        return httpx.Response(500)

    monkeypatch.setattr(http_client, "transport", httpx.MockTransport(upstream))
    for _ in range(5):
        with pytest.raises(ApiError) as caught:
            http_client.call("USER", method, "/test")
        assert caught.value.status == status

    with pytest.raises(ApiError) as caught:
        http_client.call("USER", method, "/test")
    assert (caught.value.status, caught.value.code) == (503, "USER_UNAVAILABLE")
    assert len(requests) == 5 * attempts

    clock.now = 30.0
    assert http_client.call("USER", method, "/test") == {"ok": True}
    assert len(requests) == 5 * attempts + 1


def test_business_errors_and_successes_reset_the_failure_count(monkeypatch):
    statuses = iter([500] * 4 + [404] * 6 + [500] * 4 + [204] + [500] * 4)
    monkeypatch.setattr(http_client, "transport", httpx.MockTransport(
        lambda request: httpx.Response(next(statuses), json={"error": {"code": "NOT_FOUND", "message": "Missing"}}),
    ))

    def failures():
        for _ in range(4):
            with pytest.raises(ApiError) as caught:
                http_client.call("USER", "PUT", "/test")
            assert caught.value.status == 502

    failures()
    for _ in range(6):
        with pytest.raises(http_client.UpstreamError) as caught:
            http_client.call("USER", "PUT", "/test")
        assert caught.value.status == 404
    failures()
    assert http_client.call("USER", "PUT", "/test") is None
    failures()
