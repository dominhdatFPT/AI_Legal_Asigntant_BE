"""Kiểm tra tài khoản còn dùng được không (khóa có thời hạn, tự mở khi hết hạn).

Khóa có thời hạn không cần job nền: mỗi lần đọc user ở luồng đăng nhập / API, nếu `locked_until`
đã qua thì mở khóa luôn rồi mới kiểm tra.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.responses import ACCOUNT_LOCKED, ApiError
from app.models import User, UserStatus

# Giờ Việt Nam để hiện thời hạn khóa cho người dùng (cố định +07:00, không cần gói tzdata trên Windows).
VN_TZ = timezone(timedelta(hours=7))


def as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def lock_expired(user: User, now: datetime | None = None) -> bool:
    """Đang ở trạng thái locked nhưng thời hạn khóa đã hết."""
    return (
        user.status == UserStatus.locked
        and user.locked_until is not None
        and as_utc(user.locked_until) <= (now or now_utc())
    )


def is_locked(user: User, now: datetime | None = None) -> bool:
    return user.status == UserStatus.locked and not lock_expired(user, now)


def clear_lock(user: User) -> None:
    user.status = UserStatus.active
    user.locked_reason = None
    user.locked_by = None
    user.locked_at = None
    user.locked_until = None


def release_expired_lock(db: Session, user: User, commit: bool = True) -> bool:
    """Hết hạn khóa thì mở khóa. Trả True nếu vừa mở.

    commit=False khi hàm gọi tự commit sau đó (luồng đăng nhập, refresh), để không cắt ngang transaction.
    """
    if not lock_expired(user):
        return False
    clear_lock(user)
    if commit:
        db.commit()
    else:
        db.flush()
    return True


def locked_message(user: User) -> str:
    parts = ["Tài khoản của bạn đã bị khóa"]
    if user.locked_until is not None:
        parts.append(" đến " + as_utc(user.locked_until).astimezone(VN_TZ).strftime("%H:%M %d/%m/%Y"))
    else:
        parts.append(" vĩnh viễn")
    message = "".join(parts) + "."
    if user.locked_reason:
        message += f" Lý do: {user.locked_reason}."
    return message + " Vui lòng liên hệ quản trị viên nếu cần hỗ trợ."


def ensure_not_locked(db: Session, user: User, commit: bool = True) -> None:
    """Mở khóa nếu đã hết hạn; còn bị khóa thì báo 403 ACCOUNT_LOCKED kèm thời hạn và lý do."""
    release_expired_lock(db, user, commit)
    if is_locked(user):
        raise ApiError(403, ACCOUNT_LOCKED, locked_message(user))
