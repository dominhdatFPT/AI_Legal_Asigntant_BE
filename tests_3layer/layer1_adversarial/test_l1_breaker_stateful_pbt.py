from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import patch

from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, rule

from common_auth import breaker


class BreakerModel(RuleBasedStateMachine):
    @initialize(failures=st.integers(1, 7), cooldown=st.integers(1, 60), lease=st.integers(1, 60))
    def configure(self, failures, cooldown, lease):
        self.clock = SimpleNamespace(now=0.0)
        self.clock_patch = patch.object(breaker, "time", SimpleNamespace(monotonic=lambda: self.clock.now))
        self.clock_patch.start()
        self.gate = breaker.CircuitBreaker(failures, cooldown, lease)
        self.threshold, self.cooldown, self.lease = failures, cooldown, lease
        self.clear_model()

    def clear_model(self):
        self.next_probe = None
        self.streak = 0

    def teardown(self):
        self.clock_patch.stop()

    @rule(jump=st.integers(0, 180))
    def advance_clock(self, jump):
        self.clock.now += jump

    @rule()
    def jump_to_probe_boundary(self):
        if self.next_probe is not None:
            self.clock.now = max(self.clock.now, self.next_probe)

    @rule(timeout=st.none() | st.integers(0, 90), succeeds=st.booleans(), completes=st.booleans())
    def attempt(self, timeout, succeeds, completes):
        may_enter = self.next_probe is None or self.clock.now >= self.next_probe
        assert self.gate.allow(probe_timeout=timeout) is may_enter
        if not may_enter:
            return
        was_probe = self.next_probe is not None
        if was_probe:
            self.next_probe = self.clock.now + max(self.lease, timeout or 0)
        if not completes:
            return
        if succeeds:
            self.gate.success()
            self.clear_model()
            return
        self.streak += 1
        opens = was_probe or self.streak >= self.threshold
        assert self.gate.failure() is opens
        if opens:
            self.next_probe = self.clock.now + self.cooldown

    @rule()
    def reset(self):
        self.gate.reset()
        self.clear_model()


TestCircuitBreakerStateMachine = BreakerModel.TestCase
TestCircuitBreakerStateMachine.settings = settings(max_examples=150, stateful_step_count=60, deadline=None)


def test_exactly_one_probe_at_open_deadline(monkeypatch):
    clock = SimpleNamespace(now=100)
    monkeypatch.setattr(breaker, "time", SimpleNamespace(monotonic=lambda: clock.now))
    gate = breaker.CircuitBreaker(failures=1, open_seconds=7, probe_timeout=1)
    assert gate.allow() and gate.failure()
    clock.now = 107
    barrier = Barrier(16, timeout=6)

    def contend():
        barrier.wait()
        return gate.allow(probe_timeout=60.0)

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: contend(), range(16), timeout=10))
    assert results.count(True) == 1 and results.count(False) == 15
    clock.now = 166.999
    assert gate.allow() is False


def test_stale_probe_cannot_close_reopened_generation(monkeypatch):
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(breaker, "time", SimpleNamespace(monotonic=lambda: clock.now))
    gate = breaker.CircuitBreaker(failures=1, open_seconds=5, probe_timeout=2)
    assert gate.allow() and gate.failure()
    clock.now = 5
    entered, release = Event(), Event()

    def old_probe():
        assert gate.allow()
        entered.set()
        assert release.wait(5)
        gate.success()

    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(old_probe)
        try:
            assert entered.wait(5)
            clock.now = 7
            assert gate.allow() and gate.failure()
        finally:
            release.set()
        old.result(timeout=5)
    assert gate.allow() is False
    clock.now = 12
    assert gate.allow() is True
