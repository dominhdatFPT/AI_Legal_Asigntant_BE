"""Gọi User Service nội bộ (USER_SERVICE_URL). Contract: services/user-service/openapi.yaml, tag Internal."""

import uuid

from app.core.responses import APPLICANT_UNAVAILABLE, ApiError
from app.services.http_client import UpstreamError, bad_response, call

SERVICE = "USER"


def _need(data, *keys):
    if not isinstance(data, dict) or any(k not in data for k in keys):
        raise bad_response(SERVICE)
    return data


def get_status(user_id: uuid.UUID) -> dict | None:
    """{has_citizen_profile, is_locked, ...}; None khi tài khoản không tồn tại / đã xóa mềm."""
    try:
        data = call(SERVICE, "GET", f"/internal/v1/users/{user_id}/status")
    except UpstreamError as e:
        if e.status == 404:
            return None
        raise bad_response(SERVICE) from None
    _need(data, "has_citizen_profile", "is_locked")
    if not isinstance(data["has_citizen_profile"], bool) or not isinstance(data["is_locked"], bool):
        raise bad_response(SERVICE)
    return data


def get_citizen_profile(user_id: uuid.UUID) -> dict | None:
    """Hồ sơ CCCD; None khi chưa khai hoặc tài khoản không tồn tại."""
    try:
        data = call(SERVICE, "GET", f"/internal/v1/users/{user_id}/citizen-profile")
    except UpstreamError as e:
        if e.status == 404:
            return None
        raise bad_response(SERVICE) from None
    if data is not None and not isinstance(data, dict):
        raise bad_response(SERVICE)
    return data


def promote_to_lawyer(user_id: uuid.UUID) -> dict:
    """Đổi vai trò sang lawyer khi duyệt đơn (gọi lại an toàn). 404/409 → người gửi không còn hợp lệ."""
    try:
        data = call(SERVICE, "PUT", f"/internal/v1/users/{user_id}/role", json={"role": "lawyer"})
    except UpstreamError as e:
        if e.status in (404, 409):
            raise ApiError(409, APPLICANT_UNAVAILABLE) from None
        raise bad_response(SERVICE) from None
    _need(data, "id", "role")
    if data["role"] != "lawyer":
        raise bad_response(SERVICE)
    return data
