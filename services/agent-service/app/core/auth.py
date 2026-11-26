import os
from functools import lru_cache
from typing import Literal

import jwt
from common_auth import revocation
from pydantic import BaseModel


class TokenPayload(BaseModel):
    sub: str                                     # user_id (UUID string)
    role: Literal["citizen", "lawyer", "admin"]
    type: Literal["access", "refresh"]
    iss: str
    iat: int
    exp: int
    username: str | None = None
    jti: str | None = None


class AuthError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 401):
        self.code = code
        self.message = message
        self.status_code = status_code


@lru_cache(maxsize=4)
def _read_public_key_file(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _load_public_key() -> str:
    """Ưu tiên JWT_PUBLIC_KEY (đồng nhất với common_auth), fallback sang file JWT_PUBLIC_KEY_PATH."""
    env_key = os.environ.get("JWT_PUBLIC_KEY")
    if env_key:
        return env_key.replace("\\n", "\n")
    path = os.environ.get("JWT_PUBLIC_KEY_PATH")
    if not path:
        keys_dir = os.environ.get("JWT_KEYS_DIR", "keys")
        path = os.path.join(keys_dir, "jwt_public.pem")
    return _read_public_key_file(path)


_ISSUER = "legal-assistant-user-service"


def decode_access_token(token: str) -> TokenPayload:
    """
    Verify JWT RS256, kiểm tra thu hồi (logout / block qua common_auth.revocation) và trả về TokenPayload.
    Raise AuthError nếu token không hợp lệ / hết hạn / đã bị thu hồi.
    """
    try:
        raw = jwt.decode(
            token,
            _load_public_key(),
            algorithms=["RS256"],
            issuer=_ISSUER,
            leeway=10,
            options={"require": ["sub", "role", "exp", "iss"]},
        )
    except jwt.ExpiredSignatureError:
        raise AuthError(code="TOKEN_EXPIRED", message="Phiên đăng nhập đã hết hạn.")
    except jwt.InvalidIssuerError:
        raise AuthError(code="UNAUTHORIZED", message="Token không hợp lệ.")
    except jwt.InvalidTokenError:
        raise AuthError(code="UNAUTHORIZED", message="Token không hợp lệ.")

    if raw.get("type") != "access":
        raise AuthError(code="UNAUTHORIZED", message="Chỉ access token được chấp nhận.")

    revocation.ensure_started()
    if revocation.is_revoked(raw):
        raise AuthError(code="UNAUTHORIZED", message="Phiên đăng nhập đã bị thu hồi hoặc tài khoản đã bị khoá.")

    return TokenPayload(**raw)
