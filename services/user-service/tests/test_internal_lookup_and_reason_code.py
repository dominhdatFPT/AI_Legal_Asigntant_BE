import asyncio
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import event

from app.core.rabbitmq_admin import admin_mq, publisher
from app.models import UserStatus
from test_admin_users import USERS, _h, _make, admin, citizen, client, env  # noqa: F401

LOOKUP = "/internal/v1/users/lookup"
HEADERS = {"X-Internal-Token": "test-lookup"}


@pytest.fixture(autouse=True)
def internal_token(monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", HEADERS["X-Internal-Token"])


def test_lookup_requires_internal_token(client):
    response = client.post(LOOKUP, json={"user_ids": [str(uuid4())]})
    assert (response.status_code, response.json()["error"]["code"]) == (401, "UNAUTHORIZED")


@pytest.mark.parametrize("kind", ["empty", "too_many", "extra"])
def test_lookup_validates_request(client, kind):
    body = {"user_ids": [str(uuid4())]}
    if kind == "empty":
        body["user_ids"] = []
    elif kind == "too_many":
        body["user_ids"] *= 101
    else:
        body["extra"] = True
    response = client.post(LOOKUP, headers=HEADERS, json=body)
    assert (response.status_code, response.json()["error"]["code"]) == (422, "VALIDATION_ERROR")


def test_lookup_returns_deleted_users_releases_expired_locks_and_loads_in_one_select(client, db, citizen):
    active_id = str(citizen.id)
    citizen.status = UserStatus.locked
    citizen.locked_until = datetime.now(timezone.utc) - timedelta(minutes=1)
    citizen.locked_reason = "Tạm khóa"
    deleted = _make(db, "deleted_lookup", deleted_at=datetime.now(timezone.utc))
    deleted_id = str(deleted.id)
    engine = db.get_bind()
    selects = []

    def count_users_select(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and re.search(r"\bFROM\s+users\b", statement, re.I):
            selects.append(statement)

    event.listen(engine, "before_cursor_execute", count_users_select)
    try:
        response = client.post(LOOKUP, headers=HEADERS, json={"user_ids": [deleted_id, str(uuid4()), active_id]})
    finally:
        event.remove(engine, "before_cursor_execute", count_users_select)
    assert response.status_code == 200, response.text
    items = {item["id"]: item for item in response.json()["data"]["items"]}
    assert set(items) == {active_id, deleted_id}
    assert items[deleted_id]["deleted_at"] is not None
    assert items[active_id]["status"] == "active"
    assert items[active_id]["locked_until"] is None and items[active_id]["locked_reason"] is None
    assert len(selects) == 1


@pytest.mark.parametrize(("reason", "kwargs", "expected"), [
    ("role_changed", {}, "role_changed"),
    ("deleted", {}, "deleted"),
    ("Spam nhiều lần", {}, "locked"),
    ("deleted", {"reason_code": "locked"}, "locked"),
])
def test_user_blocked_publisher_sets_reason_code(monkeypatch, reason, kwargs, expected):
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(publisher, "publish", send)
    assert asyncio.run(admin_mq.publish_user_blocked("user", "admin", reason, **kwargs)) is True
    send.assert_awaited_once()
    routing_key, payload = send.await_args.args
    assert routing_key == "user.blocked"
    assert payload["reason"] == reason and payload["reason_code"] == expected


def test_lock_endpoint_sets_locked_reason_code_even_if_free_text_is_reserved(client, citizen, admin, monkeypatch):
    publish = AsyncMock(return_value=True)
    monkeypatch.setattr(admin_mq, "publish_user_blocked", publish)
    response = client.post(f"{USERS}/{citizen.id}/lock", headers=_h(admin), json={"reason": "deleted"})
    assert response.status_code == 200, response.text
    publish.assert_awaited_once_with(
        user_id=str(citizen.id), admin_id=str(admin.id), reason="deleted", duration_days=None, reason_code="locked",
    )
