import os
from functools import lru_cache
from typing import Literal

import jwt
from pydantic import BaseModel


class TokenPayload(BaseModel):
    sub: str                                     # user_id (UUID string)
    role: Literal["citizen", "lawyer", "admin"]
    type: Literal["access", "refresh"]
    iss: str
    iat: int
    exp: int


class AuthError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 401):
        self.code = code
        self.message = message
        self.status_code = status_code


@lru_cache(maxsize=1)
def _load_public_key() -> str:
    """Đọc public key từ file, cache lại sau lần đầu (restart service để reload)."""
    path = os.environ.get("JWT_PUBLIC_KEY_PATH")
    if not path:
        keys_dir = os.environ.get("JWT_KEYS_DIR", "keys")
        path = os.path.join(keys_dir, "jwt_public.pem")
    with open(path) as f:
        return f.read()


_ISSUER = "legal-assistant-user-service"


def decode_access_token(token: str) -> TokenPayload:
    """
    Verify JWT RS256 và trả về TokenPayload.
    Raise AuthError nếu token không hợp lệ / hết hạn.
    """
    try:
        raw = jwt.decode(
            token,
            _load_public_key(),
            algorithms=["RS256"],
            issuer=_ISSUER,
        )
    except jwt.ExpiredSignatureError:
        raise AuthError(code="TOKEN_EXPIRED", message="Phiên đăng nhập đã hết hạn.")
    except jwt.InvalidIssuerError:
        raise AuthError(code="UNAUTHORIZED", message="Token không hợp lệ.")
    except jwt.InvalidTokenError:
        raise AuthError(code="UNAUTHORIZED", message="Token không hợp lệ.")

    if raw.get("type") != "access":
        raise AuthError(code="UNAUTHORIZED", message="Chỉ access token được chấp nhận.")

    return TokenPayload(**raw)
