import logging
import os
from dataclasses import dataclass

import cachecontrol
import google.auth.transport.requests
import requests
from google.oauth2 import id_token

from app.core.responses import INVALID_FIREBASE_TOKEN, ApiError

_request = google.auth.transport.requests.Request(session=cachecontrol.CacheControl(requests.Session()))

PROVIDERS = {"google.com": "google", "password": "password"}

logger = logging.getLogger(__name__)

# Đồng hồ máy chạy service (đặc biệt Docker Desktop/WSL2 sau khi máy sleep) hay lệch vài giây so với Google,
# làm token mới cấp bị từ chối "Token used too early". Cho phép lệch tối đa 60 giây (giới hạn của google-auth).
DEFAULT_CLOCK_SKEW_SECONDS = 10


@dataclass(frozen=True)
class FirebaseIdentity:
    uid: str
    email: str | None
    email_verified: bool
    name: str | None
    picture: str | None
    provider: str | None


def project_id() -> str:
    value = os.getenv("FIREBASE_PROJECT_ID")
    if not value:
        raise RuntimeError("Chưa cấu hình FIREBASE_PROJECT_ID")
    return value


def clock_skew_seconds() -> int:
    try:
        value = int(os.getenv("FIREBASE_CLOCK_SKEW_SECONDS", DEFAULT_CLOCK_SKEW_SECONDS))
    except ValueError:
        value = DEFAULT_CLOCK_SKEW_SECONDS
    return max(0, min(value, 60))


def verify_id_token(token: str) -> FirebaseIdentity:
    audience = project_id()
    try:
        claims = id_token.verify_firebase_token(
            token, _request, audience=audience, clock_skew_in_seconds=clock_skew_seconds()
        )
    except ValueError as exc:
        # Chỉ log lý do (hết hạn, sai aud, lệch giờ...), không log token.
        logger.warning("Firebase ID token bị từ chối: %s", exc)
        raise ApiError(401, INVALID_FIREBASE_TOKEN)
    if not claims or not claims.get("sub"):
        raise ApiError(401, INVALID_FIREBASE_TOKEN)

    provider = (claims.get("firebase") or {}).get("sign_in_provider")
    return FirebaseIdentity(
        uid=claims["sub"],
        email=claims.get("email"),
        email_verified=bool(claims.get("email_verified")),
        name=claims.get("name"),
        picture=claims.get("picture"),
        provider=PROVIDERS.get(provider, provider),
    )
