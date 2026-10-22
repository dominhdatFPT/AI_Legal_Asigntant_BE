import os
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import firebase, passwords, security
from app.core.responses import (
    ACCOUNT_DELETED,
    ACCOUNT_LOCKED,
    EMAIL_IN_USE,
    EMAIL_NOT_VERIFIED,
    INVALID_CREDENTIALS,
    INVALID_FIREBASE_TOKEN,
    INVALID_REFRESH_TOKEN,
    PHONE_IN_USE,
    ApiError,
)
from app.models import User, UserRole, UserStatus
from app.repositories import refresh_token_repository as refresh_tokens
from app.repositories import user_repository as users
from app.schemas.auth import RegisterRequest
from app.services import account_guard


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def issue_token_pair(db: Session, user: User, user_agent: str | None = None) -> dict:
    raw, token_hash, expires_at = security.new_refresh_token()
    refresh_tokens.add(db, user_id=user.id, token_hash=token_hash, expires_at=expires_at, user_agent=user_agent)
    access_token = security.create_access_token(str(user.id), user.role.value)
    return security.token_pair(access_token, raw)


def require_email_verification() -> bool:
    """Mặc định KHÔNG bắt xác minh email (dự án không dùng dịch vụ gửi email). Bật lại bằng REQUIRE_EMAIL_VERIFICATION=true."""
    return os.getenv("REQUIRE_EMAIL_VERIFICATION", "false").strip().lower() in {"1", "true", "yes"}


def login_with_password(db: Session, username: str, password: str, user_agent: str | None = None) -> tuple[User, dict]:
    user = users.get_by_username(db, username)
    if not passwords.verify_password(password, user.password_hash if user else None) or user is None:
        raise ApiError(401, INVALID_CREDENTIALS)
    account_guard.ensure_not_locked(db, user, commit=False)

    pair = issue_token_pair(db, user, user_agent)
    db.commit()
    return user, pair


def register(db: Session, body: RegisterRequest, user_agent: str | None = None) -> tuple[User, dict]:
    """Tự đăng ký tài khoản `local` (đăng nhập bằng email + mật khẩu) rồi đăng nhập luôn."""
    existing = users.find_by_email_or_username(db, body.email)
    if existing is not None:
        raise ApiError(403, ACCOUNT_DELETED) if existing.deleted_at is not None else ApiError(409, EMAIL_IN_USE)
    if users.phone_taken(db, body.phone):
        raise ApiError(409, PHONE_IN_USE)

    user = User(
        email=body.email,
        username=body.email,
        password_hash=passwords.hash_password(body.password),
        phone=body.phone,
        auth_provider="local",
        role=UserRole.citizen,
        status=UserStatus.active,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        # Hai người đăng ký cùng email / SĐT gần như cùng lúc: unique index chặn người sau.
        db.rollback()
        raise ApiError(409, EMAIL_IN_USE)

    pair = issue_token_pair(db, user, user_agent)
    db.commit()
    return user, pair


def login_with_firebase(db: Session, firebase_token: str, user_agent: str | None = None) -> tuple[User, dict, bool]:
    identity = firebase.verify_id_token(firebase_token)
    if not identity.email:
        raise ApiError(401, INVALID_FIREBASE_TOKEN)
    if identity.provider == "password" and require_email_verification() and not identity.email_verified:
        raise ApiError(403, EMAIL_NOT_VERIFIED)

    user = users.get_by_firebase_uid(db, identity.uid)
    is_new_user = user is None
    linkable = users.get_by_email(db, identity.email) if is_new_user and identity.email_verified else None
    if linkable is not None and linkable.firebase_uid is None:
        # Tự liên kết tài khoản đăng ký form với Firebase cùng email (email đã được Firebase xác minh).
        # Form không xác minh email nên người đăng ký trước có thể không phải chủ email:
        # xóa mật khẩu và thu hồi mọi phiên cũ để họ không còn vào được.
        user, is_new_user = linkable, False
        user.firebase_uid = identity.uid
        user.password_hash = None
        refresh_tokens.revoke_all_for_user(db, user.id)
    elif is_new_user:
        # Email / tài khoản Google đã bị admin xóa mềm thì không tự đăng ký lại; admin khôi phục được.
        if users.find_deleted(db, firebase_uid=identity.uid, email=identity.email) is not None:
            raise ApiError(403, ACCOUNT_DELETED)
        if users.get_by_email(db, identity.email) is not None:
            raise ApiError(409, EMAIL_IN_USE)
        try:
            user = users.create(
                db,
                firebase_uid=identity.uid,
                email=identity.email,
                full_name=identity.name,
                avatar_url=identity.picture,
                auth_provider=identity.provider,
            )
        except IntegrityError:
            db.rollback()
            user = users.get_by_firebase_uid(db, identity.uid)
            if user is None:
                raise ApiError(409, EMAIL_IN_USE)
            is_new_user = False

    if account_guard.is_locked(user):
        message = account_guard.locked_message(user)
        db.rollback()
        raise ApiError(403, ACCOUNT_LOCKED, message)
    account_guard.release_expired_lock(db, user, commit=False)

    pair = issue_token_pair(db, user, user_agent)
    db.commit()
    return user, pair, is_new_user


def refresh(db: Session, raw_token: str, user_agent: str | None = None) -> dict:
    token = refresh_tokens.get_by_hash(db, security.hash_refresh_token(raw_token), for_update=True)
    if token is None:
        raise ApiError(401, INVALID_REFRESH_TOKEN)

    if token.revoked_at is not None:
        refresh_tokens.revoke_all_for_user(db, token.user_id)
        db.commit()
        raise ApiError(401, INVALID_REFRESH_TOKEN)

    if _as_utc(token.expires_at) <= datetime.now(timezone.utc):
        raise ApiError(401, INVALID_REFRESH_TOKEN)

    user = users.get_by_id(db, token.user_id)
    if user is None:
        refresh_tokens.revoke_all_for_user(db, token.user_id)
        db.commit()
        raise ApiError(401, INVALID_REFRESH_TOKEN)

    if account_guard.is_locked(user):
        refresh_tokens.revoke_all_for_user(db, user.id)
        db.commit()
        raise ApiError(403, ACCOUNT_LOCKED, account_guard.locked_message(user))
    account_guard.release_expired_lock(db, user, commit=False)

    refresh_tokens.revoke(db, token)
    pair = issue_token_pair(db, user, user_agent)
    db.commit()
    return pair


def logout(db: Session, raw_token: str, current_user_id: str) -> None:
    token = refresh_tokens.get_by_hash(db, security.hash_refresh_token(raw_token))
    if token is not None and str(token.user_id) == current_user_id:
        refresh_tokens.revoke(db, token)
        db.commit()


def parse_user_id(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None
