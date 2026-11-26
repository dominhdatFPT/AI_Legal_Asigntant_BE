import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from aiormq import spec
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.pool import StaticPool

from common_auth import events, outbox


@pytest.fixture
def storage():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    table = outbox.define_table(MetaData())
    table.create(engine)
    try:
        yield engine, table
    finally:
        engine.dispose()


def _enqueue(storage, **kwargs):
    engine, table = storage
    with engine.begin() as conn:
        return outbox.enqueue(conn, table, "test.event", {"value": 1}, **kwargs)


def _rows(storage):
    engine, table = storage
    with engine.connect() as conn:
        return conn.execute(select(table)).all()


def test_enqueue_uses_callers_transaction(storage):
    engine, table = storage
    with engine.connect() as conn:
        outbox.enqueue(conn, table, "test.event", {})
        conn.rollback()
    assert _rows(storage) == []
    event_id = _enqueue(storage, event_id="evt-explicit")
    row, = _rows(storage)
    assert row.id == event_id == "evt-explicit"
    assert json.loads(row.payload_json)["event_id"] == row.id


def test_run_once_sends_stored_envelope_and_marks_published(storage):
    engine, table = storage
    _enqueue(storage, request_id="req-outbox")
    stored, = _rows(storage)
    transport = Mock(send=AsyncMock(return_value=True))
    relay = outbox.OutboxRelay(lambda: engine, table, transport)
    assert asyncio.run(relay.run_once()) == 1
    transport.send.assert_awaited_once_with("test.event", json.loads(stored.payload_json))
    row, = _rows(storage)
    assert row.published_at is not None
    assert row.attempts == 1


@pytest.mark.parametrize("raises", [False, True])
def test_failed_send_is_leased_and_next_run_skips_it(storage, raises):
    engine, table = storage
    _enqueue(storage)
    transport = Mock(send=AsyncMock(return_value=False, side_effect=RuntimeError("failed") if raises else None))
    relay = outbox.OutboxRelay(lambda: engine, table, transport)

    async def run():
        assert await relay.run_once() == 0
        assert await relay.run_once() == 0

    asyncio.run(run())
    transport.send.assert_awaited_once()
    row, = _rows(storage)
    assert row.attempts == 1
    assert row.published_at is None


@pytest.mark.parametrize("same_batch", [False, True])
def test_blocked_retry_preserves_per_user_fifo(storage, same_batch):
    engine, table = storage

    def blocked(event_id, user_id):
        with engine.begin() as conn:
            outbox.enqueue(conn, table, "user.blocked", {"user_id": user_id}, event_id=event_id)

    blocked("1", "user-a")
    if same_batch:
        blocked("2", "user-a")
        blocked("3", "user-b")
    transport = Mock(send=AsyncMock(side_effect=[False, True, True, True]))
    relay = outbox.OutboxRelay(lambda: engine, table, transport)

    async def run():
        assert await relay.run_once() == int(same_batch)
        if not same_batch:
            blocked("2", "user-a")
            blocked("3", "user-b")
            # A different relay still sees the leased predecessor and can publish for user-b.
            retry = outbox.OutboxRelay(lambda: engine, table, transport)
            assert await retry.run_once() == 1
        rows = {row.id: row for row in _rows(storage)}
        assert rows["1"].attempts == 1 and rows["1"].published_at is None
        assert rows["2"].attempts == 0 and rows["2"].published_at is None
        assert rows["3"].published_at is not None
        with engine.begin() as conn:
            conn.execute(table.update().where(table.c.id == "1").values(available_at=datetime.now(timezone.utc)))
        assert await relay.run_once() == 1
        assert await relay.run_once() == 1

    asyncio.run(run())
    assert [call.args[1]["event_id"] for call in transport.send.await_args_list] == ["1", "3", "1", "2"]
    assert all(row.published_at is not None for row in _rows(storage))


