"""Gọi sang Lawyer Service (LAWYER_SERVICE_URL, X-Internal-Token).

Ẩn hồ sơ luật sư khi admin hạ vai trò lawyer hoặc xóa tài khoản luật sư. Gọi TRƯỚC khi lưu thay đổi ở User Service:
lỗi thì ném ApiError 502/503/504 LAWYER_*, User Service không đổi gì (admin thử lại). API ẩn gọi lại nhiều lần an toàn.
"""

import logging
import os
import uuid

import httpx

from app.core.responses import LAWYER_BAD_RESPONSE, LAWYER_TIMEOUT, LAWYER_UNAVAILABLE, ApiError

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 5.0
transport: httpx.BaseTransport | None = None  # test thay bằng httpx.MockTransport


def hide_lawyer_profile(user_id: uuid.UUID, reason: str, request_id: str | None = None) -> None:
    base = os.getenv("LAWYER_SERVICE_URL", "").rstrip("/")
    token = os.getenv("INTERNAL_TOKEN", "")
    if not base or not token:
        logger.error("Thiếu LAWYER_SERVICE_URL hoặc INTERNAL_TOKEN, không ẩn được hồ sơ luật sư")
        raise ApiError(503, LAWYER_UNAVAILABLE)
    headers = {"X-Internal-Token": token}
    if request_id:
        headers["X-Request-ID"] = request_id
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, transport=transport, follow_redirects=False,
                          trust_env=False) as client:
            response = client.post(f"{base}/internal/v1/lawyers/{user_id}/hide", json={"reason": reason},
                                   headers=headers)
    except httpx.TimeoutException:
        raise ApiError(504, LAWYER_TIMEOUT) from None
    except httpx.HTTPError:
        raise ApiError(503, LAWYER_UNAVAILABLE) from None
    logger.info("lawyer hide %s → %s request_id=%s", user_id, response.status_code, request_id)
    try:
        ok = response.status_code == 200 and response.json()["data"]["is_hidden"] in (True, False)
    except (ValueError, KeyError, TypeError):
        ok = False
    if not ok:
        raise ApiError(502, LAWYER_BAD_RESPONSE)
