"""Dependency dùng chung cho các API cần người dùng còn hoạt động / cần quyền admin.

Khác `common_auth.require_role` (chỉ đọc vai trò trong JWT): ở đây đọc lại user trong DB, nên người vừa bị
khóa, bị xóa hoặc bị hạ quyền admin bị chặn ngay ở mọi API của User Service, không chờ token hết hạn.
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from common_auth import CurrentUser
from common_auth.fastapi_dep import get_current_user

from app.core.database import get_db
from app.core.responses import FORBIDDEN, UNAUTHORIZED, ApiError
from app.models import User, UserRole
from app.repositories import user_repository as users
from app.services import account_guard
from app.services.auth_service import parse_user_id


def get_active_user(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
    """User đang đăng nhập, còn tồn tại (chưa xóa mềm) và không bị khóa."""
    user_id = parse_user_id(current.id)
    user = users.get_by_id(db, user_id) if user_id else None
    if user is None:
        raise ApiError(401, UNAUTHORIZED)
    account_guard.ensure_not_locked(db, user)
    return user


def require_admin(user: User = Depends(get_active_user)) -> User:
    if user.role != UserRole.admin:
        raise ApiError(403, FORBIDDEN)
    return user
