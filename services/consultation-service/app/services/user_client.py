"""Gọi User Service (USER_SERVICE_URL). Contract: services/user-service/openapi.yaml."""

import hashlib
import math
import os
import threading
import time

import jwt

from app.core.responses import ACCOUNT_LOCKED, UNAUTHORIZED, ApiError
from app.services.http_client import UpstreamError, bad_response, call

SERVICE = "USER"
_active_cache: dict[str, tuple[float, str]] = {}
_cache_lock = threading.Lock()
_cache_generation = 0


def _cache_seconds() -> float:
    try:
        ttl = float(os.getenv("USER_ACTIVE_CACHE_SECONDS", "0"))
    except ValueError:
        return 0.0
    return ttl if math.isfinite(ttl) and ttl > 0 else 0.0


def forget_user(user_id: str) -> None:
    global _cache_generation
    subject = str(user_id)
    with _cache_lock:
        for key in [key for key, (_, sub) in _active_cache.items() if sub == subject]:
            del _active_cache[key]
        # An upstream check that started before revocation must not repopulate the cache.
        _cache_generation += 1


def ensure_active(access_token: str) -> None:
    """GET /users/me bằng JWT của chính người dùng: bị khóa → 403 ACCOUNT_LOCKED, không còn tồn tại → 401."""
    ttl = _cache_seconds()
    if ttl:
        key = hashlib.sha256(access_token.encode("utf-8")).hexdigest()
        with _cache_lock:
            generation = _cache_generation
            cached = _active_cache.get(key)
            if cached and cached[0] > time.monotonic():
                return
            _active_cache.pop(key, None)
    try:
        call(SERVICE, "GET", "/api/v1/users/me", bearer=access_token)
    except UpstreamError as e:
        if e.status == 403 and e.code == ACCOUNT_LOCKED:
            raise ApiError(403, ACCOUNT_LOCKED, e.message) from None
        if e.status in (401, 404):
            raise ApiError(401, UNAUTHORIZED) from None
        raise bad_response(SERVICE) from None
    if not ttl:
        return
    try:
        sub = jwt.decode(access_token, options={"verify_signature": False}).get("sub")
    except jwt.InvalidTokenError:
        return
    if not isinstance(sub, str) or not sub:
        return
    with _cache_lock:
        if generation != _cache_generation:
            return
        now = time.monotonic()
        _active_cache[key] = (now + ttl, sub)
        if len(_active_cache) > 10_000:
            for expired_key in [key for key, (expires, _) in _active_cache.items() if expires <= now]:
                del _active_cache[expired_key]
