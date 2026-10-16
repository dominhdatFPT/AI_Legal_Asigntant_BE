"""API quản lý tài khoản (màn hình "Quản lý người dùng" của admin).

Đường dẫn theo openapi.yaml (tag Admin). `{user_id:uuid}` dùng convertor uuid của Starlette để không nuốt
các route tĩnh như /users/me, /users/health, /users/summary.
"""

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_active_user, require_admin
from app.core.database import get_db
from app.core.responses import FORBIDDEN, ApiError, ok, request_id
from app.models import User, UserRole, UserStatus
from app.repositories import citizen_profile_repository as profiles
from app.repositories import user_repository as users
from app.schemas.admin_user import AdminUserOut, ChangeRoleRequest, CreateUserRequest, LockUserRequest
from app.schemas.user import UserOut
from app.services import account_guard
from app.services import admin_user_service as service
from app.services import profile_service

router = APIRouter(prefix="/api/v1/users", tags=["Admin"])


def _to_out(db: Session, items: list[User], with_profile: set[uuid.UUID] | None = None) -> list[AdminUserOut]:
    """with_profile: id những user đã khai CCCD nếu đã biết sẵn (danh sách), không thì hỏi DB."""
    if with_profile is None:
        with_profile = profiles.user_ids_with_profile(db, [u.id for u in items])
    return [AdminUserOut.model_validate(u).model_copy(update={"has_citizen_profile": u.id in with_profile})
            for u in items]


def _one(db: Session, user: User) -> AdminUserOut:
    return _to_out(db, [user])[0]


@router.get("")
def list_users(
    request: Request,
    role: UserRole | None = Query(default=None, description="Lọc theo vai trò"),
    status: UserStatus | None = Query(default=None, description="Lọc theo trạng thái"),
    q: str | None = Query(default=None, max_length=100, description="Tìm theo tên, email, tên đăng nhập, SĐT"),
    deleted: bool = Query(default=False, description="true = chỉ xem tài khoản đã xóa mềm (để khôi phục)"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    items, pagination, with_profile = service.list_users(
        db, role=role, status=status, q=q, deleted=deleted, page=page, page_size=page_size
    )
    return ok(request, {"items": _to_out(db, items, with_profile), "pagination": pagination})


@router.get("/summary")
def user_summary(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Số đếm cho các tab: tổng, theo vai trò, theo trạng thái, số tài khoản đã xóa."""
    if users.release_expired_locks(db, account_guard.now_utc()):
        db.commit()
    return ok(request, users.count_summary(db))


@router.post("", status_code=201)
def create_user(
    body: CreateUserRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = service.create_user(db, admin, body)
    return ok(request, _one(db, user), status=201)


@router.get("/{user_id:uuid}")
def get_user(
    user_id: uuid.UUID,
    request: Request,
    current: User = Depends(get_active_user),
    db: Session = Depends(get_db),
):
    """Admin xem được mọi người (kể cả đã xóa mềm); người khác chỉ xem được chính mình."""
    if current.role == UserRole.admin:
        return ok(request, _one(db, service.get_user(db, user_id)))
    if user_id != current.id:
        raise ApiError(403, FORBIDDEN)
    return ok(request, UserOut.model_validate(current))


@router.get("/{user_id:uuid}/citizen-profile")
def get_user_citizen_profile(
    user_id: uuid.UUID,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin xem hồ sơ CCCD của một người (màn hình chi tiết). Chưa khai hồ sơ thì `data` = null."""
    return ok(request, profile_service.get_profile(db, service.get_user(db, user_id)))


@router.put("/{user_id:uuid}/role")
def change_role(
    user_id: uuid.UUID,
    body: ChangeRoleRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return ok(request, _one(db, service.change_role(db, admin, user_id, body.role, request_id(request))))


@router.post("/{user_id:uuid}/lock")
def lock_user(
    user_id: uuid.UUID,
    body: LockUserRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return ok(request, _one(db, service.lock_user(db, admin, user_id, body.reason, body.duration_days)))


@router.post("/{user_id:uuid}/unlock")
def unlock_user(
    user_id: uuid.UUID,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return ok(request, _one(db, service.unlock_user(db, user_id)))


@router.delete("/{user_id:uuid}", status_code=204)
def delete_user(
    user_id: uuid.UUID,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    service.delete_user(db, admin, user_id, request_id(request))
    return Response(status_code=204)


@router.post("/{user_id:uuid}/restore")
def restore_user(
    user_id: uuid.UUID,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return ok(request, _one(db, service.restore_user(db, user_id)))
