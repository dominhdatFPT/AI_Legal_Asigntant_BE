import asyncio
from datetime import timedelta, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.consultation import CLOSED, DIRECT, IN_PROGRESS, LOBBY, WAITING, Consultation, utcnow
from app.services import user_events


def _consultation(lawyer_id=None):
    return Consultation(
        id=uuid4(), citizen_user_id=uuid4(), lawyer_user_id=lawyer_id,
        source=DIRECT if lawyer_id else LOBBY, question="Tư vấn thủ tục",
        status=IN_PROGRESS if lawyer_id else WAITING, accepted_at=utcnow() if lawyer_id else None,
    )


@pytest.mark.parametrize(("payload", "reason"), [
    ({"reason_code": "locked", "reason": "deleted"}, "locked"),
    ({"reason_code": "role_changed"}, "role_changed"),
    ({"reason": "role_changed"}, "role_changed"),
    ({"reason_code": "invalid", "reason": "deleted"}, "deleted"),
    ({"reason_code": "deleted"}, "deleted"),
    ({"reason": "Spam nhiều lần"}, "locked"),
])
def test_reason_of_prefers_valid_reason_code_and_handles_legacy_events(payload, reason):
    assert user_events.reason_of(payload) == reason


def test_close_for_user_closes_only_open_consultations_for_that_lawyer(db):
    lawyer_id = uuid4()
    opened = [_consultation(lawyer_id), _consultation(lawyer_id)]
    waiting = _consultation()
    other = _consultation(uuid4())
    db.add_all([*opened, waiting, other])
    db.commit()

    views = user_events.close_for_user(db, lawyer_id, "deleted")
    assert {view["id"] for view in views} == {row.id for row in opened}
    assert all(view["status"] == CLOSED and view["close_reason"] == "deleted"
               and view["closed_by"] is None and view["closed_at"] is not None for view in views)
    db.refresh(waiting)
    db.refresh(other)
    assert (waiting.status, other.status) == (WAITING, IN_PROGRESS)
    assert user_events.close_for_user(db, lawyer_id, "locked") == []


def test_handler_publishes_closed_view_to_chat_and_both_users(db, monkeypatch):
    lawyer_id = uuid4()
    consultation = _consultation(lawyer_id)
    db.add(consultation)
    db.commit()

    session = Session(db.get_bind(), expire_on_commit=False)
    close = Mock(wraps=session.close)
    monkeypatch.setattr(session, "close", close)
    monkeypatch.setattr(user_events.database, "new_session", lambda: session)
    publish = AsyncMock()
    monkeypatch.setattr(user_events.realtime, "publish", publish)

    event = {"payload": {"user_id": str(lawyer_id), "reason_code": "role_changed"}}
    asyncio.run(user_events.handle_user_event("user.blocked", event))
    close.assert_called_once_with()
    publish.assert_awaited_once()
    event_type, view, *rooms = publish.await_args.args
    assert event_type == "consultation.closed"
    assert (view["id"], view["close_reason"], view["status"], view["unread_count"]) == (
        consultation.id, "role_changed", CLOSED, 0,
    )
    assert rooms == [f"chat:{consultation.id}", f"me:{consultation.citizen_user_id}", f"me:{lawyer_id}"]


def test_handler_ignores_unrelated_event_and_rejects_invalid_user_id(monkeypatch):
    new_session = Mock()
    monkeypatch.setattr(user_events.database, "new_session", new_session)
    asyncio.run(user_events.handle_user_event("user.logged_out", {}))
    with pytest.raises(ValueError):
        asyncio.run(user_events.handle_user_event("user.blocked", {"payload": {"user_id": "invalid"}}))
    new_session.assert_not_called()


