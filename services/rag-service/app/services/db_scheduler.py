"""Bounded, per-tenant scheduling for the short synchronous retrieval phases."""

import asyncio
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass


class BatchDBUnavailable(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass
class _Waiter:
    grant: asyncio.Future
    expires: float
    error_code: str


class FairDBScheduler:
    def __init__(
        self, limit: int, max_waiting: int, wait_seconds: float,
        interactive_burst: int = 3, per_tenant_burst: int = 3,
    ):
        self._limit = limit
        self._max_waiting = max_waiting
        self._wait_seconds = wait_seconds
        self._interactive_burst = interactive_burst
        self._tenant_limit = min(limit, per_tenant_burst)
        # Refill lazily so one HTTP batch cannot fill a small queue on its own.
        self.phase_parallelism = min(self._tenant_limit, max_waiting + 1)
        self._lanes: dict[str, OrderedDict[str, dict[asyncio.Future, _Waiter]]] = {
            "interactive": OrderedDict(),
            "batch": OrderedDict(),
        }
        self._active: Counter[str] = Counter()
        self._running = 0
        self._waiting = 0
        self._interactive_streak = 0
        self._dispatch_scheduled = False

    def saturated(self) -> bool:
        return self._running >= self._limit and self._waiting >= self._max_waiting

    def _candidate(self, priority: str) -> str | None:
        eligible = None
        for tenant in self._lanes[priority]:
            active = self._active[tenant]
            if active == 0:
                return tenant
            if eligible is None and active < self._tenant_limit:
                eligible = tenant
        return eligible

    def _remove_waiter(self, priority: str, tenant: str, grant: asyncio.Future) -> None:
        lane = self._lanes[priority]
        queue = lane.get(tenant)
        if queue is not None and queue.pop(grant, None) is not None:
            self._waiting -= 1
            if not queue:
                del lane[tenant]
                if priority == "batch" and not lane:
                    self._interactive_streak = 0

    def _dispatch(self) -> None:
        while self._running < self._limit and self._waiting:
            batch = self._candidate("batch")
            interactive = self._candidate("interactive")
            if batch is not None and (interactive is None or self._interactive_streak >= self._interactive_burst):
                priority, tenant = "batch", batch
            elif interactive is not None:
                priority, tenant = "interactive", interactive
            else:
                return

            lane = self._lanes[priority]
            waiter = next(iter(lane[tenant].values()))
            self._remove_waiter(priority, tenant, waiter.grant)
            if waiter.grant.done():
                continue
            if time.monotonic() >= waiter.expires:
                waiter.grant.set_exception(BatchDBUnavailable(waiter.error_code))
                continue
            if tenant in lane:
                lane.move_to_end(tenant)
            if priority == "batch":
                self._interactive_streak = 0
            elif self._lanes["batch"]:
                self._interactive_streak = min(self._interactive_streak + 1, self._interactive_burst)
            self._active[tenant] += 1
            self._running += 1
            waiter.grant.set_result(None)

    def _drain_ready(self) -> None:
        self._dispatch_scheduled = False
        self._dispatch()

    def _release(self, tenant: str) -> None:
        self._running -= 1
        self._active[tenant] -= 1
        if self._active[tenant] == 0:
            del self._active[tenant]
        self._dispatch()

    async def run(self, deadline, operation, *, tenant_key: str, priority: str):
        now = time.monotonic()
        if deadline <= now:
            raise BatchDBUnavailable("DEADLINE_EXCEEDED")
        # An immediate grant does not use queue capacity.
        if self._waiting >= self._max_waiting and (
            self._running >= self._limit or self._active[tenant_key] >= self._tenant_limit
        ):
            raise BatchDBUnavailable("RAG_OVERLOADED")
        loop = asyncio.get_running_loop()
        expires = min(deadline, now + self._wait_seconds)
        code = "DEADLINE_EXCEEDED" if expires == deadline else "RAG_OVERLOADED"
        grant = loop.create_future()
        self._lanes[priority].setdefault(tenant_key, {})[grant] = _Waiter(grant, expires, code)
        self._waiting += 1
        if self._waiting > self._max_waiting:
            self._dispatch()
        elif not self._dispatch_scheduled and self._running < self._limit:
            # Give other ready requests a chance to queue before filling idle slots.
            self._dispatch_scheduled = True
            loop.call_soon(self._drain_ready)
        # Queued work can be displaced by an earlier eligible waiter.
        if not grant.done() and self._waiting > self._max_waiting:
            self._remove_waiter(priority, tenant_key, grant)
            raise BatchDBUnavailable("RAG_OVERLOADED")

        try:
            async with asyncio.timeout(max(0, expires - time.monotonic())):
                await grant

            def work():
                if time.monotonic() >= deadline:
                    raise BatchDBUnavailable("DEADLINE_EXCEEDED")
                return operation()

            future = loop.run_in_executor(None, work)
        except BaseException as exc:
            self._remove_waiter(priority, tenant_key, grant)
            if grant.done() and not grant.cancelled() and grant.exception() is None:
                self._release(tenant_key)
            if isinstance(exc, TimeoutError):
                raise BatchDBUnavailable(code) from exc
            raise

        def finished(result):
            self._release(tenant_key)
            if not result.cancelled():
                result.exception()

        # The worker owns the permit through session close, even after its caller leaves.
        future.add_done_callback(finished)
        timeout = asyncio.timeout(max(0, deadline - time.monotonic()))
        try:
            async with timeout:
                return await asyncio.shield(future)
        except TimeoutError as exc:
            if not timeout.expired():
                raise
            raise BatchDBUnavailable("DEADLINE_EXCEEDED") from exc
