import asyncio
import json
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from common_auth import events, outbox
from sqlalchemy import select

from app import main
from app.core import event_outbox, rabbitmq_lawyer
from app.models.lawyer import APPROVED, LawyerApplication
from test_admin import LAWYERS, _pending


@pytest.mark.parametrize(("enabled", "promote_fails", "status"), [
    (True, False, 200),
    (True, True, 503),
    (False, False, 200),
])
def test_approved_event_is_committed_with_approval(client, db, upstream, auth, monkeypatch,
                                                   enabled, promote_fails, status):
    monkeypatch.setenv("OUTBOX_ENABLED", str(enabled))
    kick = Mock()
    monkeypatch.setattr(event_outbox.relay, "kick", kick)
    admin_id = upstream.user(role="admin")
    uid, application = _pending(client, upstream, auth)
    if promote_fails:
        upstream.fail["/internal/v1/users"] = "down"

    response = client.post(f"{LAWYERS}/{application['id']}/approve", headers=auth(admin_id, "admin"))
    assert response.status_code == status, response.text
    rows = db.execute(select(event_outbox.EVENT_OUTBOX)).all()
    if not enabled or promote_fails:
        assert rows == []
        kick.assert_not_called()
        return
    row, = rows
    assert row.routing_key == "lawyer.approved"
    envelope = json.loads(row.payload_json)
    assert envelope["event_type"] == row.routing_key
    assert envelope["event_id"] == row.id
    assert envelope["payload"] == rabbitmq_lawyer.approved_payload(
        application["id"], uid, "12345/TP", str(admin_id),
    )
    kick.assert_called_once_with()


@pytest.mark.parametrize("publish_fails", [False, True])
def test_enqueue_failure_rolls_back_only_outbox_and_falls_back_after_commit(
    client, db, upstream, auth, monkeypatch, publish_fails,
):
    monkeypatch.setenv("OUTBOX_ENABLED", "true")
    admin_id = upstream.user(role="admin")
    uid, application = _pending(client, upstream, auth)
    application_id = UUID(application["id"])
    original_enqueue = outbox.enqueue
    transactions_at_publish = []

    def fail_enqueue(*args):
        original_enqueue(*args)
        db.execute(event_outbox.EVENT_OUTBOX.insert().values(id="missing-required-columns"))

    def direct_publish(*_):
        transactions_at_publish.append(db.in_transaction())
        if publish_fails:
            raise RuntimeError("broker publish failed")
        return True

    publish = Mock(side_effect=direct_publish)
    kick = Mock()
    monkeypatch.setattr(outbox, "enqueue", fail_enqueue)
    monkeypatch.setattr(events, "publish_event_sync", publish)
    monkeypatch.setattr(event_outbox.relay, "kick", kick)

    response = client.post(f"{LAWYERS}/{application_id}/approve", headers=auth(admin_id, "admin"))

    assert response.status_code == 200, response.text
    assert db.scalar(select(LawyerApplication.status).where(LawyerApplication.id == application_id)) == APPROVED
    assert db.execute(select(event_outbox.EVENT_OUTBOX)).all() == []
    assert transactions_at_publish == [False]
    publish.assert_called_once_with(
        "lawyer.approved", rabbitmq_lawyer.approved_payload(application_id, uid, "12345/TP", str(admin_id)),
    )
    kick.assert_not_called()


def test_direct_publisher_uses_the_shared_approved_payload(monkeypatch):
    publish = AsyncMock(return_value=True)
    monkeypatch.setattr(rabbitmq_lawyer.publisher, "publish", publish)

    assert asyncio.run(rabbitmq_lawyer.lawyer_mq.publish_lawyer_approved("a", "u", "123", "Hanoi"))

    expected = {
        "lawyer_id": "a", "user_id": "u", "license_number": "123", "bar_association": "Hanoi", "approved_by": "admin",
    }
    assert rabbitmq_lawyer.approved_payload("a", "u", "123", None, "Hanoi") == expected
    publish.assert_awaited_once_with("lawyer.approved", expected)


def test_lifespan_stops_and_awaits_relay_before_closing_clients(monkeypatch):
    monkeypatch.setenv("OUTBOX_ENABLED", "true")
    monkeypatch.setattr(main.database, "database_url", lambda: "database-configured")
    steps = []
    stopping = asyncio.Event()

    async def relay():
        steps.append("start")
        await stopping.wait()
        await asyncio.sleep(0)
        steps.append("drained")

    def stop():
        steps.append("stop")
        stopping.set()

    monkeypatch.setattr(event_outbox.relay, "run_forever", relay)
    monkeypatch.setattr(event_outbox.relay, "stop", stop)
    monkeypatch.setattr(main.http_client, "close_shared_client", lambda: steps.append("close"))

    async def start_and_stop():
        async with main.lifespan(main.app):
            pass

    asyncio.run(start_and_stop())
    assert steps == ["start", "stop", "drained", "close"]
