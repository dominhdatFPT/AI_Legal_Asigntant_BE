"""Người gọi lấy từ JWT (`sub`, `role`) qua libs/common_auth; không tin user_id do client gửi."""

import uuid
from dataclasses import dataclass

from fastapi import Depends

from common_auth import CurrentUser
from common_auth.fastapi_dep import get_current_user

from app.core.responses import FORBIDDEN, UNAUTHORIZED, ApiError


@dataclass(frozen=True)
class Caller:
    id: uuid.UUID
    role: str


def get_caller(current: CurrentUser = Depends(get_current_user)) -> Caller:
    try:
        return Caller(uuid.UUID(current.id), current.role)
    except (ValueError, TypeError, AttributeError):
        raise ApiError(401, UNAUTHORIZED) from None


def require_admin(caller: Caller = Depends(get_caller)) -> Caller:
    if caller.role != "admin":
        raise ApiError(403, FORBIDDEN)
    return caller
