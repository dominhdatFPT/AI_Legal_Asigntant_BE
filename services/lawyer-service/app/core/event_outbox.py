from common_auth import outbox

from app.core.database import Base, get_engine

EVENT_OUTBOX = outbox.define_table(Base.metadata)
relay = outbox.OutboxRelay(get_engine, EVENT_OUTBOX)
