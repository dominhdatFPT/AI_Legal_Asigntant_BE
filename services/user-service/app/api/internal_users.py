"""API nội bộ về tài khoản (service gọi service, header X-Internal-Token). Gateway không chuyển tiếp /internal/..."""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.internal_auth import require_internal_token
from app.core.responses import USER_NOT_FOUND, ApiError, ok
from app.repositories import user_repository as users
from app.repositories import citizen_profile_repository as profiles
from app.schemas.admin_user import AdminUserOut, InternalChangeRoleRequest
from app.services import admin_user_service, user_status_service
from app.services.auth_service import parse_user_id

router = APIRouter(
    prefix="/internal/v1/users",
    tags=["Internal"],
    dependencies=[Depends(require_internal_token)],
)


@router.get("/{user_id}/status")
def get_user_status_internal(user_id: str, request: Request, db: Session = Depends(get_db)):
    """Contract Service gọi trước khi tạo bộ hồ sơ: tài khoản còn dùng được không, đã khai hồ sơ CCCD chưa."""
    uid = parse_user_id(user_id)
    # get_by_id bỏ qua tài khoản đã xóa mềm → coi như không tồn tại.
    user = users.get_by_id(db, uid) if uid else None
    if user is None:
        raise ApiError(404, USER_NOT_FOUND)
    return ok(request, user_status_service.get_status(db, user))


@router.put("/{user_id}/role")
def change_role_internal(user_id: uuid.UUID, body: InternalChangeRoleRequest, request: Request,
                         db: Session = Depends(get_db)):
    """Lawyer Service đổi vai trò sang lawyer khi admin duyệt hồ sơ luật sư."""
    user = admin_user_service.promote_to_lawyer(db, user_id)
    has_profile = profiles.get_by_user_id(db, user.id) is not None
    return ok(request, AdminUserOut.model_validate(user).model_copy(update={"has_citizen_profile": has_profile}))
