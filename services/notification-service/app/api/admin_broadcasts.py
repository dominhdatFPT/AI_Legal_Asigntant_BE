"""API Endpoints cho Admin phát thông báo tới toàn bộ người dùng trong hệ thống (và cả chính Admin đó)."""

from __future__ import annotations

import uuid
from typing import Optional

from common_auth import CurrentUser
from common_auth.fastapi_dep import require_role
from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from app.schemas.reports import BroadcastCreateRequest
from app.services import broadcast_service
from app.services.recipient_service import validate_recipients

router = APIRouter(
    prefix="/api/v1/admin/broadcasts",
    tags=["Admin Broadcasts"],
    dependencies=[Depends(require_role("admin"))],
)


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_system_broadcast(
    payload: BroadcastCreateRequest,
    request: Request,
    response: Response,
    admin: CurrentUser = Depends(require_role("admin")),
    x_request_id: str | None = Header(default=None),
):
    """Phát thông báo cho toàn bộ user trong hệ thống hoặc các người nhận cụ thể."""
    recipients = []
    if payload.recipient_ids:
        recipients = list(dict.fromkeys(str(r).strip() for r in payload.recipient_ids if r and str(r).strip()))
    elif payload.recipient_id is not None and payload.recipient_id.strip():
        recipients = [payload.recipient_id.strip()]

    recipient_labels = None
    if recipients:
        validated_users = await validate_recipients(recipients, payload.audience, request)
        recipient_labels = [
            u.get("full_name") or u.get("email") or str(u.get("id"))
            for u in validated_users
        ]

    data = await broadcast_service.create_broadcast(
        admin_id=admin.id,
        req=payload,
        recipient_labels=recipient_labels,
    )
    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    response.headers["X-Request-ID"] = request_id
    return {
        "data": data,
        "error": None,
        "request_id": request_id,
    }


@router.get("")
def list_system_broadcasts(
    request: Request,
    response: Response,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    page: Optional[int] = Query(default=None, ge=1),
    page_size: Optional[int] = Query(default=None, ge=1, le=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    x_request_id: str | None = Header(default=None),
):
    """Xem lịch sử các đợt phát thông báo toàn hệ thống và số lượt đã đọc."""
    effective_limit = page_size if page_size is not None else limit
    effective_offset = ((page - 1) * effective_limit) if page is not None else offset
    data = broadcast_service.list_broadcasts(
        status=status_filter,
        limit=effective_limit,
        offset=effective_offset,
    )
    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "private, no-store"
    return {
        "data": data,
        "error": None,
        "request_id": request_id,
    }


@router.get("/{broadcast_id}")
def get_system_broadcast_detail(
    broadcast_id: str,
    request: Request,
    response: Response,
    x_request_id: str | None = Header(default=None),
):
    """Xem chi tiết một đợt phát thông báo toàn hệ thống."""
    data = broadcast_service.get_broadcast_detail(broadcast_id=broadcast_id)
    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "private, no-store"
    return {
        "data": data,
        "error": None,
        "request_id": request_id,
    }


