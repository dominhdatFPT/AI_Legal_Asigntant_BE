"""Xác thực API nội bộ /internal/v1/...: header X-Internal-Token phải khớp biến môi trường INTERNAL_TOKEN."""

import hmac
import os

from fastapi import Request

from app.core.responses import UNAUTHORIZED, ApiError


def require_internal_token(request: Request) -> None:
    expected = os.getenv("INTERNAL_TOKEN", "")
    given = request.headers.get("X-Internal-Token", "")
    # Chưa cấu hình INTERNAL_TOKEN thì chặn hết, không bao giờ mở cửa.
    if not expected or not hmac.compare_digest(given, expected):
        raise ApiError(401, UNAUTHORIZED)
