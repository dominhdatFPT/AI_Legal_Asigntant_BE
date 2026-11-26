from sqlalchemy import MetaData

from common_auth import events, outbox

from app.database import get_engine

EVENT_OUTBOX = outbox.define_table(MetaData(), schema="public")
# A confirm on the topic could come from an unrelated wildcard subscriber.
relay = outbox.OutboxRelay(
    get_engine, EVENT_OUTBOX,
    outbox.RabbitTransport(target_queues={"procedure.purged": events.PROCEDURE_PURGE_QUEUE}),
)
