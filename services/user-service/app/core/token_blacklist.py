"""Blacklist access token theo jti.

Dùng chung kho với common_auth.revocation: token bị đưa vào đây thì `decode_access_token`
của mọi request trong process này đều từ chối; các service khác nhận qua event `user.logged_out`.
"""

from __future__ import annotations

import time

from common_auth import revocation


def blacklist_token(jti: str, exp_timestamp: float | int | None = None, ttl_seconds: int = 900) -> None:
    revocation.revoke_jti(jti, exp_timestamp if exp_timestamp is not None else time.time() + ttl_seconds)


def is_token_blacklisted(jti: str | None) -> bool:
    return revocation.is_jti_revoked(jti)


def clear_blacklist() -> None:
    """Xoá sạch danh sách (phục vụ test)."""
    revocation.clear()
