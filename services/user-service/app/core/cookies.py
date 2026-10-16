"""HttpOnly cookie cho access/refresh token, chạy song song với Bearer JWT.

- Trình duyệt: BE set cookie khi đăng nhập / refresh, FE gọi API với `credentials: "include"`,
  JS không đọc được token (chống bị đánh cắp qua XSS).
- Postman, mobile, service-to-service: vẫn dùng token trong body + header `Authorization: Bearer`.

Cấu hình qua biến môi trường (mặc định hợp với chạy local qua gateway):
- AUTH_COOKIE_SECURE      true khi chạy HTTPS (bắt buộc true nếu SameSite=None). Mặc định false.
- AUTH_COOKIE_SAMESITE    lax | strict | none. Mặc định lax.
- AUTH_COOKIE_DOMAIN      để trống = cookie gắn với host hiện tại.
- AUTH_REFRESH_COOKIE_PATH đường dẫn trình duyệt gửi refresh cookie. Mặc định /api
  (khớp cả /api/users/auth/... qua gateway lẫn /api/v1/users/auth/... gọi thẳng service).
"""

import os

from fastapi import Request, Response

from common_auth import ACCESS_COOKIE
from app.core.security import REFRESH_TOKEN_TTL

REFRESH_COOKIE = "refresh_token"


def _flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes"}


def _options() -> dict:
    samesite = os.getenv("AUTH_COOKIE_SAMESITE", "lax").strip().lower()
    if samesite not in {"lax", "strict", "none"}:
        samesite = "lax"
    return {
        "httponly": True,
        # Trình duyệt từ chối cookie SameSite=None nếu không có Secure.
        "secure": _flag("AUTH_COOKIE_SECURE") or samesite == "none",
        "samesite": samesite,
        "domain": os.getenv("AUTH_COOKIE_DOMAIN") or None,
    }


def _refresh_path() -> str:
    return os.getenv("AUTH_REFRESH_COOKIE_PATH", "/api").strip() or "/api"


def set_auth_cookies(response: Response, pair: dict) -> None:
    opts = _options()
    # Cookie access sống bằng refresh token (JWT bên trong vẫn hết hạn sau 15 phút) để khi
    # token hết hạn, BE vẫn nhận được cookie và trả TOKEN_EXPIRED như luồng Bearer,
    # thay vì trình duyệt tự xoá cookie rồi BE chỉ thấy UNAUTHORIZED.
    max_age = int(REFRESH_TOKEN_TTL.total_seconds())
    response.set_cookie(ACCESS_COOKIE, pair["access_token"], max_age=max_age, path="/", **opts)
    response.set_cookie(REFRESH_COOKIE, pair["refresh_token"], max_age=max_age, path=_refresh_path(), **opts)


def clear_auth_cookies(response: Response) -> None:
    opts = _options()
    response.delete_cookie(ACCESS_COOKIE, path="/", **opts)
    response.delete_cookie(REFRESH_COOKIE, path=_refresh_path(), **opts)


def refresh_token_from(request: Request, body_token: str | None) -> str | None:
    """Ưu tiên refresh_token trong body (Postman/mobile), không có thì lấy từ cookie."""
    return body_token or request.cookies.get(REFRESH_COOKIE) or None
