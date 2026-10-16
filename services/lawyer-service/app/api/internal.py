"""API nội bộ (service gọi service, X-Internal-Token). Gateway không chuyển tiếp /internal/..."""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.internal_auth import require_internal_token
from app.core.responses import ok
from app.schemas.lawyer import HideRequest
from app.services import profile_service

router = APIRouter(prefix="/internal/v1/lawyers", tags=["Internal"], dependencies=[Depends(require_internal_token)])


@router.get("/{user_id}/status")
def lawyer_status(user_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    """Consultation Service: người này dùng được chức năng luật sư chưa."""
    return ok(request, profile_service.lawyer_status(db, user_id))


@router.post("/{user_id}/hide")
def hide_profile(user_id: uuid.UUID, body: HideRequest, request: Request, db: Session = Depends(get_db)):
    """User Service gọi trước khi hạ vai trò lawyer / xóa tài khoản luật sư."""
    return ok(request, profile_service.hide_profile(db, user_id, body.reason))
