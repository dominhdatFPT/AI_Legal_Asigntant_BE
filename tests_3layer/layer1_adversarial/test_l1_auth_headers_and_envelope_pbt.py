import asyncio
import re

import httpx
import pytest
from hypothesis import example, given, settings, strategies as st

from common_auth.context import request_id_var, sanitize_request_id
from common_auth.events import make_event
from tests_3layer.support import INTERNAL_TOKEN, service_module

HOSTILE_TEXT = st.text(alphabet=st.characters(exclude_categories=()), max_size=512)
REQUEST_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")


@settings(max_examples=200)
@given(value=HOSTILE_TEXT)
@example(value="\ud800\udfffprefix\x00\r\nEvil-Header: injected")
@example(value="!" + "a" * 5000 + "\n")
@example(value=" \t\x1f\r\n")
def test_request_id_and_event_envelope_use_the_same_idempotent_normal_form(value):
    candidate = sanitize_request_id(value)
    assert sanitize_request_id(candidate) == candidate
    assert candidate is None or REQUEST_ID.fullmatch(candidate)
    legal = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-")
    expected = "".join(ch for ch in value if ch in legal)[:128] or None
    assert candidate == expected
    reset = request_id_var.set(None)
    try:
        payload = {"owner": "synthetic"}
        event = make_event("test.event", payload, event_id="test-id", request_id=value)
        canonical = make_event("test.event", payload, event_id="test-id", request_id=candidate)
        assert (event.get("request_id"), event.get("correlation_id")) == (candidate, candidate)
        assert event.get("request_id") == canonical.get("request_id")
        assert event["event_id"] == "test-id" and event["payload"] == payload
        token = request_id_var.set("fallback._-:001")
        try:
            assert make_event("test.event", {}, request_id="\r\n")["request_id"] == "fallback._-:001"
        finally:
            request_id_var.reset(token)
    finally:
        request_id_var.reset(reset)


async def post(app, path, headers=None, **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://service.test",
    ) as client:
        return await client.post(path, headers=headers, **kwargs)


@pytest.fixture(scope="module", params=[
    ("user", "/internal/v1/users/lookup", {"user_ids": ["11111111-1111-4111-8111-111111111111"]}),
    ("procedure", "/internal/v1/procedures", None),
])
def internal_endpoint(request):
    service, path, body = request.param
    # Module fixtures import the application before function-scoped fixtures.
    with pytest.MonkeyPatch.context() as env:
        env.setenv("PROCEDURE_SKIP_DOTENV", "1")
        with service_module(service, "app.main") as module:
            yield module.app, path, body


@settings(max_examples=100, deadline=None)
@given(value=st.binary(max_size=512).filter(lambda value: value != INTERNAL_TOKEN.encode()))
@example(value="Tiếng Việt 😀".encode())
@example(value=b"")
@example(value=b"\0" * 129)
@example(value=INTERNAL_TOKEN.encode()[:-1] + bytes([ord(INTERNAL_TOKEN[-1]) ^ 1]))
def test_malformed_internal_headers_are_denied(internal_endpoint, value):
    app, path, body = internal_endpoint
    with pytest.MonkeyPatch.context() as env:
        for configured in (True, False):
            env.setenv("INTERNAL_TOKEN", INTERNAL_TOKEN if configured else "")
            response = asyncio.run(post(app, path, [(b"x-internal-token", value)], json=body))
            assert response.status_code in ({401} if configured else {401, 503}), response.text


@pytest.mark.parametrize("token", [None, "incorrect"])
@pytest.mark.parametrize("path", ["/internal/v1/procedures", "/internal/v1/procedures/register"])
def test_unauthenticated_multipart_is_never_read(token, path, monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", INTERNAL_TOKEN)
    reads = []

    async def body():
        reads.append(True)
        raise RuntimeError("BODY_SHOULD_NOT_BE_READ")
        yield b""  # The receive call must trigger the sentinel.

    headers = {"Content-Type": "multipart/form-data; boundary=test-boundary"}
    if token is not None:
        headers["X-Internal-Token"] = token
    with service_module("procedure", "app.main") as module:
        response = asyncio.run(post(module.app, path, headers, content=body()))
    assert reads == []
    assert response.status_code == 401, response.text


def _iter_route_paths(routes, prefix=""):
    for route in routes:
        included_router = getattr(route, "original_router", None)
        if included_router is not None:
            # Newer FastAPI versions retain included routers and their include-time prefixes.
            include_prefix = getattr(getattr(route, "include_context", None), "prefix", "")
            yield from _iter_route_paths(included_router.routes, prefix + include_prefix)
        else:
            path = prefix + getattr(route, "path", "")
            yield path
            yield from _iter_route_paths(getattr(route, "routes", ()), path.rstrip("/"))


def test_notification_service_has_no_inbound_internal_routes():
    with service_module("notification", "app.main") as module:
        assert all(not path.startswith("/internal/") for path in _iter_route_paths(module.app.routes))
        response = asyncio.run(post(module.app, "/internal/v1/users/lookup", {"X-Internal-Token": INTERNAL_TOKEN}))
    assert response.status_code == 404, response.text
