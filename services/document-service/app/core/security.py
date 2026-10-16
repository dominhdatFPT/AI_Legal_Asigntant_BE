from uuid import UUID

from common_auth import CurrentUser
from common_auth.fastapi_dep import get_current_user
from fastapi import Depends, Request
from starlette.concurrency import run_in_threadpool

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


def get_user_status():
    from app.services.user_status import UserStatus
    return UserStatus.from_env()


async def document_writer(request: Request, user: CurrentUser = Depends(document_user), status=Depends(get_user_status)):
    # Always use the verified JWT subject, never a form/body/header user ID.
    await run_in_threadpool(status.ensure_document_write, user.id, request.state.request_id)
    return user
