"""Quản lý tài khoản (chỉ admin): xem, tạo, khóa / mở khóa, đổi vai trò, xóa mềm / khôi phục.

Quy tắc chung:
- Admin không tự khóa, tự xóa, tự đổi vai trò của mình (nên hệ thống luôn còn ít nhất 1 admin).
- Khóa, xóa, đổi vai trò → thu hồi mọi refresh token: người đó không làm mới token được nữa, mọi API của
  User Service kiểm tra trạng thái trong DB nên chặn ngay; access token ở service khác hết hạn sau tối đa 15 phút.
- Luật sư bị khóa / xóa / hạ vai trò → kết thúc các cuộc tư vấn đang mở (Consultation Service).
- Luật sư chỉ có qua Lawyer Service (admin tạo tài khoản luật sư / duyệt đơn): API đổi vai trò không cho lên lawyer.
  Hạ vai trò / xóa luật sư → gọi Lawyer Service ẩn hồ sơ TRƯỚC khi lưu; lỗi thì không đổi gì (502/503/504).
"""

import math
import uuid
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clients import consultation_client, lawyer_client
from app.core import passwords
from app.core.responses import (
    CANNOT_CHANGE_OWN_ROLE,
    CANNOT_DELETE_SELF,
    CANNOT_LOCK_SELF,
    EMAIL_DELETED,
    EMAIL_IN_USE,
    HARD_DELETE_NOT_ALLOWED,
    LAWYER_ROLE_VIA_LAWYER_SERVICE,
    PHONE_IN_USE,
    ROLE_CHANGE_NOT_ALLOWED,
    VALIDATION_ERROR,
    USER_ALREADY_LOCKED,
    USER_DELETED,
    USER_NOT_DELETED,
    USER_NOT_FOUND,
    USER_NOT_LOCKED,
    ApiError,
)
from app.models import User, UserRole, UserStatus
from app.repositories import citizen_profile_repository as profiles
from app.repositories import refresh_token_repository as refresh_tokens
from app.repositories import user_repository as users
from app.schemas.admin_user import AccountType, CreateUserRequest, InternalCreateUserRequest
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


def _create_local_account(db: Session, *, email: str, password: str, full_name: str | None, phone: str | None,
                          role: UserRole, created_by: uuid.UUID) -> User:
    existing = users.find_by_email_or_username(db, email)
    if existing is not None:
        raise ApiError(409, EMAIL_DELETED if existing.deleted_at is not None else EMAIL_IN_USE)
    if phone and users.phone_taken(db, phone):
        raise ApiError(409, PHONE_IN_USE)

    user = User(
        email=email,
        username=email,  # đăng nhập bằng email ở ô "tên đăng nhập"
        password_hash=passwords.hash_password(password),
        full_name=full_name or None,
        phone=phone,
        auth_provider="local",
        role=role,
        status=UserStatus.active,
        created_by=created_by,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, EMAIL_IN_USE)
    return user


def create_user(db: Session, admin: User, body: CreateUserRequest) -> User:
    """Admin cấp tài khoản đăng nhập bằng email + mật khẩu do admin đặt (người dùng đổi lại sau)."""
    role = UserRole.admin if body.account_type == AccountType.admin else UserRole.citizen
    return _create_local_account(db, email=body.email, password=body.password, full_name=body.full_name,
                                 phone=body.phone, role=role, created_by=admin.id)


def create_lawyer_account(db: Session, body: InternalCreateUserRequest) -> User:
    """Lawyer Service gọi khi admin tạo tài khoản luật sư: vai trò lawyer ngay, created_by = admin trong JWT."""
    creator = users.get_by_id(db, body.created_by)
    if creator is None or creator.role != UserRole.admin:
        raise ApiError(422, VALIDATION_ERROR, "created_by phải là một tài khoản admin đang hoạt động.")
    return _create_local_account(db, email=body.email, password=body.password, full_name=body.full_name,
                                 phone=body.phone, role=UserRole.lawyer, created_by=creator.id)


def hard_delete_lawyer_account(db: Session, user_id: uuid.UUID) -> None:
    """Bù trừ của Lawyer Service: xóa hẳn tài khoản vừa tạo (email đăng ký lại được).

    Chỉ cho tài khoản do admin tạo, chưa đăng nhập lần nào và chưa khai CCCD, để không bao giờ xóa nhầm dữ liệu thật.
    """
    user = users.get_by_id_including_deleted(db, user_id)
    if user is None:
        raise ApiError(404, USER_NOT_FOUND)
    if user.created_by is None or refresh_tokens.has_any(db, user.id) or profiles.get_by_user_id(db, user.id):
        raise ApiError(409, HARD_DELETE_NOT_ALLOWED)
    db.delete(user)
    db.commit()


def promote_to_lawyer(db: Session, user_id: uuid.UUID) -> User:
    """Lawyer Service gọi khi duyệt đơn xin làm luật sư. Đã là lawyer thì không đổi gì (gọi lại an toàn)."""
    user = get_user(db, user_id)
    if user.deleted_at is not None:
        raise ApiError(409, USER_DELETED)
    if user.role == UserRole.lawyer:
        return user
    if user.role != UserRole.citizen:
        raise ApiError(409, ROLE_CHANGE_NOT_ALLOWED)
    user.role = UserRole.lawyer
    refresh_tokens.revoke_all_for_user(db, user.id, reason=refresh_tokens.ROLE_CHANGED)
    db.commit()
    return user


def change_role(db: Session, admin: User, user_id: uuid.UUID, role: UserRole, request_id: str | None = None) -> User:
    if user_id == admin.id:
        raise ApiError(409, CANNOT_CHANGE_OWN_ROLE)
    user = _get_not_deleted(db, user_id)
    if user.role == role:
        return user
    if role == UserRole.lawyer:
        raise ApiError(409, LAWYER_ROLE_VIA_LAWYER_SERVICE)

    was_lawyer = user.role == UserRole.lawyer
    if was_lawyer:
        # Ẩn hồ sơ luật sư trước; Lawyer Service lỗi thì ném 502/503/504, vai trò chưa đổi.
        lawyer_client.hide_lawyer_profile(user.id, "role_changed", request_id)
    user.role = role
    # Token cũ mang vai trò cũ: thu hồi để người đó đăng nhập lại nhận vai trò mới.
    refresh_tokens.revoke_all_for_user(db, user.id, reason=refresh_tokens.ROLE_CHANGED)
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


def delete_user(db: Session, admin: User, user_id: uuid.UUID, request_id: str | None = None) -> None:
    """Xóa mềm: giữ dữ liệu, email không đăng ký lại được, admin khôi phục được."""
    if user_id == admin.id:
        raise ApiError(409, CANNOT_DELETE_SELF)
    user = _get_not_deleted(db, user_id)
    if user.role == UserRole.lawyer:
        lawyer_client.hide_lawyer_profile(user.id, "deleted", request_id)
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
