import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import httpx
import jwt
import pytest
from common_auth import breaker

from app.core.responses import ApiError
from app.services import http_client, user_client, user_events


@pytest.fixture
def account():
    user_id = uuid4()
    token = jwt.encode({"sub": str(user_id)}, "fixture-secret-key-long-enough-for-hs256", algorithm="HS256")
    yield user_id, token
    user_client.forget_user(user_id)


@pytest.fixture
def me(monkeypatch):
    handler = Mock(return_value=httpx.Response(200, json={"data": {"active": True}}))
    monkeypatch.setattr(http_client, "transport", httpx.MockTransport(handler))
    return handler


@pytest.mark.parametrize("ttl", [None, "0", "", "bad", "-10", "NaN", "inf", "-inf"])
def test_nonpositive_or_invalid_cache_ttl_calls_me_twice(monkeypatch, account, me, ttl):
    if ttl is None:
        monkeypatch.delenv("USER_ACTIVE_CACHE_SECONDS", raising=False)
    else:
        monkeypatch.setenv("USER_ACTIVE_CACHE_SECONDS", ttl)
    _, token = account
    user_client.ensure_active(token)
    user_client.ensure_active(token)
    assert me.call_count == 2
    assert all(call.args[0].url.path == "/api/v1/users/me" for call in me.call_args_list)


def test_positive_cache_reuses_the_same_token_until_expiry(monkeypatch, account, me):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(user_client, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setenv("USER_ACTIVE_CACHE_SECONDS", "30")
    _, token = account
    user_client.ensure_active(token)
    clock.now = 29
    user_client.ensure_active(token)
    assert me.call_count == 1
    clock.now = 30
    user_client.ensure_active(token)
    assert me.call_count == 2


@pytest.mark.parametrize("payload_as_string", [False, True])
def test_block_event_forgets_user_before_closing_consultations(monkeypatch, account, me, payload_as_string):
    monkeypatch.setenv("USER_ACTIVE_CACHE_SECONDS", "30")
    user_id, token = account
    user_client.ensure_active(token)

    def close(user_id_to_close, reason, blocked_at):
        assert user_id_to_close == user_id
        user_client.ensure_active(token)
        return []

    monkeypatch.setattr(user_events, "_close_in_session", close)
    event = {"payload": {"user_id": str(user_id) if payload_as_string else user_id}}
    asyncio.run(user_events.handle_user_event("user.blocked", event))
    assert me.call_count == 2


def test_revocation_during_a_lookup_prevents_caching_the_old_response(monkeypatch, account, me):
    monkeypatch.setenv("USER_ACTIVE_CACHE_SECONDS", "30")
    user_id, token = account

    def active_before_revocation(_):
        user_client.forget_user(user_id)
        return httpx.Response(200, json={"data": {}})

    me.side_effect = active_before_revocation
    user_client.ensure_active(token)
    me.side_effect = None
    user_client.ensure_active(token)
    assert me.call_count == 2


def test_forbidden_me_is_never_cached(monkeypatch, account, me):
    monkeypatch.setenv("USER_ACTIVE_CACHE_SECONDS", "30")
    me.return_value = httpx.Response(403, json={"error": {"code": "ACCOUNT_LOCKED", "message": "Locked"}})
    for _ in range(2):
        with pytest.raises(ApiError) as caught:
            user_client.ensure_active(account[1])
        assert (caught.value.status, caught.value.code) == (403, "ACCOUNT_LOCKED")
    assert me.call_count == 2


def test_open_user_breaker_returns_503_without_http(monkeypatch, account, me):
    monkeypatch.delenv("USER_ACTIVE_CACHE_SECONDS", raising=False)
    gate = breaker.get("consultation:USER", failures=1)
    assert gate.failure()

    with pytest.raises(ApiError) as caught:
        user_client.ensure_active(account[1])
    assert (caught.value.status, caught.value.code) == (503, "USER_UNAVAILABLE")
    me.assert_not_called()


def test_agent_summary_probe_survives_past_the_default_probe_timeout(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(breaker, "time", SimpleNamespace(monotonic=lambda: clock.now))
    gate = breaker.get("consultation:AGENT", failures=1, open_seconds=5)
    assert gate.failure()
    clock.now = 5
    probing, finish = Event(), Event()

    def respond(request):
        assert request.extensions["timeout"]["read"] == 60.0
        probing.set()
        assert finish.wait(timeout=5)
        return httpx.Response(200, json={"data": {"summary": "Ready"}})

    handler = Mock(side_effect=respond)
    monkeypatch.setattr(http_client, "transport", httpx.MockTransport(handler))
    path = "/internal/v1/agent/conversations/test/summary"
    with ThreadPoolExecutor(max_workers=1) as pool:
        probe = pool.submit(http_client.call, "AGENT", "POST", path, timeout=60.0)
        try:
            assert probing.wait(timeout=5)
            clock.now = 20
            with pytest.raises(ApiError) as caught:
                http_client.call("AGENT", "POST", path, timeout=60.0)
            assert (caught.value.status, caught.value.code) == (503, "AGENT_UNAVAILABLE")
            handler.assert_called_once()
        finally:
            finish.set()
        assert probe.result(timeout=5) == {"summary": "Ready"}

    assert http_client.call("AGENT", "POST", path, timeout=60.0) == {"summary": "Ready"}
    assert gate.allow()
