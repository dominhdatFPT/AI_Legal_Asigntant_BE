import os
from dataclasses import dataclass

import jwt

ISSUER = "legal-assistant-user-service"
ALGORITHM = "RS256"

# Tên HttpOnly cookie chứa access token (user-service set khi đăng nhập / refresh).
ACCESS_COOKIE = "access_token"

UNAUTHORIZED = "UNAUTHORIZED"
TOKEN_EXPIRED = "TOKEN_EXPIRED"
FORBIDDEN = "FORBIDDEN"

MESSAGES = {
    UNAUTHORIZED: "Bạn cần đăng nhập để tiếp tục.",
    TOKEN_EXPIRED: "Phiên đăng nhập đã hết hạn.",
    FORBIDDEN: "Bạn không có quyền thực hiện thao tác này.",
}
STATUS = {UNAUTHORIZED: 401, TOKEN_EXPIRED: 401, FORBIDDEN: 403}


class AuthError(Exception):
    def __init__(self, code: str):
        super().__init__(MESSAGES[code])
        self.code = code
        self.status = STATUS[code]
        self.message = MESSAGES[code]


@dataclass(frozen=True)
class CurrentUser:
    id: str
    role: str


def _load_public_key() -> str:
    key = os.getenv("JWT_PUBLIC_KEY")
    if key:
        return key.replace("\\n", "\n")
    path = os.getenv("JWT_PUBLIC_KEY_PATH")
    if path:
        with open(path, encoding="utf-8") as f:
            return f.read()
    raise RuntimeError("Chưa cấu hình JWT_PUBLIC_KEY hoặc JWT_PUBLIC_KEY_PATH")


def extract_bearer(header_value: str | None) -> str:
    if not header_value or not header_value.startswith("Bearer "):
        raise AuthError(UNAUTHORIZED)
    token = header_value[len("Bearer "):].strip()
    if not token:
        raise AuthError(UNAUTHORIZED)
    return token


def extract_token(authorization: str | None, cookie_token: str | None) -> str:
    """Lấy access token theo thứ tự: header `Authorization: Bearer` trước, sau đó HttpOnly cookie.

    Có header mà sai định dạng thì báo 401 luôn, không âm thầm chuyển sang cookie.
    """
    if authorization:
        return extract_bearer(authorization)
    if cookie_token and cookie_token.strip():
        return cookie_token.strip()
    raise AuthError(UNAUTHORIZED)


def decode_access_token(token: str) -> CurrentUser:
    try:
        payload = jwt.decode(
            token,
            _load_public_key(),
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            leeway=10,
            options={"require": ["sub", "role", "exp", "iss"]},
        )
    except jwt.ExpiredSignatureError:
        raise AuthError(TOKEN_EXPIRED)
    except jwt.InvalidTokenError:
        raise AuthError(UNAUTHORIZED)

    if payload.get("type") != "access":
        raise AuthError(UNAUTHORIZED)

    return CurrentUser(id=str(payload["sub"]), role=str(payload["role"]))
