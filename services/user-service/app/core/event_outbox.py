import logging

from common_auth import outbox

from app.core.database import Base, get_engine

logger = logging.getLogger(__name__)
EVENT_OUTBOX = outbox.define_table(Base.metadata)
relay = outbox.OutboxRelay(get_engine, EVENT_OUTBOX)


def enqueue_and_commit(db, routing_key, payload, *, request_id=None) -> bool:
    """Commit event separately after the state change.

    A crash between commits can lose the event. Atomic delivery would require
    enqueueing in the state transaction in the service layer.
    """
    try:
        outbox.enqueue(db, EVENT_OUTBOX, routing_key, payload, request_id=request_id)
        db.commit()
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            logger.warning("Outbox rollback failed for %s", routing_key)
        logger.warning("Outbox insert failed for %s (%s)", routing_key, type(exc).__name__)
        return False
    try:
        relay.kick()
    except RuntimeError:
        logger.warning("Outbox wakeup failed for %s; the relay will pick it up on its next poll", routing_key)
    return True
