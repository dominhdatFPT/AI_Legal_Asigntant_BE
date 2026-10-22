"""Single-process per-user search limit; provider call budget lives in GoongClient."""
import asyncio
from collections import defaultdict, deque
import time

from app.core.errors import LocationError


class SearchLimiter:
    def __init__(self, per_minute: int = 5):
        self._per_minute = per_minute
        self._requests = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, user_id: str):
        async with self._lock:
            now = time.monotonic()
            values = self._requests[user_id]
            while values and now - values[0] >= 60:
                values.popleft()
            if len(values) >= self._per_minute:
                raise LocationError("RATE_LIMITED", "Bạn tìm kiếm quá nhanh, vui lòng thử lại sau.", 429)
            values.append(now)
