from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace

import pytest

from common_auth import breaker


def test_opens_after_threshold_and_only_one_thread_can_probe(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(breaker, "time", SimpleNamespace(monotonic=lambda: clock.now))
    gate = breaker.CircuitBreaker(failures=3, open_seconds=5)
    for _ in range(2):
        assert gate.allow()
        assert gate.failure() is False
    assert gate.allow()
    assert gate.failure() is True
    assert not gate.allow()

    clock.now = 5.0
    ready = Barrier(8)

    def try_probe():
        ready.wait(timeout=5)
        return gate.allow()

    with ThreadPoolExecutor(max_workers=8) as pool:
        attempts = [pool.submit(try_probe) for _ in range(8)]
        assert sum(attempt.result(timeout=5) for attempt in attempts) == 1


def test_a_failed_probe_reopens_and_a_successful_probe_resets_failure_count(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(breaker, "time", SimpleNamespace(monotonic=lambda: clock.now))
    gate = breaker.CircuitBreaker(failures=2, open_seconds=5)
    assert not gate.failure()
    assert gate.failure()
    clock.now = 5
    assert gate.allow()
    assert not gate.allow()
    assert gate.failure()
    clock.now = 9
    assert not gate.allow()
    clock.now = 10
    assert gate.allow()
    gate.success()
    assert gate.allow()
    assert not gate.failure()
    assert gate.allow()


def test_probe_timeout_allows_a_replacement_and_ignores_the_old_result(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(breaker, "time", SimpleNamespace(monotonic=lambda: clock.now))
    gate = breaker.CircuitBreaker(failures=1, open_seconds=5, probe_timeout=2)
    assert gate.failure()
    clock.now = 5
    started, finish = Event(), Event()

    def stalled_probe():
        assert gate.allow()
        started.set()
        assert finish.wait(timeout=5)
        gate.success()

    with ThreadPoolExecutor(max_workers=1) as pool:
        waiting = pool.submit(stalled_probe)
        try:
            assert started.wait(timeout=5)
            clock.now = 6
            assert not gate.allow()
            clock.now = 7
            assert gate.allow()
        finally:
            finish.set()
        waiting.result(timeout=5)
        assert not gate.allow()
        assert gate.failure()


def test_registry_and_reset_all():
    breaker.reset_all()
    try:
        gate = breaker.get("svc:USER", failures=1)
        assert breaker.get("svc:USER") is gate
        assert breaker.get("svc:OTHER") is not gate
        assert gate.failure()
        assert not gate.allow()
        breaker.reset_all()
        assert gate.allow()
        fresh = breaker.get("svc:USER", failures=2)
        assert fresh.allow()
        assert not fresh.failure()
    finally:
        breaker.reset_all()


@pytest.mark.parametrize(("configured_timeout", "call_timeout"), [(10, 60), (60, 5)])
def test_per_call_probe_deadline_keeps_long_probe_in_control(monkeypatch, configured_timeout, call_timeout):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(breaker, "time", SimpleNamespace(monotonic=lambda: clock.now))
    try:
        gate = breaker.get("svc:AGENT", failures=1, open_seconds=5, probe_timeout=configured_timeout)
        assert gate.failure()
        clock.now = 5
        assert gate.allow(probe_timeout=call_timeout)
        clock.now = 20
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert not pool.submit(gate.allow).result(timeout=5)
            gate.success()
            assert pool.submit(gate.allow).result(timeout=5)
    finally:
        breaker.reset_all()
