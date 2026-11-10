"""Gọi User Service (USER_SERVICE_URL). Contract: services/user-service/openapi.yaml."""

from app.core.responses import ACCOUNT_LOCKED, UNAUTHORIZED, ApiError
from app.services.http_client import UpstreamError, bad_response, call

SERVICE = "USER"


def ensure_active(access_token: str) -> None:
    """GET /users/me bằng JWT của chính người dùng: bị khóa → 403 ACCOUNT_LOCKED, không còn tồn tại → 401."""
    try:
        call(SERVICE, "GET", "/api/v1/users/me", bearer=access_token)
    except UpstreamError as e:
        if e.status == 403 and e.code == ACCOUNT_LOCKED:
            raise ApiError(403, ACCOUNT_LOCKED, e.message) from None
        if e.status in (401, 404):
            raise ApiError(401, UNAUTHORIZED) from None
        raise bad_response(SERVICE) from None
