from fastapi import Cookie, Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth import AuthError, TokenPayload, decode_access_token

_bearer = HTTPBearer(auto_error=False)


async def raw_access_token(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    access_token: str | None = Cookie(default=None),
) -> str | None:
    """Token thô của người dùng để chuyển tiếp cho service khác. Không báo lỗi khi thiếu:
    chỉ endpoint thật sự cần token mới kiểm tra, các endpoint khác chạy như cũ."""
    return credentials.credentials if credentials else access_token


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    access_token: str | None = Cookie(default=None),
) -> TokenPayload:
    # Bearer header trước, cookie HttpOnly làm fallback (FE dùng credentials: "include")
    token = credentials.credentials if credentials else access_token
    if not token:
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "Bạn cần đăng nhập để tiếp tục."},
        )
    try:
        return decode_access_token(token)
    except AuthError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "message": e.message},
        )
