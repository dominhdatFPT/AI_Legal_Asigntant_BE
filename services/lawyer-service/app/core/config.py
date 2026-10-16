import os
from datetime import timedelta

DEFAULT_REAPPLY_COOLDOWN_HOURS = 24


def reapply_cooldown() -> timedelta:
    """Thời gian phải chờ sau khi bị từ chối mới được gửi đơn mới (LAWYER_REAPPLY_COOLDOWN_HOURS, mặc định 24)."""
    raw = os.getenv("LAWYER_REAPPLY_COOLDOWN_HOURS", "").strip()
    try:
        hours = int(raw) if raw else DEFAULT_REAPPLY_COOLDOWN_HOURS
    except ValueError:
        hours = DEFAULT_REAPPLY_COOLDOWN_HOURS
    return timedelta(hours=max(hours, 0))
