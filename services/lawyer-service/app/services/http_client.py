"""Gọi API nội bộ của service khác (X-Internal-Token), quy ước API_GUIDELINES mục 8.

- Không kết nối được → 503 <SERVICE>_UNAVAILABLE; quá thời gian → 504 <SERVICE>_TIMEOUT;
  5xx / redirect / JSON sai cấu trúc → 502 <SERVICE>_BAD_RESPONSE.
- 4xx là kết quả nghiệp vụ: ném UpstreamError để bên gọi tự xử lý theo contract.
- Chỉ retry GET (tối đa 2 lần, chờ 0,5 rồi 1 giây). Không log body (có mật khẩu, CCCD...).
"""

import logging
import os
import time
from typing import Any

import httpx

from app.core.responses import ApiError, current_request_id

logger = logging.getLogger("lawyer-service")

TIMEOUT_SECONDS = 5.0
RETRY_DELAYS = (0.5, 1.0)
LABELS = {"USER": "Hệ thống tài khoản", "DOCUMENT": "Hệ thống giấy tờ"}

# Test thay bằng httpx.MockTransport / hàm sleep giả; code thật để None / time.sleep.
transport: httpx.BaseTransport | None = None
sleep = time.sleep


class UpstreamError(Exception):
    """Service phụ trả 4xx kèm envelope lỗi hợp lệ."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(code)
        self.status, self.code, self.message = status, code, message


def unavailable(service: str) -> ApiError:
    return ApiError(503, f"{service}_UNAVAILABLE", f"{LABELS[service]} tạm thời không phản hồi, vui lòng thử lại sau.")


def timed_out(service: str) -> ApiError:
    return ApiError(504, f"{service}_TIMEOUT", f"{LABELS[service]} phản hồi quá chậm, vui lòng thử lại sau.")


def bad_response(service: str) -> ApiError:
    return ApiError(502, f"{service}_BAD_RESPONSE", f"{LABELS[service]} đang gặp lỗi, vui lòng thử lại sau.")


def _parse(service: str, response: httpx.Response) -> Any:
    if response.status_code == 204:
        return None
    try:
        body = response.json()
    except ValueError:
        raise bad_response(service) from None
    if not isinstance(body, dict):
        raise bad_response(service)
    if 200 <= response.status_code < 300:
        if "data" not in body:
            raise bad_response(service)
        return body["data"]
    error = body.get("error")
    if not isinstance(error, dict) or not isinstance(error.get("code"), str) or not isinstance(error.get("message"), str):
        raise bad_response(service)
    raise UpstreamError(response.status_code, error["code"], error["message"])


def call(service: str, method: str, path: str, *, json: dict | None = None) -> Any:
    base = os.getenv(f"{service}_SERVICE_URL", "").rstrip("/")
    token = os.getenv("INTERNAL_TOKEN", "")
    if not base or not token:
        logger.error("Thiếu %s_SERVICE_URL hoặc INTERNAL_TOKEN", service)
        raise unavailable(service)

    headers = {"X-Internal-Token": token}
    rid = current_request_id.get()
    if rid:
        headers["X-Request-ID"] = rid
    attempts = 1 + (len(RETRY_DELAYS) if method == "GET" else 0)

    for attempt in range(attempts):
        started = time.monotonic()
        try:
            with httpx.Client(timeout=TIMEOUT_SECONDS, transport=transport, follow_redirects=False,
                              trust_env=False) as client:
                response = client.request(method, base + path, json=json, headers=headers)
        except httpx.TimeoutException:
            failure = timed_out(service)
        except httpx.HTTPError:
            failure = unavailable(service)
        else:
            logger.info("%s %s %s → %s (%.0f ms) request_id=%s", service, method, path.split("?")[0],
                        response.status_code, (time.monotonic() - started) * 1000, rid)
            if response.status_code >= 500 or 300 <= response.status_code < 400:
                failure = bad_response(service)
            else:
                return _parse(service, response)
        logger.warning("%s %s %s lỗi %s (lần %d) request_id=%s", service, method, path.split("?")[0],
                       failure.code, attempt + 1, rid)
        if attempt + 1 < attempts:
            sleep(RETRY_DELAYS[attempt])
    raise failure
