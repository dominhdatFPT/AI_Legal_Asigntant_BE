from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth import AuthError, TokenPayload, decode_access_token

_bearer = HTTPBearer(auto_error=False)  # thiếu token thì tự trả 401 theo openapi


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> TokenPayload:
    """
    Verify JWT và trả về payload.
    Dùng làm dependency trong mọi endpoint cần đăng nhập.

    Ví dụ:
        @router.get("/conversations")
        async def list_conversations(
            user: TokenPayload = Depends(get_current_user),
        ):
            ...
    """
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "Bạn cần đăng nhập để tiếp tục."},
        )
    try:
        return decode_access_token(credentials.credentials)
    except AuthError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "message": e.message},
        )
