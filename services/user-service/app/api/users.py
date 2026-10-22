from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from common_auth import CurrentUser
from common_auth.fastapi_dep import get_current_user

from app.core import token_blacklist
from app.core.database import get_db
from app.core.responses import UNAUTHORIZED, ApiError, ok
from app.repositories import user_repository as users
from app.schemas.user import UserOut
from app.services import account_guard
from app.services.auth_service import parse_user_id

router = APIRouter(prefix="/api/v1/users", tags=["Profile"])


@router.get("/me")
def get_my_profile(
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current.jti and token_blacklist.is_token_blacklisted(current.jti):
        raise ApiError(401, UNAUTHORIZED)
    user_id = parse_user_id(current.id)
    user = users.get_by_id(db, user_id) if user_id else None
    if user is None:
        raise ApiError(401, UNAUTHORIZED)
    account_guard.ensure_not_locked(db, user)
    return ok(request, UserOut.model_validate(user))
