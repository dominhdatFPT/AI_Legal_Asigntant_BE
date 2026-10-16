import hmac

from fastapi import Depends, Header, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth import AuthError, TokenPayload, decode_access_token
from app.core.config import get_settings

_bearer = HTTPBearer()


# ── Bearer JWT ─────────────────────────────────────────────────────────────────

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> TokenPayload:
    """
    Verify JWT và trả về payload.

    Ví dụ:
        @router.get("/documents")
        async def list_documents(
            user: TokenPayload = Depends(get_current_user),
        ):
            ...
    """
    try:
        return decode_access_token(credentials.credentials)
    except AuthError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.code, "message": e.message},
        )


async def require_admin(
    current_user: TokenPayload = Depends(get_current_user),
) -> TokenPayload:
    """
    Dependency cho các endpoint quản lý văn bản pháp luật.
    Chỉ role 'admin' mới được phép.
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "FORBIDDEN",
                "message": "Bạn không có quyền thực hiện thao tác này.",
            },
        )
    return current_user


# ── Internal token ─────────────────────────────────────────────────────────────

def require_internal_token(
    x_internal_token: str | None = Header(default=None),
) -> None:
    """
    Dependency cho /internal/v1/rag/search — chỉ agent-service được gọi.
    Giá trị lấy từ INTERNAL_TOKEN (biến môi trường hoặc .env).
    Header gửi lên: X-Internal-Token: <giá trị>
    """
    expected = get_settings().internal_token

    if not expected:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "INTERNAL_ERROR",
                "message": "Server chưa cấu hình INTERNAL_TOKEN.",
            },
        )

    # compare_digest: so sánh không để lộ độ dài khớp qua thời gian phản hồi
    if not x_internal_token or not hmac.compare_digest(x_internal_token, expected):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "UNAUTHORIZED",
                "message": "Internal token không hợp lệ.",
            },
        )
