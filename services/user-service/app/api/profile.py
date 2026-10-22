from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from common_auth import CurrentUser
from common_auth.fastapi_dep import get_current_user

from app.core import token_blacklist
from app.core.database import get_db
from app.core.internal_auth import require_internal_token
from app.core.responses import UNAUTHORIZED, USER_NOT_FOUND, ApiError, ok
from app.models import User
from app.repositories import user_repository as users
from app.schemas.citizen_profile import CitizenProfileRequest
from app.services import account_guard, profile_service
from app.services.auth_service import parse_user_id

router = APIRouter(prefix="/api/v1/users/me/citizen-profile", tags=["Profile"])
internal_router = APIRouter(prefix="/internal/v1/users", tags=["Internal"])


def _active_user(current: CurrentUser, db: Session) -> User:
    """User đang đăng nhập, còn tồn tại và không bị khóa (giống GET /users/me)."""
    if current.jti and token_blacklist.is_token_blacklisted(current.jti):
        raise ApiError(401, UNAUTHORIZED)
    user_id = parse_user_id(current.id)
    user = users.get_by_id(db, user_id) if user_id else None
    if user is None:
        raise ApiError(401, UNAUTHORIZED)
    account_guard.ensure_not_locked(db, user)
    return user


@router.get("")
def get_my_citizen_profile(
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current.jti and token_blacklist.is_token_blacklisted(current.jti):
        raise ApiError(401, UNAUTHORIZED)
    # Lấy user và hồ sơ trong 1 câu query (DB ở cloud, bớt 1 lượt hỏi-đáp); kiểm tra giống _active_user.
    user_id = parse_user_id(current.id)
    found = users.get_with_citizen_profile(db, user_id) if user_id else None
    if found is None:
        raise ApiError(401, UNAUTHORIZED)
    user, profile = found
    account_guard.ensure_not_locked(db, user)
    return ok(request, profile_service.to_out(profile, user) if profile else None)


@router.put("")
def save_my_citizen_profile(
    body: CitizenProfileRequest,
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = _active_user(current, db)
    return ok(request, profile_service.save_profile(db, user, body))


@internal_router.get("/{user_id}/citizen-profile", dependencies=[Depends(require_internal_token)])
def get_citizen_profile_internal(user_id: str, request: Request, db: Session = Depends(get_db)):
    uid = parse_user_id(user_id)
    user = users.get_by_id(db, uid) if uid else None
    if user is None:
        raise ApiError(404, USER_NOT_FOUND)
    return ok(request, profile_service.get_profile(db, user))
