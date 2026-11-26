from fastapi import Cookie, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth import AuthError, TokenPayload, decode_access_token

_bearer = HTTPBearer(auto_error=False)


async def raw_access_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    access_token: str | None = Cookie(default=None),
) -> str | None:
    """Token thô của người dùng để chuyển tiếp cho service khác. Không báo lỗi khi thiếu:
    chỉ endpoint thật sự cần token mới kiểm tra, các endpoint khác chạy như cũ."""
    if request.headers.get("Authorization"):
        return credentials.credentials if credentials else None
    return access_token


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    access_token: str | None = Cookie(default=None),
) -> TokenPayload:
    # Bearer header trước, cookie HttpOnly làm fallback (FE dùng credentials: "include").
    # Có Authorization header nhưng sai định dạng thì từ chối 401 ngay, không âm thầm fallback sang cookie.
    if request.headers.get("Authorization"):
        token = credentials.credentials if credentials else None
    else:
        token = access_token
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
