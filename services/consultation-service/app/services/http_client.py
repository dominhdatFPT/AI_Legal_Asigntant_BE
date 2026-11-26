"""Gọi API nội bộ của service khác (X-Internal-Token), quy ước API_GUIDELINES mục 8.

- Không kết nối được → 503 <SERVICE>_UNAVAILABLE; quá thời gian → 504 <SERVICE>_TIMEOUT;
  5xx / redirect / JSON sai cấu trúc → 502 <SERVICE>_BAD_RESPONSE.
- 4xx là kết quả nghiệp vụ: ném UpstreamError để bên gọi tự xử lý theo contract.
- Chỉ retry GET (tối đa 2 lần, chờ 0,5 rồi 1 giây). Không log body (có mật khẩu, CCCD...).
"""

from http.cookiejar import DefaultCookiePolicy
import logging
import os
import threading
import time
from typing import Any

import httpx

from app.core.responses import ApiError, current_request_id

logger = logging.getLogger("consultation-service")

TIMEOUT_SECONDS = 5.0
RETRY_DELAYS = (0.5, 1.0)
LABELS = {"USER": "Hệ thống tài khoản", "LAWYER": "Hệ thống luật sư", "AGENT": "Trợ lý AI"}

# Test thay bằng httpx.MockTransport / hàm sleep giả; code thật để None / time.sleep.
transport: httpx.BaseTransport | None = None
sleep = time.sleep

_shared: httpx.Client | None = None
_shared_lock = threading.Lock()


def _shared_client() -> httpx.Client:
    """Client dùng chung cả process: giữ kết nối keep-alive/TLS giữa các lần gọi thay vì bắt tay lại mỗi lần."""
    global _shared
    with _shared_lock:
        if _shared is None:
            # keepalive_expiry < 5s (Uvicorn default) để tránh race đóng kết nối với POST không retry.
            _shared = httpx.Client(
                follow_redirects=False,
                trust_env=False,
                limits=httpx.Limits(keepalive_expiry=2.0),
            )
            # Dùng chung cho mọi người dùng: không lưu/gửi lại cookie do service khác đặt.
            _shared.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))
        return _shared


def close_shared_client() -> None:
    global _shared
    with _shared_lock:
        if _shared is not None:
            _shared.close()
            _shared = None


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


def call(service: str, method: str, path: str, *, json: dict | None = None, bearer: str | None = None,
         raw: bool = False, token_env: str = "INTERNAL_TOKEN", timeout: float = TIMEOUT_SECONDS) -> Any:
    """`bearer`: gọi API công khai bằng access token của người dùng thay cho X-Internal-Token.
    `raw`: 2xx trả nguyên httpx.Response (file nhị phân), lỗi vẫn xử lý như JSON.
    `token_env`: tên biến môi trường chứa giá trị gửi trong X-Internal-Token (mặc định INTERNAL_TOKEN).
    `timeout`: giây chờ tối đa của một lần gọi (mặc định 5; việc gọi LLM cần lâu hơn)."""
    from common_auth import breaker

    base = os.getenv(f"{service}_SERVICE_URL", "").rstrip("/")
    token = os.getenv(token_env, "")
    if not base or not (token or bearer):
        logger.error("Thiếu %s_SERVICE_URL hoặc %s", service, token_env)
        raise unavailable(service)

    retry_delays = RETRY_DELAYS if method == "GET" else ()
    attempts = 1 + len(retry_delays)
    budget = attempts * timeout + sum(retry_delays)
    gate = breaker.get(f"consultation:{service}")
    if not gate.allow(probe_timeout=budget + 5.0):
        logger.warning("Ngắt mạch đang mở cho %s", service)
        raise unavailable(service)

    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {"X-Internal-Token": token}
    rid = current_request_id.get()
    if rid:
        headers["X-Request-ID"] = rid
    for attempt in range(attempts):
        started = time.monotonic()
        try:
            if transport is None:
                response = _shared_client().request(method, base + path, json=json, headers=headers, timeout=timeout)
            else:  # test: MockTransport → client riêng, đóng sau mỗi lần gọi như trước
                with httpx.Client(timeout=timeout, transport=transport, follow_redirects=False,
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
                gate.success()
                if raw and 200 <= response.status_code < 300:
                    return response
                return _parse(service, response)
        logger.warning("%s %s %s lỗi %s (lần %d) request_id=%s", service, method, path.split("?")[0],
                       failure.code, attempt + 1, rid)
        if attempt + 1 < attempts:
            sleep(retry_delays[attempt])
    gate.failure()
    raise failure
