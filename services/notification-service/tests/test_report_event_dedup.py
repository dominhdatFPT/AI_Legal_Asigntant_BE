import asyncio
import signal
from unittest.mock import AsyncMock

import pytest

from app import main, notification_event_worker, report_enrichment_worker
from app.services import report_service


@pytest.mark.parametrize("confirmed", [True, False])
def test_report_fallback_uses_the_same_envelope_only_when_publish_fails(monkeypatch, confirmed):
    publish = AsyncMock(return_value=confirmed)
    handle = AsyncMock()
    monkeypatch.setattr(report_service.publisher, "publish_envelope", publish)
    monkeypatch.setattr(report_service, "handle_event", handle)
    payload = {"report_id": "report-1"}
    asyncio.run(report_service._emit_report_event("report.created", payload))

    publish.assert_awaited_once()
    key, event = publish.await_args.args
    assert key == event["event_type"] == "report.created"
    assert event["event_id"].startswith("evt-rpt-")
    assert event["payload"] == payload
    if confirmed:
        handle.assert_not_awaited()
    else:
        handle.assert_awaited_once_with(key, event)
        assert handle.await_args.args[1] is event


def test_notification_worker_starts_hub_and_enrichment(monkeypatch):
    hub, enrichment = AsyncMock(), AsyncMock()
    monkeypatch.setattr(notification_event_worker, "start_notification_hub", hub)
    monkeypatch.setattr(notification_event_worker, "start_enrichment_worker", enrichment)
    asyncio.run(notification_event_worker._run())
    hub.assert_awaited_once_with()
    enrichment.assert_awaited_once_with()


@pytest.mark.parametrize("uses_loop_handlers", [True, False])
def test_notification_worker_drains_both_consumers_on_sigterm(monkeypatch, uses_loop_handlers):
    async def run():
        loop = asyncio.get_running_loop()
        handlers = {}
        loop_handlers_removed = []
        previous = {signal.SIGTERM: signal.SIG_DFL, signal.SIGINT: signal.default_int_handler}
        started = asyncio.Event()
        active, cleaned_up = set(), set()

        def add_signal_handler(signum, handler):
            if not uses_loop_handlers:
                raise NotImplementedError
            handlers[signum] = handler

        async def consumer(name):
            active.add(name)
            if len(active) == 2:
                started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                await asyncio.sleep(0)
                cleaned_up.add(name)
                raise

        with monkeypatch.context() as patches:
            patches.setattr(loop, "add_signal_handler", add_signal_handler)
            patches.setattr(loop, "remove_signal_handler", loop_handlers_removed.append)
            patches.setattr(signal, "getsignal", previous.__getitem__)
            patches.setattr(signal, "signal", handlers.__setitem__)
            patches.setattr(notification_event_worker, "start_notification_hub", lambda: consumer("hub"))
            patches.setattr(notification_event_worker, "start_enrichment_worker", lambda: consumer("enrichment"))

            worker = asyncio.create_task(notification_event_worker._run())
            await asyncio.wait_for(started.wait(), timeout=2)
            assert set(handlers) == {signal.SIGTERM, signal.SIGINT}
            if uses_loop_handlers:
                handlers[signal.SIGTERM]()
            else:
                handlers[signal.SIGTERM](signal.SIGTERM, None)
            await asyncio.wait_for(worker, timeout=2)
            assert cleaned_up == {"hub", "enrichment"}
            assert handlers == previous
            assert set(loop_handlers_removed) == (set(previous) if uses_loop_handlers else set())

    asyncio.run(run())


@pytest.mark.parametrize("flag", [None, " TrUe "])
def test_api_enrichment_is_opt_in_and_cancelled_on_shutdown(monkeypatch, flag):
    monkeypatch.setenv("RUN_NOTIFICATION_HUB_IN_API", "false")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(main, "get_engine", lambda: None)
    monkeypatch.setattr(main, "close_http_client", AsyncMock())
    monkeypatch.setattr(main.realtime_broker, "close", lambda: None)
    if flag is None:
        monkeypatch.delenv("RUN_REPORT_ENRICHMENT_IN_API", raising=False)
    else:
        monkeypatch.setenv("RUN_REPORT_ENRICHMENT_IN_API", flag)
    cancelled = []

    async def enrich():
        try:
            await asyncio.Future()
        finally:
            cancelled.append(True)

    worker = AsyncMock(side_effect=enrich)
    monkeypatch.setattr(report_enrichment_worker, "start_enrichment_worker", worker)

    async def run():
        async with main.lifespan(main.app):
            await asyncio.sleep(0)
            if flag is None:
                worker.assert_not_awaited()
            else:
                worker.assert_awaited_once_with()

    asyncio.run(run())
    assert cancelled == ([True] if flag is not None else [])
