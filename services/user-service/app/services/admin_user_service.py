"""Quản lý tài khoản (chỉ admin): xem, tạo, khóa / mở khóa, đổi vai trò, xóa mềm / khôi phục.

Quy tắc chung:
- Admin không tự khóa, tự xóa, tự đổi vai trò của mình (nên hệ thống luôn còn ít nhất 1 admin).
- Khóa, xóa, đổi vai trò → thu hồi mọi refresh token: người đó không làm mới token được nữa, mọi API của
  User Service kiểm tra trạng thái trong DB nên chặn ngay; access token ở service khác hết hạn sau tối đa 15 phút.
- Luật sư bị khóa / xóa / hạ vai trò → kết thúc các cuộc tư vấn đang mở (Consultation Service).
"""

import math
import uuid
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clients import consultation_client
from app.core import passwords
from app.core.responses import (
    CANNOT_CHANGE_OWN_ROLE,
    CANNOT_DELETE_SELF,
    CANNOT_LOCK_SELF,
    EMAIL_DELETED,
    EMAIL_IN_USE,
    PHONE_IN_USE,
    ROLE_CHANGE_NOT_ALLOWED,
    USER_ALREADY_LOCKED,
    USER_DELETED,
    USER_NOT_DELETED,
    USER_NOT_FOUND,
    USER_NOT_LOCKED,
    ApiError,
)
from app.models import User, UserRole, UserStatus
from app.repositories import refresh_token_repository as refresh_tokens
from app.repositories import user_repository as users
from app.schemas.admin_user import AccountType, CreateUserRequest
from app.services import account_guard


def list_users(
    db: Session,
    *,
    role: UserRole | None,
    status: UserStatus | None,
    q: str | None,
    deleted: bool,
    page: int,
    page_size: int,
) -> tuple[list[User], dict, set[uuid.UUID]]:
    """Trả về (user của trang, phân trang, id những user đã khai hồ sơ CCCD)."""
    if users.release_expired_locks(db, account_guard.now_utc()):
        db.commit()
    items, total, with_profile = users.search(
        db, role=role, status=status, q=q, deleted=deleted, page=page, page_size=page_size
    )
    pagination = {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": math.ceil(total / page_size) if total else 0,
    }
    return items, pagination, with_profile


def get_user(db: Session, user_id: uuid.UUID) -> User:
    user = users.get_by_id_including_deleted(db, user_id)
    if user is None:
        raise ApiError(404, USER_NOT_FOUND)
    account_guard.release_expired_lock(db, user)
    return user


def _get_not_deleted(db: Session, user_id: uuid.UUID) -> User:
    user = get_user(db, user_id)
    if user.deleted_at is not None:
        raise ApiError(409, USER_DELETED)
    return user


def create_user(db: Session, admin: User, body: CreateUserRequest) -> User:
    """Admin cấp tài khoản đăng nhập bằng email + mật khẩu do admin đặt (người dùng đổi lại sau).

    Tài khoản cấp cho luật sư vẫn là `citizen` (đánh dấu created_as_lawyer) cho tới khi được duyệt hồ sơ.
    """
    existing = users.find_by_email_or_username(db, body.email)
    if existing is not None:
        raise ApiError(409, EMAIL_DELETED if existing.deleted_at is not None else EMAIL_IN_USE)
    if body.phone and users.phone_taken(db, body.phone):
        raise ApiError(409, PHONE_IN_USE)

    user = User(
        email=body.email,
        username=body.email,  # đăng nhập bằng email ở ô "tên đăng nhập"
        password_hash=passwords.hash_password(body.password),
        full_name=body.full_name or None,
        phone=body.phone,
        auth_provider="local",
        role=UserRole.admin if body.account_type == AccountType.admin else UserRole.citizen,
        status=UserStatus.active,
        created_by=admin.id,
        created_as_lawyer=body.account_type == AccountType.lawyer,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, EMAIL_IN_USE)
    return user


def promote_to_lawyer(db: Session, user_id: uuid.UUID) -> User:
    """Lawyer Service gọi khi admin duyệt hồ sơ luật sư. Đã là lawyer thì không đổi gì (gọi lại an toàn)."""
    user = _get_not_deleted(db, user_id)
    if user.role == UserRole.lawyer:
        return user
    if user.role != UserRole.citizen:
        raise ApiError(409, ROLE_CHANGE_NOT_ALLOWED)
    user.role = UserRole.lawyer
    # Token cũ mang vai trò citizen: thu hồi để lần đăng nhập sau nhận vai trò lawyer.
    refresh_tokens.revoke_all_for_user(db, user.id)
    db.commit()
    return user


def change_role(db: Session, admin: User, user_id: uuid.UUID, role: UserRole) -> User:
    if user_id == admin.id:
        raise ApiError(409, CANNOT_CHANGE_OWN_ROLE)
    user = _get_not_deleted(db, user_id)
    if user.role == role:
        return user

    was_lawyer = user.role == UserRole.lawyer
    user.role = role
    # Token cũ mang vai trò cũ: thu hồi để người đó đăng nhập lại nhận vai trò mới.
    refresh_tokens.revoke_all_for_user(db, user.id)
    db.commit()

    if was_lawyer:
        consultation_client.close_lawyer_consultations(user.id, reason="role_changed")
    return user


def lock_user(db: Session, admin: User, user_id: uuid.UUID, reason: str, duration_days: int | None) -> User:
    if user_id == admin.id:
        raise ApiError(409, CANNOT_LOCK_SELF)
    user = _get_not_deleted(db, user_id)
    if account_guard.is_locked(user):
        raise ApiError(409, USER_ALREADY_LOCKED)

    now = account_guard.now_utc()
    user.status = UserStatus.locked
    user.locked_reason = reason
    user.locked_by = admin.id
    user.locked_at = now
    user.locked_until = now + timedelta(days=duration_days) if duration_days else None
    refresh_tokens.revoke_all_for_user(db, user.id)
    db.commit()

    if user.role == UserRole.lawyer:
        consultation_client.close_lawyer_consultations(user.id, reason="locked")
    return user


def unlock_user(db: Session, user_id: uuid.UUID) -> User:
    user = _get_not_deleted(db, user_id)
    if user.status != UserStatus.locked:
        raise ApiError(409, USER_NOT_LOCKED)
    account_guard.clear_lock(user)
    db.commit()
    return user


def delete_user(db: Session, admin: User, user_id: uuid.UUID) -> None:
    """Xóa mềm: giữ dữ liệu, email không đăng ký lại được, admin khôi phục được."""
    if user_id == admin.id:
        raise ApiError(409, CANNOT_DELETE_SELF)
    user = _get_not_deleted(db, user_id)
    user.deleted_at = account_guard.now_utc()
    user.deleted_by = admin.id
    refresh_tokens.revoke_all_for_user(db, user.id)
    db.commit()

    if user.role == UserRole.lawyer:
        consultation_client.close_lawyer_consultations(user.id, reason="deleted")


def restore_user(db: Session, user_id: uuid.UUID) -> User:
    user = get_user(db, user_id)
    if user.deleted_at is None:
        raise ApiError(409, USER_NOT_DELETED)
    user.deleted_at = None
    user.deleted_by = None
    db.commit()
    return user
