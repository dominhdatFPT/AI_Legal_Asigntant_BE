import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from threading import Barrier, Event
from uuid import uuid4

import aio_pika
import pytest
from sqlalchemy import MetaData, create_engine, event, func, select

from common_auth import events, outbox
from tests_3layer.layer2_cross_service.db_bootstrap import database_url
from tests_3layer.support import L2_AMQP

BULK = "test.outbox.delivery"
BLOCKED = "user.blocked"


@pytest.fixture
def state():
    url = database_url("user_db").replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url)
    table = outbox.define_table(MetaData(), schema="public")
    prefix = f"test-{uuid4().hex}-"
    try:
        yield engine, table, prefix
    finally:
        with engine.begin() as db:
            db.execute(table.delete().where(table.c.id.startswith(prefix)))
        engine.dispose()


def insert(state, key, payloads, *, start=0):
    engine, table, prefix = state
    with engine.begin() as db:
        return [
            outbox.enqueue(db, table, key, payload, event_id=f"{prefix}{index}")
            for index, payload in enumerate(payloads, start=start)
        ]


def status(state, ids):
    engine, table, _ = state
    with engine.connect() as db:
        return dict(db.execute(select(table.c.id, table.c.published_at).where(table.c.id.in_(ids))).all())


@asynccontextmanager
async def inbox(key):
    connection = await aio_pika.connect(L2_AMQP, timeout=3)
    async with connection:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(events.EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
        queue = await channel.declare_queue(exclusive=True)
        deliveries = asyncio.Queue()

        async def consume(message):
            async with message.process():
                envelope = json.loads(message.body)
                assert message.message_id == envelope["event_id"]
                deliveries.put_nowait(envelope)

        await queue.bind(exchange, key)
        await queue.consume(consume)
        yield deliveries


async def no_duplicate(deliveries):
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(deliveries.get(), 0.3)


def test_two_real_relays_claim_disjoint_rows_under_lock(state):
    engine, table, _ = state
    ids = insert(state, BULK, ({"sequence": i} for i in range(40)))
    rendezvous = Barrier(2, timeout=8)
    claims = []

    def hold_claim(open_conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement:
            claims.append(statement)
            rendezvous.wait()

    async def scenario():
        relays = [outbox.OutboxRelay(lambda: engine, table, batch_size=20) for _ in range(2)]
        try:
            async with inbox(BULK) as deliveries:
                event.listen(engine, "after_cursor_execute", hold_claim)
                try:
                    assert await asyncio.wait_for(asyncio.gather(*(relay.run_once() for relay in relays)), 15) == [20, 20]
                finally:
                    event.remove(engine, "after_cursor_execute", hold_claim)
                received = [await asyncio.wait_for(deliveries.get(), 4) for _ in ids]
                assert len(claims) == 2 and all("SKIP LOCKED" in claim for claim in claims)
                assert {item["event_id"]: item["payload"]["sequence"] for item in received} == {
                    eid: index for index, eid in enumerate(ids)
                }
                assert len(status(state, ids)) == 40 and all(status(state, ids).values())
                await no_duplicate(deliveries)
        finally:
            await asyncio.gather(*(relay._transport.close() for relay in relays))

    asyncio.run(scenario())


def test_failed_block_for_a_cannot_be_overtaken_and_b_makes_progress(state):
    engine, table, _ = state
    user_a, user_b = str(uuid4()), str(uuid4())
    first, = insert(state, BLOCKED, [{"user_id": user_a, "reason_code": "locked", "order": 1}])

    class FailFirst(outbox.RabbitTransport):
        def __init__(self):
            super().__init__()
            self.failed = False

        async def send(self, key, envelope):
            if envelope["event_id"] == first and not self.failed:
                self.failed = True
                return False
            return await super().send(key, envelope)

    async def scenario():
        transport = FailFirst()
        a = outbox.OutboxRelay(lambda: engine, table, transport, lease_seconds=30)
        b = outbox.OutboxRelay(lambda: engine, table, lease_seconds=30)
        try:
            async with inbox(BLOCKED) as deliveries:
                assert await a.run_once() == 0 and transport.failed
                second, independent = insert(state, BLOCKED, [
                    {"user_id": user_a, "reason_code": "locked", "order": 2},
                    {"user_id": user_b, "reason_code": "locked", "order": 3},
                ], start=1)
                assert await b.run_once() == 1
                assert (await asyncio.wait_for(deliveries.get(), 4))["event_id"] == independent
                assert status(state, [first, second]) == {first: None, second: None}
                await no_duplicate(deliveries)
                with engine.begin() as db:
                    db.execute(table.update().where(table.c.id == first).values(available_at=func.now()))
                assert await a.run_once() == 1
                assert await b.run_once() == 1
                received = [await asyncio.wait_for(deliveries.get(), 4) for _ in range(2)]
                assert [item["event_id"] for item in received] == [first, second]
                assert received[0]["timestamp"] < received[1]["timestamp"]
                assert all(status(state, [first, second, independent]).values())
                await no_duplicate(deliveries)
        finally:
            await asyncio.gather(transport.close(), b._transport.close())

    asyncio.run(scenario())


def test_shutdown_preserves_confirmation_while_db_update_is_pending(state):
    engine, table, _ = state
    eid, = insert(state, BULK, [{"shutdown": True}])
    writing, release = Event(), Event()

    def stop_before_update(conn, cursor, statement, params, context, executemany):
        if statement.startswith("UPDATE") and "SET published_at=" in statement:
            writing.set()
            if not release.wait(8):
                raise TimeoutError("Test did not release the outbox timestamp write")

    async def scenario():
        relay = outbox.OutboxRelay(lambda: engine, table, poll_seconds=1)
        async with inbox(BULK) as deliveries:
            event.listen(engine, "before_cursor_execute", stop_before_update)
            task = asyncio.create_task(relay.run_forever())
            shutdown = None
            try:
                assert (await asyncio.wait_for(deliveries.get(), 4))["event_id"] == eid
                assert await asyncio.to_thread(writing.wait, 5)
                assert status(state, [eid]) == {eid: None}
                shutdown = asyncio.create_task(relay.shutdown(task))
                await asyncio.sleep(0)
                assert not shutdown.done()
            finally:
                release.set()
                try:
                    if shutdown:
                        await asyncio.wait_for(shutdown, 8)
                    else:
                        await relay.shutdown(task)
                finally:
                    event.remove(engine, "before_cursor_execute", stop_before_update)
            assert status(state, [eid])[eid] is not None
            await no_duplicate(deliveries)

    asyncio.run(scenario())


def test_recovered_older_block_is_retired_after_newer_confirmation(state):
    engine, table, prefix = state
    subject = str(uuid4())
    payload = {"user_id": subject, "reason_code": "locked"}
    newer, = insert(state, BLOCKED, [{**payload, "order": 2}], start=1)
    older = f"{prefix}0"

    async def scenario():
        relay = outbox.OutboxRelay(lambda: engine, table)
        try:
            async with inbox(BLOCKED) as deliveries:
                assert await relay.run_once() == 1
                latest = await asyncio.wait_for(deliveries.get(), 4)
                assert latest["event_id"] == newer
                previous = events.make_event(BLOCKED, {**payload, "order": 1}, event_id=older)
                created_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
                previous["timestamp"] = created_at.isoformat()
                with engine.begin() as db:
                    db.execute(table.insert().values(
                        id=older, routing_key=BLOCKED, payload_json=json.dumps(previous),
                        created_at=created_at, available_at=func.now(),
                    ))
                assert await relay.run_once() == 0
                assert status(state, [older]) == {}
                await no_duplicate(deliveries)
        finally:
            await relay._transport.close()

    asyncio.run(scenario())
