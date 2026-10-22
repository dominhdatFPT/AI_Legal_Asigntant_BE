"""Per-request paid provider call count shared across async request tasks."""
from contextvars import ContextVar


provider_counter: ContextVar[list[int] | None] = ContextVar("location_provider_counter", default=None)
