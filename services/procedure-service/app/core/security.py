import os
import logging
from fastapi import Request

from common_auth.core import (
    ACCESS_COOKIE,
    CurrentUser,
)
from common_auth.fastapi_dep import (
    AuthHTTPException,
    get_current_user as _common_get_current_user,
    require_role,
)

logger = logging.getLogger(__name__)

# Đảm bảo đường dẫn public key được cấu hình
if not os.getenv("JWT_PUBLIC_KEY") and not os.getenv("JWT_PUBLIC_KEY_PATH"):
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    local_key = os.path.join(base_dir, "keys", "jwt_public.pem")
    hidden_key = os.path.join(base_dir, ".keys", "jwt_public.pem")

    if os.path.exists(local_key):
        os.environ["JWT_PUBLIC_KEY_PATH"] = local_key
    elif os.path.exists(hidden_key):
        os.environ["JWT_PUBLIC_KEY_PATH"] = hidden_key


def _is_dev_auth_allowed() -> bool:
    """
    Kiểm tra xem header X-User-Id có được phép sử dụng không.
    TUYỆT ĐỐI KHÔNG cho phép trên production.
    Chỉ cho phép khi có cờ ALLOW_DEV_AUTH=true trên môi trường dev/test.
    """
    env = os.getenv("ENVIRONMENT", "development").strip().lower()
    if env == "production":
        return False
    allow_dev = os.getenv("ALLOW_DEV_AUTH", "false").strip().lower()
    return allow_dev in ("true", "1", "yes")


def get_current_user(request: Request) -> CurrentUser:
    """
    Xác thực người dùng từ Bearer token hoặc HttpOnly cookie qua common_auth.
    Chỉ hỗ trợ fallback X-User-Id khi ALLOW_DEV_AUTH=true và ENVIRONMENT != production.
    """
    auth_header = request.headers.get("Authorization")
    cookie_token = request.cookies.get(ACCESS_COOKIE)

    if not auth_header and not cookie_token:
        if _is_dev_auth_allowed():
            dev_user_id = request.headers.get("X-User-Id")
            if dev_user_id:
                logger.warning(
                    f"[SECURITY WARNING] Sử dụng DEV auth fallback X-User-Id={dev_user_id}. "
                    "Không được phép sử dụng trên Production!"
                )
                return CurrentUser(id=dev_user_id, role="citizen")

    return _common_get_current_user(request)
