"""API nội bộ về tài khoản (service gọi service, header X-Internal-Token). Gateway không chuyển tiếp /internal/..."""

import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.internal_auth import require_internal_token
from app.core.responses import USER_NOT_FOUND, ApiError, ok
from app.repositories import citizen_profile_repository as profiles
from app.repositories import user_repository as users
from app.schemas.admin_user import AdminUserOut, InternalChangeRoleRequest, InternalCreateUserRequest
from app.services import admin_user_service, user_status_service
from app.services.auth_service import parse_user_id

router = APIRouter(
    prefix="/internal/v1/users",
    tags=["Internal"],
    dependencies=[Depends(require_internal_token)],
)


def _admin_out(db: Session, user) -> AdminUserOut:
    has_profile = profiles.get_by_user_id(db, user.id) is not None
    return AdminUserOut.model_validate(user).model_copy(update={"has_citizen_profile": has_profile})


@router.get("/{user_id}/status")
def get_user_status_internal(user_id: str, request: Request, db: Session = Depends(get_db)):
    """Contract Service gọi trước khi tạo bộ hồ sơ: tài khoản còn dùng được không, đã khai hồ sơ CCCD chưa."""
    uid = parse_user_id(user_id)
    # get_by_id bỏ qua tài khoản đã xóa mềm → coi như không tồn tại.
    user = users.get_by_id(db, uid) if uid else None
    if user is None:
        raise ApiError(404, USER_NOT_FOUND)
    return ok(request, user_status_service.get_status(db, user))


@router.post("", status_code=201)
def create_lawyer_account_internal(body: InternalCreateUserRequest, request: Request, db: Session = Depends(get_db)):
    """Lawyer Service tạo tài khoản luật sư (admin tạo luật sư)."""
    user = admin_user_service.create_lawyer_account(db, body)
    return ok(request, _admin_out(db, user), status=201)


@router.delete("/{user_id}", status_code=204)
def hard_delete_user_internal(user_id: uuid.UUID, db: Session = Depends(get_db)):
    """Lawyer Service bù trừ khi lưu hồ sơ luật sư lỗi: xóa cứng tài khoản vừa tạo."""
    admin_user_service.hard_delete_lawyer_account(db, user_id)
    return Response(status_code=204)


@router.put("/{user_id}/role")
def change_role_internal(user_id: uuid.UUID, body: InternalChangeRoleRequest, request: Request,
                         db: Session = Depends(get_db)):
    """Lawyer Service đổi vai trò sang lawyer khi duyệt đơn."""
    return ok(request, _admin_out(db, admin_user_service.promote_to_lawyer(db, user_id)))
