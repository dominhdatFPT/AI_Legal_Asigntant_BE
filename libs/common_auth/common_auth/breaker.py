"""Process-local circuit breakers for synchronous upstream calls."""

import threading
import time


class CircuitBreaker:
    def __init__(self, failures: int = 5, open_seconds: float = 30.0, probe_timeout: float = 10.0):
        self._failures = max(1, failures)
        self._open_seconds = open_seconds
        self._probe_timeout = probe_timeout
        self._lock = threading.Lock()
        self._attempt = threading.local()
        self._generation = 0
        self._consecutive_failures = 0
        self._open_until: float | None = None
        self._probe_deadline: float | None = None

    def allow(self, *, probe_timeout: float | None = None) -> bool:
        with self._lock:
            if self._open_until is not None:
                now = time.monotonic()
                if now < self._open_until or (self._probe_deadline is not None and now < self._probe_deadline):
                    return False
                effective_probe_timeout = self._probe_timeout
                if probe_timeout is not None:
                    effective_probe_timeout = max(effective_probe_timeout, probe_timeout)
                self._probe_deadline = now + effective_probe_timeout
                self._generation += 1
            self._attempt.generation = self._generation
            return True

    def _is_current_attempt(self) -> bool:
        # A call can finish after its probe expired and another thread took over.
        generation = self._attempt.__dict__.pop("generation", self._generation)
        return generation == self._generation

    def success(self) -> None:
        with self._lock:
            if self._is_current_attempt():
                self._consecutive_failures = 0
                self._open_until = self._probe_deadline = None

    def failure(self) -> bool:
        """Record a failed call and return whether it opened the circuit."""
        with self._lock:
            if not self._is_current_attempt():
                return False
            now = time.monotonic()
            if self._open_until is not None and now < self._open_until:
                return False
            self._consecutive_failures += 1
            if self._probe_deadline is None and self._consecutive_failures < self._failures:
                return False
            self._open_until = now + self._open_seconds
            self._probe_deadline = None
            self._generation += 1
            return True

    def reset(self) -> None:
        with self._lock:
            self._generation += 1
            self._consecutive_failures = 0
            self._open_until = self._probe_deadline = None


_registry: dict[str, CircuitBreaker] = {}
_registry_lock = threading.Lock()


def get(name: str, *, failures: int = 5, open_seconds: float = 30.0,
        probe_timeout: float = 10.0) -> CircuitBreaker:
    with _registry_lock:
        if name not in _registry:
            _registry[name] = CircuitBreaker(failures=failures, open_seconds=open_seconds, probe_timeout=probe_timeout)
        return _registry[name]


def reset_all() -> None:
    with _registry_lock:
        for gate in _registry.values():
            gate.reset()
        _registry.clear()
