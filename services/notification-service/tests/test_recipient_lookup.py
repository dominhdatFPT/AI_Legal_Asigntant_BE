import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import recipient_service  # noqa: E402
from app.services.report_service import ReportDomainError  # noqa: E402

REQUEST = SimpleNamespace(headers=httpx.Headers({
    "X-Request-ID": "req-broadcast", "Authorization": "Bearer secret", "Cookie": "session=secret",
}))
LOOKUP = "/internal/v1/users/lookup"


@pytest.fixture(autouse=True)
def user_service(monkeypatch):
    monkeypatch.setenv("USER_SERVICE_URL", "https://users.test/")
    monkeypatch.setenv("INTERNAL_TOKEN", "lookup-token")


def _user(uid, **changes):
    return {"id": uid, "role": "citizen", "status": "active", "deleted_at": None, **changes}


def _items(items):
    return httpx.Response(200, json={"data": {"items": items}})


def _validate(monkeypatch, handler, ids, audience="citizen"):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), cookies={"upstream": "private"}) as client:
            monkeypatch.setattr(recipient_service, "_get_http_client", lambda: client)
            return await recipient_service.validate_recipients(ids, audience, REQUEST)
    return asyncio.run(run())


def test_bulk_lookup_sends_only_service_auth_and_preserves_requested_order(monkeypatch):
    ids = [str(uuid4()) for _ in range(3)]
    calls = []

    def handler(request):
        assert (request.method, request.url.path) == ("POST", LOOKUP)
        assert request.headers["X-Internal-Token"] == "lookup-token"
        assert request.headers["X-Request-ID"] == "req-broadcast"
        assert "Authorization" not in request.headers and "Cookie" not in request.headers
        calls.append(json.loads(request.content)["user_ids"])
        return _items([_user(uid) for uid in reversed(ids)])

    result = _validate(monkeypatch, handler, [*ids, ids[0]])
    assert calls == [ids]
    assert [item["id"] for item in result] == ids


@pytest.mark.parametrize(("changes", "code"), [
    (None, "INVALID_RECIPIENT"),
    ({"deleted_at": "2026-01-01"}, "INVALID_RECIPIENT"),
    ({"status": "locked"}, "INVALID_RECIPIENT"),
    ({"role": "lawyer"}, "RECIPIENT_ROLE_MISMATCH"),
])
def test_bulk_lookup_checks_account_and_role(monkeypatch, changes, code):
    uid = str(uuid4())
    with pytest.raises(ReportDomainError) as exc:
        _validate(monkeypatch, lambda req: _items([] if changes is None else [_user(uid, **changes)]), [uid])
    assert (exc.value.status_code, exc.value.code) == (422, code)


@pytest.mark.parametrize("unsupported", [None, 404, 405])
def test_lookup_falls_back_to_get_per_id(monkeypatch, unsupported):
    if unsupported is None:
        monkeypatch.delenv("INTERNAL_TOKEN")
    ids = [str(uuid4()), str(uuid4())]
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            assert unsupported is not None
            return httpx.Response(unsupported)
        assert request.headers["Authorization"] == "Bearer secret"
        uid = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"data": _user(uid)})

    assert [item["id"] for item in _validate(monkeypatch, handler, ids)] == ids
    if unsupported:
        assert calls.pop(0) == ("POST", LOOKUP)
    assert sorted(calls) == sorted(("GET", f"/api/v1/users/{uid}") for uid in ids)


@pytest.mark.parametrize("failure", ["connect", 500, "malformed"])
def test_lookup_failure_returns_503_without_fallback(monkeypatch, failure):
    def handler(request):
        assert request.method == "POST"
        if failure == "connect":
            raise httpx.ConnectError("down", request=request)
        if failure == "malformed":
            return _items([{"role": "citizen"}])
        return httpx.Response(failure)

    with pytest.raises(ReportDomainError) as exc:
        _validate(monkeypatch, handler, [str(uuid4())])
    assert (exc.value.status_code, exc.value.code) == (503, "RECIPIENT_LOOKUP_UNAVAILABLE")


def test_bulk_lookup_splits_150_ids_into_two_posts(monkeypatch):
    ids = [str(uuid4()) for _ in range(150)]
    batches = []

    def handler(request):
        assert (request.method, request.url.path) == ("POST", LOOKUP)
        batch = json.loads(request.content)["user_ids"]
        batches.append(batch)
        return _items([_user(uid) for uid in batch])

    result = _validate(monkeypatch, handler, ids)
    assert batches == [ids[:100], ids[100:]]
    assert [item["id"] for item in result] == ids


def test_validate_all_uuids_before_looking_up_any_recipient(monkeypatch):
    http = Mock()
    monkeypatch.setattr(recipient_service, "_get_http_client", http)
    with pytest.raises(ReportDomainError) as exc:
        asyncio.run(recipient_service.validate_recipients([str(uuid4()), "me"], "all", REQUEST))
    assert (exc.value.status_code, exc.value.code) == (422, "INVALID_RECIPIENT")
    http.assert_not_called()
