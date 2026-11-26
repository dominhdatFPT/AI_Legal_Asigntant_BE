import asyncio
import json
from unittest.mock import AsyncMock, Mock

import jwt
import pytest
from sqlalchemy import select

from app.core import event_outbox, rabbitmq
from app.core.rabbitmq_admin import admin_mq
from app.core.security import create_access_token
from app.main import app, firebase, lifespan
from app.models import UserStatus
from app.services import logout_worker
from app.services.auth_service import issue_token_pair
from test_security_hardening import AUTH, USERS, accounts, client  # noqa: F401


@pytest.fixture
def delivery(monkeypatch):
    monkeypatch.setenv("OUTBOX_ENABLED", "true")
    kick = Mock()
    blocked = AsyncMock()
    logged_out = AsyncMock()
    monkeypatch.setattr(event_outbox.relay, "kick", kick)
    monkeypatch.setattr(admin_mq, "publish_user_blocked", blocked)
    monkeypatch.setattr(rabbitmq, "publish_user_logged_out", logged_out)
    return kick, blocked, logged_out


@pytest.mark.parametrize(("method", "suffix", "body", "status", "expected"), [
    ("POST", "/lock", {"reason": "spam", "duration_days": 2}, 200,
     {"reason": "spam", "reason_code": "locked", "duration_days": 2}),
    ("PUT", "/role", {"role": "citizen"}, 200,
     {"reason": "role_changed", "reason_code": "role_changed", "duration_days": None, "notify_user": False}),
    ("DELETE", "", None, 204,
     {"reason": "deleted", "reason_code": "deleted", "duration_days": None, "notify_user": False}),
])
def test_admin_changes_enqueue_the_legacy_blocked_payload(client, accounts, db, delivery,
                                                         method, suffix, body, status, expected):
    admin, user = accounts
    token = create_access_token(str(admin.id), admin.role.value)
    response = client.request(method, f"{USERS}/{user.id}{suffix}", json=body, headers={
        "Authorization": f"Bearer {token}", "X-Request-ID": "req-admin-outbox",
    })
    assert response.status_code == status, response.text
    row, = db.execute(select(event_outbox.EVENT_OUTBOX)).all()
    assert row.routing_key == "user.blocked"
    event = json.loads(row.payload_json)
    assert event["event_type"] == "user.blocked"
    assert event["request_id"] == "req-admin-outbox"
    assert event["payload"] == {"user_id": str(user.id), "admin_id": str(admin.id), **expected}
    kick, blocked, logged_out = delivery
    kick.assert_called_once_with()
    blocked.assert_not_awaited()
    logged_out.assert_not_awaited()


def test_logout_enqueues_the_legacy_seven_fields(client, accounts, db, delivery):
    _, user = accounts
    pair = issue_token_pair(db, user)
    db.commit()
    access = pair["access_token"]
    claims = jwt.decode(access, options={"verify_signature": False})
    response = client.post(f"{AUTH}/logout", json={"refresh_token": pair["refresh_token"]}, headers={
        "Authorization": f"Bearer {access}", "X-Request-ID": "req-logout-outbox", "User-Agent": "outbox-test",
    })
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"logged_out": True}
    row, = db.execute(select(event_outbox.EVENT_OUTBOX)).all()
    assert row.routing_key == "user.logged_out"
    event = json.loads(row.payload_json)
    assert event["event_type"] == "user.logged_out"
    assert event["request_id"] == "req-logout-outbox"
    assert event["payload"] == {
        "user_id": str(user.id), "role": user.role.value, "jti": claims["jti"], "exp": claims["exp"],
        "client_ip": "testclient", "user_agent": "outbox-test", "request_id": "req-logout-outbox",
    }
    kick, blocked, logged_out = delivery
    kick.assert_called_once_with()
    blocked.assert_not_awaited()
    logged_out.assert_not_awaited()


def test_enqueue_failure_preserves_the_lock_and_publishes_once(client, accounts, db, delivery, monkeypatch):
    admin, user = accounts
    enqueue = Mock(side_effect=RuntimeError("outbox unavailable"))
    monkeypatch.setattr(event_outbox.outbox, "enqueue", enqueue)
    token = create_access_token(str(admin.id), admin.role.value)
    response = client.post(f"{USERS}/{user.id}/lock", json={"reason": "spam", "duration_days": 2},
                           headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    db.refresh(user)
    assert user.status == UserStatus.locked
    assert db.execute(select(event_outbox.EVENT_OUTBOX)).all() == []
    enqueue.assert_called_once()
    kick, blocked, _ = delivery
    kick.assert_not_called()
    blocked.assert_awaited_once_with(user_id=str(user.id), admin_id=str(admin.id), reason="spam",
                                     duration_days=2, reason_code="locked")


@pytest.mark.parametrize("flag", [None, "false", " YeS "])
def test_lifespan_starts_and_cancels_logout_audit_only_when_enabled(monkeypatch, flag):
    if flag is None:
        monkeypatch.delenv("RUN_LOGOUT_AUDIT_IN_API", raising=False)
    else:
        monkeypatch.setenv("RUN_LOGOUT_AUDIT_IN_API", flag)
    monkeypatch.delenv("OUTBOX_ENABLED", raising=False)
    monkeypatch.setattr(firebase, "warmup_certs", lambda: None)
    cancelled = []

    async def consume(*_):
        try:
            await asyncio.Future()
        finally:
            cancelled.append(True)

    worker = AsyncMock(side_effect=consume)
    monkeypatch.setattr(logout_worker, "consume_forever", worker)

    async def run():
        async with lifespan(app):
            await asyncio.sleep(0)
            if flag == " YeS ":
                worker.assert_awaited_once_with(logout_worker.QUEUE_AUDIT, ["user.logged_out"],
                                                logout_worker.process_audit_log)
            else:
                worker.assert_not_awaited()

    asyncio.run(run())
    assert cancelled == ([True] if flag == " YeS " else [])
