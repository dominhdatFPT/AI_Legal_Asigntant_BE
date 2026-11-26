import re
from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def sanitize_request_id(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    return re.sub(r"[^A-Za-z0-9_.:-]", "", value)[:128] or None
