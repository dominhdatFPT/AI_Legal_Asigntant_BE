import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from common_auth import ALGORITHM, ISSUER

ACCESS_TOKEN_TTL = timedelta(minutes=15)
REFRESH_TOKEN_TTL = timedelta(days=14)


def _load_private_key() -> str:
    key = os.getenv("JWT_PRIVATE_KEY")
    if key:
        return key.replace("\\n", "\n")
    path = os.getenv("JWT_PRIVATE_KEY_PATH")
    if path:
        with open(path, encoding="utf-8") as f:
            return f.read()
    raise RuntimeError("Chưa cấu hình JWT_PRIVATE_KEY hoặc JWT_PRIVATE_KEY_PATH")


def create_access_token(user_id: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "iss": ISSUER,
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, _load_private_key(), algorithm=ALGORITHM)


def hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def new_refresh_token() -> tuple[str, str, datetime]:
    raw = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + REFRESH_TOKEN_TTL
    return raw, hash_refresh_token(raw), expires_at


def token_pair(access_token: str, refresh_token: str) -> dict:
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": int(ACCESS_TOKEN_TTL.total_seconds()),
    }
