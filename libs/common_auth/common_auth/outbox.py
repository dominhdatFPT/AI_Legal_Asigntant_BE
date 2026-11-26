"""Transactional event storage and an at-least-once relay."""

import asyncio
import json
import logging
import math
import os
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from sqlalchemy import JSON, Column, DateTime, Integer, String, Table, Text, case, cast, func, or_, select, tuple_
from sqlalchemy.engine import Engine

from common_auth import events

logger = logging.getLogger(__name__)
_DB_SHUTDOWN_TIMEOUT = 5.0
_USER_BLOCKED = "user.blocked"


def enabled() -> bool:
    return os.getenv("OUTBOX_ENABLED", "").strip().lower() in {"1", "true", "yes"}


def _poll_interval(value: float | None) -> float:
    try:
        seconds = float(os.getenv("OUTBOX_POLL_SECONDS", "30") if value is None else value)
    except (TypeError, ValueError):
        seconds = 30.0
    return max(0.2, seconds) if math.isfinite(seconds) else 30.0


def define_table(metadata, *, schema: str | None = None) -> Table:
    return Table(
        "event_outbox",
        metadata,
        Column("id", String(64), primary_key=True),
        Column("routing_key", String(100), nullable=False),
        Column("payload_json", Text, nullable=False),
        Column("attempts", Integer, nullable=False, server_default="0"),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("available_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("published_at", DateTime(timezone=True)),
        schema=schema,
    )


def enqueue(conn, table, routing_key: str, payload: dict, *, request_id=None, event_id=None) -> str:
    envelope = events.make_event(routing_key, payload, request_id=request_id, event_id=event_id)
    now = datetime.fromisoformat(envelope["timestamp"])
    conn.execute(table.insert().values(
        id=envelope["event_id"], routing_key=routing_key, payload_json=json.dumps(envelope, default=str),
        created_at=now, available_at=now,
    ))
    return envelope["event_id"]


def _blocked_user(columns, dialect: str):
    if dialect == "postgresql":
        user_id = cast(columns.payload_json, JSON)["payload"]["user_id"].as_string()
        return case((columns.routing_key == _USER_BLOCKED, user_id))
    return case((func.json_valid(columns.payload_json), func.json_extract(columns.payload_json, "$.payload.user_id")))


def _blocked_peer(table, dialect: str, *, older: bool):
    row, peer = table.c, table.alias().c
    # Event IDs are UUIDs by default. Use insertion time and break timestamp ties by ID.
    row_order = tuple_(row.created_at, row.id)
    peer_order = tuple_(peer.created_at, peer.id)
    return select(peer.id).where(
        row.routing_key == _USER_BLOCKED,
        peer.routing_key == row.routing_key,
        _blocked_user(peer, dialect) == _blocked_user(row, dialect),
        peer_order < row_order if older else peer_order > row_order,
        peer.published_at.is_(None) if older else peer.published_at.is_not(None),
    ).correlate(table).exists()


class Transport(Protocol):
    async def send(self, routing_key: str, envelope: dict) -> bool: ...


class RabbitTransport:
    def __init__(self, *, target_queues: dict[str, str] | None = None) -> None:
        self._publisher = events.EventPublisher()
        self._target_queues = target_queues or {}

    async def send(self, routing_key: str, envelope: dict) -> bool:
        return await self._publisher.publish_envelope(
            routing_key, envelope, mandatory=True, queue_name=self._target_queues.get(routing_key),
        )

    async def close(self) -> None:
        await self._publisher.close()


class OutboxRelay:
    def __init__(
        self, engine_factory: Callable[[], Engine | None], table, transport: Transport | None = None, *,
        batch_size=50, poll_seconds: float | None = None, lease_seconds=60.0, keep_days=7,
    ):
        self._engine_factory = engine_factory
        self._table = table
        self._transport = RabbitTransport() if transport is None else transport
        self._batch_size = batch_size
        self._poll_seconds = _poll_interval(poll_seconds)
        self._lease = timedelta(seconds=lease_seconds)
        self._retention = timedelta(days=keep_days)
        self._last_prune: datetime | None = None
        self._wakeup: tuple[asyncio.AbstractEventLoop, asyncio.Event] | None = None
        self._stopping = False
        self._db_task: asyncio.Task | None = None
        self._db_finished: Future[None] | None = None

    def stop(self) -> None:
        self._stopping = True
        self.kick()

    async def shutdown(self, task: asyncio.Task, *, timeout: float = 5.0) -> None:
        self.stop()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            if task.done():
                await task
                return
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def dispose_engine_when_idle(self, engine_factory: Callable[[], Engine | None]) -> None:
        # This callback tracks the worker itself, including when the event loop is closing.
        def dispose(_=None):
            if engine := engine_factory():
                engine.dispose()

        finished = self._db_finished
        if finished is not None:
            finished.add_done_callback(dispose)
        else:
            dispose()

    async def _run_db(self, operation, *args):
        finished = self._db_finished = Future()

        def run():
            try:
                return operation(*args)
            finally:
                finished.set_result(None)

        task = asyncio.create_task(asyncio.to_thread(run))
        self._db_task = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                self._db_task = None
                if finished.done():
                    self._db_finished = None

    async def _drain_db(self) -> None:
        task = self._db_task
        if task is None:
            return
        done, _ = await asyncio.wait({task}, timeout=_DB_SHUTDOWN_TIMEOUT)
        if not done:
            logger.warning("Outbox database operation still running after shutdown timeout")
            task.add_done_callback(self._collect_db_result)
        else:
            self._collect_db_result(task)

    def _collect_db_result(self, task: asyncio.Task) -> None:
        if self._db_task is task:
            self._db_task = None
            if self._db_finished is not None and self._db_finished.done():
                self._db_finished = None
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.warning("Outbox database operation failed during shutdown (%s)", type(error).__name__)

    def kick(self) -> None:
        if wakeup := self._wakeup:
            loop, wake = wakeup
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:
                if not loop.is_closed():
                    raise

    def _claim(self, engine):
        table = self._table
        cols = table.c
        with engine.begin() as conn:
            postgres = conn.dialect.name == "postgresql"
            # Compare against the same clock used by database-generated available_at values.
            now = func.now() if postgres else datetime.now(timezone.utc)
            stmt = (
                select(cols.id, cols.routing_key, cols.payload_json, cols.attempts)
                .where(cols.published_at.is_(None), cols.available_at <= now,
                       ~_blocked_peer(table, conn.dialect.name, older=True))
                .order_by(cols.created_at, cols.id).limit(self._batch_size)
            )
            if postgres:
                stmt = stmt.with_for_update(skip_locked=True)
            rows = conn.execute(stmt).all()
            if rows:
                conn.execute(table.update().where(cols.id.in_([row.id for row in rows])).values(
                    available_at=now + self._lease, attempts=cols.attempts + 1,
                ))
        return rows

    async def _send(self, row) -> bool:
        try:
            return await self._transport.send(row.routing_key, json.loads(row.payload_json))
        except Exception as exc:
            logger.warning("Sending outbox event %s (%s) failed (%s)", row.id, row.routing_key, type(exc).__name__)
            return False

    async def _send_blocked(self, engine, row) -> bool:
        loop = asyncio.get_running_loop()
        locked = asyncio.Event()
        confirmed: Future[bool] = Future()

        async def send_when_locked():
            sent = False
            try:
                await locked.wait()
                sent = await self._send(row)
            finally:
                confirmed.set_result(sent)

        sender = asyncio.create_task(send_when_locked())
        try:
            return await self._run_db(
                self._publish_blocked, engine, row, lambda: loop.call_soon_threadsafe(locked.set), confirmed,
            )
        finally:
            sender.cancel()
            try:
                await asyncio.gather(sender, return_exceptions=True)
            finally:
                if not confirmed.done():
                    confirmed.set_result(False)

    def _publish_blocked(self, engine, row, on_locked, confirmed: Future[bool]) -> bool:
        table = self._table
        cols = table.c
        with engine.begin() as conn:
            dialect = conn.dialect.name
            current = (cols.id == row.id, cols.published_at.is_(None), cols.attempts == row.attempts + 1)
            stmt = select(
                _blocked_peer(table, dialect, older=False).label("superseded"),
                (cols.created_at < datetime.now(timezone.utc) - self._retention).label("expired"),
            ).where(
                *current, ~_blocked_peer(table, dialect, older=True),
            )
            if dialect == "postgresql":
                stmt = stmt.with_for_update(skip_locked=True, of=table)
            status = conn.execute(stmt).one_or_none()
            if status is None:
                return False
            if status.superseded:
                if status.expired:
                    conn.execute(table.delete().where(cols.id == row.id))
                else:
                    conn.execute(table.update().where(cols.id == row.id).values(published_at=func.now()))
                logger.warning("Retired superseded user.blocked outbox event %s", row.id)
            else:
                # Keep the head locked until its confirmation is recorded, even if its lease expires.
                on_locked()
                if not confirmed.result():
                    return False
                conn.execute(table.update().where(cols.id == row.id).values(published_at=datetime.now(timezone.utc)))
        self.kick()
        return not status.superseded

    def _mark_published(self, engine, sent_ids: list[str]) -> None:
        table = self._table
        with engine.begin() as conn:
            conn.execute(table.update().where(table.c.id.in_(sent_ids)).values(
                published_at=datetime.now(timezone.utc),
            ))

    def _prune(self, engine) -> None:
        now = datetime.now(timezone.utc)
        if self._last_prune is not None and now - self._last_prune < timedelta(hours=1):
            return
        table = self._table
        with engine.begin() as conn:
            # Keep published watermarks until any older pending event has been retired.
            conn.execute(table.delete().where(table.c.published_at < now - self._retention,
                                              ~_blocked_peer(table, conn.dialect.name, older=True)))
        self._last_prune = now

    async def run_once(self) -> int:
        try:
            engine = await self._run_db(self._engine_factory)
        except Exception as exc:
            logger.warning("Outbox engine initialization failed (%s)", type(exc).__name__)
            return 0
        if engine is None:
            logger.warning("Outbox database is unavailable")
            return 0
        if self._stopping:
            return 0

        rows = await self._run_db(self._claim, engine)
        sent_ids = []
        ordered_sent = 0
        try:
            for row in rows:
                if self._stopping:
                    break
                if row.routing_key == _USER_BLOCKED:
                    ordered_sent += await self._send_blocked(engine, row)
                elif await self._send(row):
                    sent_ids.append(row.id)
        finally:
            if sent_ids:
                await self._run_db(self._mark_published, engine, sent_ids)
        if not self._stopping:
            await self._run_db(self._prune, engine)
        return len(sent_ids) + ordered_sent

    async def run_forever(self) -> None:
        wake = asyncio.Event()
        self._wakeup = (asyncio.get_running_loop(), wake)
        try:
            while not self._stopping:
                try:
                    sent = await self.run_once()
                except Exception as exc:
                    logger.warning("Outbox relay iteration failed (%s)", type(exc).__name__)
                    sent = 0
                if sent < self._batch_size and not self._stopping:
                    try:
                        await asyncio.wait_for(wake.wait(), timeout=self._poll_seconds)
                    except asyncio.TimeoutError:
                        pass
                    wake.clear()
        finally:
            self._wakeup = None
            try:
                await self._drain_db()
            finally:
                if isinstance(self._transport, RabbitTransport):
                    await self._transport.close()
                self._stopping = False
