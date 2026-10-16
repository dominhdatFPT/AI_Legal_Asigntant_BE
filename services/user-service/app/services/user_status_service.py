"""Trạng thái tài khoản cho API nội bộ GET /internal/v1/users/{user_id}/status.

Gom luật "được tạo bộ hồ sơ hay không" về một chỗ ở User Service, để Contract Service chỉ cần đọc
`can_create_dossier` / `block_reason`, không phải tự lặp lại luật khóa tài khoản, duyệt hồ sơ.
"""

from sqlalchemy.orm import Session

from app.models import User
from app.repositories import citizen_profile_repository as profiles
from app.schemas.user_status import UserStatusOut
from app.services import account_guard

PROFILE_NOT_SUBMITTED_MESSAGE = "Bạn cần khai hồ sơ cá nhân (CCCD) trước khi tạo bộ hồ sơ."


def get_status(db: Session, user: User) -> UserStatusOut:
    # Khóa đã hết hạn thì mở luôn, giống luồng đăng nhập (account_guard).
    account_guard.release_expired_lock(db, user)
    locked = account_guard.is_locked(user)
    has_profile = profiles.get_by_user_id(db, user.id) is not None

    if locked:
        block_reason, block_message = "ACCOUNT_LOCKED", account_guard.locked_message(user)
    elif not has_profile:
        block_reason, block_message = "PROFILE_NOT_SUBMITTED", PROFILE_NOT_SUBMITTED_MESSAGE
    else:
        block_reason, block_message = None, None

    return UserStatusOut(
        user_id=user.id,
        role=user.role,
        status=user.status,
        is_locked=locked,
        locked_until=user.locked_until if locked else None,
        has_citizen_profile=has_profile,
        profile_status="approved" if has_profile else "not_submitted",
        can_create_dossier=block_reason is None,
        block_reason=block_reason,
        block_message=block_message,
    )