def test_old_block_event_keeps_new_consultations_open(db, monkeypatch):
    lawyer = uuid4()
    blocked_at = utcnow() - timedelta(days=1)
    old, boundary, new, legacy_old, legacy_new = [_consultation(lawyer) for _ in range(5)]
    old.accepted_at = blocked_at - timedelta(seconds=1)
    boundary.accepted_at = blocked_at
    new.created_at = old.accepted_at
    new.accepted_at = blocked_at + timedelta(seconds=1)
    legacy_old.accepted_at = legacy_new.accepted_at = None
    legacy_old.created_at = old.accepted_at
    legacy_new.created_at = new.accepted_at
    publish = AsyncMock()
    monkeypatch.setattr(user_events.realtime, "publish", publish)
    monkeypatch.setattr(user_events.database, "new_session", lambda: Session(db.get_bind(), expire_on_commit=False))

    # Exercise the fallback for legacy rows that predate the accepted_at constraint.
    db.execute(text("PRAGMA ignore_check_constraints = ON"))
    try:
        rows = [old, boundary, new, legacy_old, legacy_new]
        db.add_all(rows)
        db.commit()
        event = {"event_id": str(uuid4()), "timestamp": blocked_at.astimezone(
            timezone(timedelta(hours=7))).isoformat(), "payload": {"user_id": str(lawyer)}}
        asyncio.run(user_events.handle_user_event("user.blocked", event))
        for row in rows:
            db.refresh(row)
        assert [row.status for row in rows] == [CLOSED, CLOSED, IN_PROGRESS, CLOSED, IN_PROGRESS]
        assert {call.args[1]["id"] for call in publish.await_args_list} == {old.id, boundary.id, legacy_old.id}
    finally:
        db.execute(text("PRAGMA ignore_check_constraints = OFF"))


def test_redelivery_retries_only_pending_realtime_updates_after_commit(db, monkeypatch):
    lawyer = uuid4()
    first, second = _consultation(lawyer), _consultation(lawyer)
    db.add_all([first, second])
    db.commit()
    new_session = Mock(side_effect=lambda: Session(db.get_bind(), expire_on_commit=False))
    monkeypatch.setattr(user_events.database, "new_session", new_session)
    publish = AsyncMock(side_effect=[None, ValueError("realtime failed"), None])
    monkeypatch.setattr(user_events.realtime, "publish", publish)
    event_id = str(uuid4())
    event = {"event_id": event_id, "timestamp": utcnow().isoformat().replace("+00:00", "Z"),
             "payload": {"user_id": str(lawyer)}}

    with pytest.raises(RuntimeError, match="notifications are pending"):
        asyncio.run(user_events.handle_user_event("user.blocked", event))
    assert event_id not in user_events._completed
    db.refresh(first)
    db.refresh(second)
    assert first.status == second.status == CLOSED
    db.rollback()

    asyncio.run(user_events.handle_user_event("user.blocked", event))
    asyncio.run(user_events.handle_user_event("user.blocked", event))
    new_session.assert_called_once_with()
    first_id, failed_id, retried_id = [call.args[1]["id"] for call in publish.await_args_list]
    assert first_id != failed_id == retried_id
    assert event_id in user_events._completed


def test_concurrent_redelivery_and_completed_cache_are_bounded(monkeypatch):
    monkeypatch.setattr(user_events, "_completed", user_events.OrderedDict())
    monkeypatch.setattr(user_events, "_MAX_COMPLETED_EVENTS", 1)
    lawyer = str(uuid4())
    view = {"id": uuid4(), "citizen_id": uuid4(), "lawyer_id": lawyer}
    close = Mock(return_value=[view])
    monkeypatch.setattr(user_events, "_close_in_session", close)

    async def run():
        publishing, release = asyncio.Event(), asyncio.Event()

        async def send(*args):
            publishing.set()
            await release.wait()

        publish = AsyncMock(side_effect=send)
        monkeypatch.setattr(user_events.realtime, "publish", publish)
        event = {"event_id": "evt-pending", "payload": {"user_id": lawyer}}
        first = asyncio.create_task(user_events.handle_user_event("user.blocked", event))
        await asyncio.wait_for(publishing.wait(), timeout=2)
        duplicate = asyncio.create_task(user_events.handle_user_event("user.blocked", event))
        await asyncio.sleep(0)
        assert "evt-pending" not in user_events._completed and not duplicate.done()
        release.set()
        await asyncio.wait_for(asyncio.gather(first, duplicate), timeout=2)
        close.assert_called_once()
        publish.assert_awaited_once()
        await user_events.handle_user_event("user.blocked", {**event, "event_id": "evt-later"})
        assert list(user_events._completed) == ["evt-later"]

    asyncio.run(run())


def test_malformed_timestamp_leaves_consultations_alone(monkeypatch):
    close = Mock()
    monkeypatch.setattr(user_events, "_close_in_session", close)
    with pytest.raises(ValueError):
        asyncio.run(user_events.handle_user_event(
            "user.blocked", {"timestamp": "broken", "payload": {"user_id": str(uuid4())}},
        ))
    close.assert_not_called()
