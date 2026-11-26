"""Close open consultations when a lawyer loses account access."""

import asyncio
import logging
import uuid
from collections import OrderedDict, deque
from datetime import datetime, timezone

from common_auth import events
from sqlalchemy.orm import Session

from app.core import database
from app.models.consultation import DELETED, LOCKED, ROLE_CHANGED, Consultation, as_utc, utcnow
from app.repositories import consultation_repository as repo
from app.services import realtime, user_client
from app.services.views import consultation_view

QUEUE = "q.consultation.user_events"
ROUTING_KEY = "user.blocked"
logger = logging.getLogger(__name__)
_MAX_COMPLETED_EVENTS = 2048
_completed: OrderedDict[str, None] = OrderedDict()
_pending: dict[tuple, deque] = {}
_in_flight: dict[tuple, asyncio.Task] = {}


def reason_of(payload: dict) -> str:
    reason_code = payload.get("reason_code")
    if reason_code in (LOCKED, ROLE_CHANGED, DELETED):
        return reason_code
    reason = payload.get("reason")
    return reason if reason in (ROLE_CHANGED, DELETED) else LOCKED


def close_for_user(
    db: Session, user_id: uuid.UUID, reason: str, *, blocked_at: datetime | None = None,
) -> list[dict]:
    ids = repo.close_open_by_lawyer(db, user_id, reason, utcnow(), blocked_at=blocked_at)
    views = []
    for consultation_id in ids:
        consultation = db.get(Consultation, consultation_id, populate_existing=True)
        if consultation is not None:
            views.append(consultation_view(db, consultation, consultation.citizen_user_id))
    db.commit()
    return views


def _close_in_session(user_id: uuid.UUID, reason: str, blocked_at: datetime | None) -> list[dict]:
    db = database.new_session()
    try:
        return close_for_user(db, user_id, reason, blocked_at=blocked_at)
    finally:
        db.close()


def _blocked_at(event: dict) -> datetime | None:
    timestamp = event.get("timestamp")
    if timestamp is None:
        return None
    if not isinstance(timestamp, str):
        raise ValueError("Event timestamp must be an ISO 8601 string")
    return as_utc(datetime.fromisoformat(timestamp.replace("Z", "+00:00"))).astimezone(timezone.utc)


async def _process(key: tuple, event_id: str | None, user_id: uuid.UUID, reason: str,
                   blocked_at: datetime | None) -> None:
    views = _pending.get(key)
    if views is None:
        views = deque(await asyncio.to_thread(_close_in_session, user_id, reason, blocked_at))
        _pending[key] = views
    while views:
        view = views[0]
        try:
            await realtime.publish(
                "consultation.closed", {**view, "unread_count": 0},
                realtime.chat(view["id"]), realtime.me(view["citizen_id"]), realtime.me(view["lawyer_id"]),
            )
        except Exception as exc:
            logger.warning("Realtime publish failed for user.blocked event %s; %d updates pending",
                           event_id, len(views))
            raise RuntimeError("Consultation closure notifications are pending") from exc
        views.popleft()
    _pending.pop(key)
    if event_id:
        _completed[event_id] = None
        if len(_completed) > _MAX_COMPLETED_EVENTS:
            _completed.popitem(last=False)


def _finish(key: tuple, task: asyncio.Task) -> None:
    _in_flight.pop(key, None)
    if not task.cancelled():
        task.exception()


async def handle_user_event(routing_key: str, event: dict) -> None:
    if routing_key != ROUTING_KEY:
        return
    payload = event["payload"]
    user_id = uuid.UUID(str(payload["user_id"]))
    reason, blocked_at = reason_of(payload), _blocked_at(event)
    event_id = event.get("event_id")
    if event_id is not None and (not isinstance(event_id, str) or not event_id):
        raise ValueError("Event ID must be a nonempty string")
    if event_id in _completed:
        return
    key = (event_id,) if event_id else (user_id, reason, blocked_at)
    task = _in_flight.get(key)
    if task is None:
        user_client.forget_user(str(user_id))
        task = asyncio.create_task(_process(key, event_id, user_id, reason, blocked_at))
        _in_flight[key] = task
        task.add_done_callback(lambda finished: _finish(key, finished))
    await asyncio.shield(task)


async def start() -> None:
    await events.consume_forever(QUEUE, [ROUTING_KEY], handle_user_event)