@pytest.mark.parametrize("age_days", [2, 8])
def test_blocked_does_not_publish_a_legacy_row_superseded_by_a_newer_confirmation(storage, age_days):
    engine, table = storage
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        payload = {"user_id": "user-a"}
        # Legacy UUID-based IDs need not sort in creation order.
        outbox.enqueue(conn, table, "user.blocked", payload, event_id="evt-ff")
        outbox.enqueue(conn, table, "user.blocked", payload, event_id="evt-00")
        conn.execute(table.update().where(table.c.id == "evt-ff").values(
            available_at=now - timedelta(days=age_days), created_at=now - timedelta(days=age_days),
        ))
        conn.execute(table.update().where(table.c.id == "evt-00").values(
            published_at=now, created_at=now - timedelta(days=1),
        ))
    transport = Mock(send=AsyncMock())

    assert asyncio.run(outbox.OutboxRelay(lambda: engine, table, transport).run_once()) == 0
    transport.send.assert_not_awaited()
    rows = {row.id: row.published_at for row in _rows(storage)}
    if age_days == 8:
        assert set(rows) == {"evt-00"}
    else:
        assert set(rows) == {"evt-ff", "evt-00"}
        assert rows["evt-ff"] is not None


def test_prune_removes_old_published_rows(storage):
    engine, table = storage
    old_id = _enqueue(storage)
    fresh_id = _enqueue(storage)
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(table.update().where(table.c.id == old_id).values(published_at=now - timedelta(days=8)))
        conn.execute(table.update().where(table.c.id == fresh_id).values(published_at=now))
    assert asyncio.run(outbox.OutboxRelay(lambda: engine, table).run_once()) == 0
    assert [row.id for row in _rows(storage)] == [fresh_id]


def test_rabbit_transport_without_url_keeps_outbox_row(monkeypatch, storage):
    monkeypatch.delenv("RABBITMQ_URL", raising=False)
    engine, table = storage
    _enqueue(storage)
    assert asyncio.run(outbox.OutboxRelay(lambda: engine, table).run_once()) == 0
    row, = _rows(storage)
    assert row.published_at is None and row.attempts == 1


@pytest.mark.parametrize("confirmation", [spec.Basic.Ack(delivery_tag=1), False, RuntimeError("NO_ROUTE")])
def test_outbox_requires_confirmation_from_target_queue(monkeypatch, storage, confirmation):
    monkeypatch.setenv("RABBITMQ_ENABLED", "true")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://broker.test/")
    engine, table = storage
    with engine.begin() as conn:
        outbox.enqueue(conn, table, "procedure.purged", {}, request_id="req-\r\ntrace")
    topic = SimpleNamespace(publish=AsyncMock())
    target = SimpleNamespace(publish=AsyncMock(
        return_value=confirmation, side_effect=confirmation if isinstance(confirmation, Exception) else None,
    ))
    transport = outbox.RabbitTransport(target_queues={"procedure.purged": events.PROCEDURE_PURGE_QUEUE})
    publisher = transport._publisher

    async def connect(url):
        publisher._channel = SimpleNamespace(default_exchange=target)
        return topic

    monkeypatch.setattr(publisher, "_get_exchange", connect)
    relay = outbox.OutboxRelay(lambda: engine, table, transport)
    success = isinstance(confirmation, spec.Basic.Ack)
    assert asyncio.run(relay.run_once()) == int(success)
    row, = _rows(storage)
    assert (row.published_at is not None) == success
    topic.publish.assert_not_awaited()
    message, = target.publish.await_args.args
    assert target.publish.await_args.kwargs == {"routing_key": events.PROCEDURE_PURGE_QUEUE, "mandatory": True}
    assert message.correlation_id == "req-trace"
    assert message.headers == {"x-request-id": "req-trace", "x-original-routing-key": "procedure.purged"}


def test_kick_from_thread_wakes_relay(storage):
    engine, table = storage

    async def run():
        initial_poll = asyncio.Event()
        delivered = asyncio.Event()

        async def send(routing_key, envelope):
            assert envelope["event_type"] == routing_key
            return True

        relay = outbox.OutboxRelay(lambda: engine, table, Mock(send=send), poll_seconds=60)
        original = relay.run_once

        async def observed_poll():
            count = await original()
            initial_poll.set()
            if count:
                delivered.set()
            return count

        relay.run_once = observed_poll
        task = asyncio.create_task(relay.run_forever())
        try:
            await asyncio.wait_for(initial_poll.wait(), timeout=2)
            await asyncio.to_thread(_enqueue, storage)
            await asyncio.to_thread(relay.kick)
            await asyncio.wait_for(delivered.wait(), timeout=2)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())


