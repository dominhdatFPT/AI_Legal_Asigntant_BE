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
            if len(self._requests) > 1000:
                stale_users = [
                    uid for uid, q in self._requests.items()
                    if not q or (now - q[-1] >= 60)
                ]
                for uid in stale_users:
                    self._requests.pop(uid, None)
            values = self._requests[user_id]
            while values and now - values[0] >= 60:
                values.popleft()
            if len(values) >= self._per_minute:
                raise LocationError("RATE_LIMITED", "Bạn tìm kiếm quá nhanh, vui lòng thử lại sau.", 429)
            values.append(now)
