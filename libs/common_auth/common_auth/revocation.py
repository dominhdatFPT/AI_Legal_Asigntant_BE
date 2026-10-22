"""Thu hồi access token trên toàn hệ thống.

Mỗi process dùng common_auth nghe `user.logged_out` (thu hồi 1 token theo jti) và
`user.blocked` (thu hồi mọi token phát hành trước lúc khoá) qua một queue tạm của riêng nó,
rồi `decode_access_token` từ chối các token đó.

shortcut: danh sách nằm trong RAM; process khởi động lại hoặc rớt kết nối broker thì bỏ lỡ
event trong lúc đó, token cũ sống tiếp tối đa ACCESS_TOKEN_TTL (15 phút). Cần chặt hơn thì
chuyển kho sang Redis dùng chung.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime
from typing import Any

logger = logging.getLogger("common_auth.revocation")

LOGGED_OUT = "user.logged_out"
BLOCKED = "user.blocked"
DEFAULT_TTL = 900
KEEP_BLOCK_SECONDS = 24 * 3600

_lock = threading.Lock()
_revoked_jti: dict[str, float] = {}  # jti -> exp (epoch)
_blocked_at: dict[str, float] = {}  # user_id -> thời điểm bị khoá (epoch)
_started = False

# Service tự đọc trạng thái khoá trong DB (User Service, trả 403 kèm lý do) có thể tắt kiểm tra này.
CHECK_BLOCKED = True


def revoke_jti(jti: str | None, exp: float | int | None = None) -> None:
    if not jti:
        return
    now = time.time()
    until = float(exp) if exp is not None else now + DEFAULT_TTL
    with _lock:
        for key in [k for k, e in _revoked_jti.items() if e <= now]:
            del _revoked_jti[key]
        if until > now:
            _revoked_jti[jti] = until


def block_user(user_id: str | None, at: float | None = None) -> None:
    if not user_id:
        return
    now = time.time()
    with _lock:
        for key in [k for k, t in _blocked_at.items() if t < now - KEEP_BLOCK_SECONDS]:
            del _blocked_at[key]
        _blocked_at[str(user_id)] = now if at is None else at


def is_jti_revoked(jti: str | None) -> bool:
    if not jti:
        return False
    with _lock:
        until = _revoked_jti.get(jti)
    return until is not None and until > time.time()


def is_revoked(claims: dict[str, Any]) -> bool:
    if is_jti_revoked(claims.get("jti")):
        return True
    if not CHECK_BLOCKED:
        return False
    with _lock:
        blocked_at = _blocked_at.get(str(claims.get("sub")))
    # Token phát hành trước (hoặc cùng giây) lúc khoá thì bị từ chối; đăng nhập lại sau khi mở khoá vẫn dùng được.
    return blocked_at is not None and float(claims.get("iat") or 0) <= blocked_at


def clear() -> None:
    with _lock:
        _revoked_jti.clear()
        _blocked_at.clear()


def _event_time(event: dict[str, Any]) -> float:
    try:
        return datetime.fromisoformat(event["timestamp"]).timestamp()
    except Exception:
        return time.time()


async def apply_event(routing_key: str, event: dict[str, Any]) -> None:
    payload = event.get("payload") or {}
    if routing_key == LOGGED_OUT:
        revoke_jti(payload.get("jti"), payload.get("exp"))
    elif routing_key == BLOCKED:
        block_user(payload.get("user_id"), _event_time(event))


def ensure_started() -> None:
    """Chạy listener nền một lần cho mỗi process (thread daemon có event loop riêng)."""
    global _started
    with _lock:
        if _started:
            return
        _started = True

    from . import events

    if not events.rabbitmq_url():
        logger.info("Chưa cấu hình RABBITMQ_URL: chỉ thu hồi token cục bộ trong process này.")
        return
    try:
        import aio_pika  # noqa: F401
    except ImportError:
        logger.error("Thiếu aio-pika: không nghe được event thu hồi token.")
        return

    def run() -> None:
        asyncio.run(events.consume_forever(None, [LOGGED_OUT, BLOCKED], apply_event, prefetch=100))

    threading.Thread(target=run, name="token-revocation", daemon=True).start()