@pytest.mark.parametrize(("value", "expected"), [(None, False), ("0", False), ("false", False),
                                                ("1", True), ("TRUE", True), (" yes ", True)])
def test_outbox_enabled_reads_environment(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("OUTBOX_ENABLED", raising=False)
    else:
        monkeypatch.setenv("OUTBOX_ENABLED", value)
    assert outbox.enabled() is expected


@pytest.mark.parametrize(("value", "expected"), [
    ("invalid", 30.0), ("-2", 0.2), ("0", 0.2), ("NaN", 30.0), ("inf", 30.0), ("-inf", 30.0), ("1.5", 1.5),
])
def test_poll_interval_validates_environment(monkeypatch, storage, value, expected):
    monkeypatch.setenv("OUTBOX_POLL_SECONDS", value)
    engine, table = storage
    assert outbox.OutboxRelay(lambda: engine, table)._poll_seconds == expected


@pytest.mark.parametrize("times_out", [False, True])
def test_cancel_drains_db_work_before_pool_disposal(monkeypatch, storage, times_out):
    engine, table = storage
    transport = outbox.RabbitTransport()
    close = AsyncMock()
    monkeypatch.setattr(transport, "close", close)
    relay = outbox.OutboxRelay(lambda: engine, table, transport)
    release, disposed = Event(), Event()
    claim_calls = []

    def claim(_):
        claim_calls.append("started")
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        claim_calls.append("finished")
        return []

    def dispose():
        assert claim_calls == ["started", "finished"]
        disposed.set()

    monkeypatch.setattr(relay, "_claim", claim)
    if times_out:
        monkeypatch.setattr(outbox, "_DB_SHUTDOWN_TIMEOUT", 0.01)

    async def run():
        nonlocal loop, started
        loop, started = asyncio.get_running_loop(), asyncio.Event()
        task = asyncio.create_task(relay.run_forever())
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            relay.stop()
            task.cancel()
            if not times_out:
                await asyncio.sleep(0)
                assert not task.done()
                close.assert_not_awaited()
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(task), timeout=2)
            close.assert_awaited_once()
            relay.dispose_engine_when_idle(lambda: SimpleNamespace(dispose=dispose))
            assert disposed.is_set() != times_out
        finally:
            release.set()
        assert await asyncio.to_thread(disposed.wait, 2)
        assert claim_calls == ["started", "finished"]

    loop = started = None
    asyncio.run(run())


def test_stop_wakes_idle_relay_without_starting_another_batch(storage):
    engine, table = storage
    relay = outbox.OutboxRelay(lambda: engine, table, poll_seconds=60)

    async def run():
        first_batch = asyncio.Event()

        async def run_once():
            first_batch.set()
            return 0

        relay.run_once = AsyncMock(side_effect=run_once)
        task = asyncio.create_task(relay.run_forever())
        await asyncio.wait_for(first_batch.wait(), timeout=2)
        relay.stop()
        await asyncio.wait_for(task, timeout=2)
        relay.run_once.assert_awaited_once()

    asyncio.run(run())


@pytest.mark.parametrize("routing_key", ["test.event", "user.blocked"])
def test_shutdown_waits_for_broker_confirmation_and_persists_it(monkeypatch, storage, routing_key):
    engine, table = storage
    with engine.begin() as conn:
        outbox.enqueue(conn, table, routing_key, {"user_id": "user-a"})

    async def run():
        publishing, stopping, confirm = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def send(*_):
            publishing.set()
            await confirm.wait()
            return True

        relay = outbox.OutboxRelay(lambda: engine, table, Mock(send=send), poll_seconds=60)
        original_stop = relay.stop

        def stop():
            original_stop()
            stopping.set()

        monkeypatch.setattr(relay, "stop", stop)
        task = asyncio.create_task(relay.run_forever())
        await asyncio.wait_for(publishing.wait(), timeout=2)
        shutdown = asyncio.create_task(relay.shutdown(task, timeout=2))
        await asyncio.wait_for(stopping.wait(), timeout=2)
        assert not shutdown.done()
        assert _rows(storage)[0].published_at is None
        confirm.set()
        await shutdown
        assert task.done() and not task.cancelled()
        assert _rows(storage)[0].published_at is not None

    asyncio.run(run())


