"""Gọi service khác: chỉ retry GET (tối đa 2 lần), map lỗi 502/503/504, truyền X-Request-ID."""

import httpx
import pytest

from app.core.responses import ApiError, current_request_id
from app.services import http_client


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(http_client, "sleep", lambda seconds: seen.append(("sleep", seconds)))
    return seen


def _use(monkeypatch, handler):
    monkeypatch.setattr(http_client, "transport", httpx.MockTransport(handler))


def test_get_retry_toi_da_hai_lan(monkeypatch, calls):
    attempts = []

    def handler(request):
        attempts.append(request)
        if len(attempts) < 3:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, json={"data": {"ok": True}, "error": None})

    _use(monkeypatch, handler)
    assert http_client.call("USER", "GET", "/internal/v1/users/x/status") == {"ok": True}
    assert len(attempts) == 3 and calls == [("sleep", 0.5), ("sleep", 1.0)]


def test_post_khong_retry(monkeypatch, calls):
    attempts = []

    def handler(request):
        attempts.append(request)
        raise httpx.ReadTimeout("slow", request=request)

    _use(monkeypatch, handler)
    with pytest.raises(ApiError) as caught:
        http_client.call("USER", "POST", "/internal/v1/users", json={})
    assert caught.value.status == 504 and caught.value.code == "USER_TIMEOUT" and len(attempts) == 1


@pytest.mark.parametrize("response", [httpx.Response(302, headers={"Location": "https://evil.example"}),
                                      httpx.Response(200, text="<html>"), httpx.Response(200, json=[1]),
                                      httpx.Response(200, json={"error": None}),
                                      httpx.Response(404, text="not json")])
def test_du_lieu_sai_cau_truc_la_502(monkeypatch, calls, response):
    _use(monkeypatch, lambda request: response)
    with pytest.raises(ApiError) as caught:
        http_client.call("DOCUMENT", "POST", "/internal/v1/documents/lawyer-cards/x/view-url")
    assert caught.value.status == 502 and caught.value.code == "DOCUMENT_BAD_RESPONSE"


def test_4xx_la_ket_qua_nghiep_vu(monkeypatch, calls):
    _use(monkeypatch, lambda request: httpx.Response(409, json={"data": None, "error": {
        "code": "EMAIL_IN_USE", "message": "Email đã dùng"}}))
    with pytest.raises(http_client.UpstreamError) as caught:
        http_client.call("USER", "POST", "/internal/v1/users", json={})
    assert caught.value.code == "EMAIL_IN_USE"


def test_thieu_cau_hinh_la_503(monkeypatch, calls):
    monkeypatch.delenv("USER_SERVICE_URL")
    with pytest.raises(ApiError) as caught:
        http_client.call("USER", "GET", "/x")
    assert caught.value.status == 503


def test_truyen_request_id_va_internal_token(monkeypatch, calls):
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json={"data": None, "error": None})

    _use(monkeypatch, handler)
    token = current_request_id.set("req-abc")
    try:
        http_client.call("USER", "GET", "/x")
    finally:
        current_request_id.reset(token)
    assert seen["x-request-id"] == "req-abc" and seen["x-internal-token"] == "test-internal-token"
