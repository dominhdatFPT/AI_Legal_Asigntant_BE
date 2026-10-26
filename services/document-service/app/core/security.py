from uuid import UUID

from common_auth import CurrentUser
from common_auth.fastapi_dep import get_current_user
from fastapi import Depends, Request

from app.core.config import ConfigurationError, Settings
from app.core.errors import DocumentError


def document_user(request: Request, user: CurrentUser = Depends(get_current_user)):
    try:
        owner_id = str(UUID(user.id))
    except (ValueError, TypeError, AttributeError):
        raise DocumentError("UNAUTHORIZED", "Access token không hợp lệ.", 401) from None
    if user.role not in {"citizen", "lawyer", "admin"}:
        raise DocumentError("FORBIDDEN", "Bạn không có quyền thực hiện thao tác này.", 403)
    # Bearer takes precedence in common_auth. Cookie mutations need a trusted Origin.
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not request.headers.get("Authorization"):
        try:
            origins = Settings.allowed_origins()
        except ConfigurationError:
            raise DocumentError("CONFIGURATION_ERROR", "Cấu hình Document chưa sẵn sàng.") from None
        if request.headers.get("Origin") not in origins:
            raise DocumentError("FORBIDDEN", "Nguồn yêu cầu không được phép.", 403)
    return CurrentUser(id=owner_id, role=user.role)


async def document_writer(request: Request, user: CurrentUser = Depends(document_user)):
    # Always use the verified JWT subject, never a form/body/header user ID.
    return user