def test_shutdown_timeout_cancels_unconfirmed_send_and_saves_prior_confirmation(storage):
    engine, table = storage
    confirmed_id = _enqueue(storage)
    pending_id = _enqueue(storage)

    async def run():
        waiting = asyncio.Event()

        async def send(_, envelope):
            if envelope["event_id"] == confirmed_id:
                return True
            waiting.set()
            await asyncio.Future()

        relay = outbox.OutboxRelay(lambda: engine, table, Mock(send=send))
        task = asyncio.create_task(relay.run_forever())
        await asyncio.wait_for(waiting.wait(), timeout=2)
        await relay.shutdown(task, timeout=0.01)
        assert task.cancelled()
        rows = {row.id: row for row in _rows(storage)}
        assert rows[confirmed_id].published_at is not None
        assert rows[pending_id].published_at is None

    asyncio.run(run())


def test_stop_during_engine_initialization_does_not_claim_work(monkeypatch, storage):
    engine, table = storage
    claim = Mock()

    async def run():
        started = asyncio.Event()
        release = Event()
        loop = asyncio.get_running_loop()

        def engine_factory():
            loop.call_soon_threadsafe(started.set)
            assert release.wait(5)
            return engine

        relay = outbox.OutboxRelay(engine_factory, table)
        monkeypatch.setattr(relay, "_claim", claim)
        task = asyncio.create_task(relay.run_forever())
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            relay.stop()
        finally:
            release.set()
        await asyncio.wait_for(task, timeout=2)
        claim.assert_not_called()

    asyncio.run(run())


@pytest.mark.skipif(not os.getenv("RABBITMQ_TEST_URL"), reason="requires disposable RabbitMQ")
def test_real_broker_does_not_confirm_purge_until_target_exists(monkeypatch, storage):
    import aio_pika

    broker_url = os.environ["RABBITMQ_TEST_URL"]
    monkeypatch.setenv("RABBITMQ_URL", broker_url)
    monkeypatch.setenv("RABBITMQ_ENABLED", "true")
    engine, table = storage
    target_name = f"q.test.outbox.{uuid4().hex}"
    with engine.begin() as conn:
        event_id = outbox.enqueue(conn, table, "procedure.purged", {})

    async def run():
        transport = outbox.RabbitTransport(target_queues={"procedure.purged": target_name})
        try:
            async with await aio_pika.connect(broker_url) as connection:
                channel = await connection.channel()
                exchange = await channel.declare_exchange(events.EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
                observer = await channel.declare_queue(exclusive=True)
                await observer.bind(exchange, routing_key="procedure.#")
                relay = outbox.OutboxRelay(lambda: engine, table, transport, lease_seconds=0)
                assert await relay.run_once() == 0
                assert _rows(storage)[0].published_at is None
                assert await observer.get(no_ack=True, fail=False) is None

                target = await channel.declare_queue(target_name, exclusive=True)
                # Start a fresh publisher to retry immediately without waiting for its failure backoff.
                retry = outbox.RabbitTransport(target_queues={"procedure.purged": target_name})
                try:
                    assert await outbox.OutboxRelay(lambda: engine, table, retry).run_once() == 1
                    assert _rows(storage)[0].published_at is not None
                    message = await target.get(no_ack=True, fail=True)
                    assert json.loads(message.body)["event_id"] == event_id
                    assert message.headers["x-original-routing-key"] == "procedure.purged"
                finally:
                    await retry.close()
        finally:
            await transport.close()

    asyncio.run(run())
