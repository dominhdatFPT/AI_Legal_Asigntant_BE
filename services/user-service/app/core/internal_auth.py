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


def require_lawyer_service_token(request: Request) -> None:
    """Chỉ Lawyer Service đổi được vai trò: header X-Internal-Token phải khớp LAWYER_SERVICE_TOKEN (không nhận INTERNAL_TOKEN)."""
    expected = os.getenv("LAWYER_SERVICE_TOKEN", "")
    # Chưa cấu hình thì 503, không bao giờ mở cửa.
    if not expected:
        raise ApiError(503, "SERVICE_NOT_CONFIGURED", "Chưa cấu hình LAWYER_SERVICE_TOKEN.")
    given = request.headers.get("X-Internal-Token", "")
    if not hmac.compare_digest(given.encode(), expected.encode()):
        raise ApiError(401, UNAUTHORIZED)
