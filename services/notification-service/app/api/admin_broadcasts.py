"""API Endpoints cho Admin phát thông báo tới toàn bộ người dùng trong hệ thống (và cả chính Admin đó)."""

from __future__ import annotations

import uuid
from typing import Optional

from common_auth import CurrentUser
from common_auth.fastapi_dep import require_role
from fastapi import APIRouter, Depends, Header, Query, status

from app.schemas.reports import BroadcastCreateRequest
from app.services import broadcast_service

router = APIRouter(
    prefix="/api/v1/admin/broadcasts",
    tags=["Admin Broadcasts"],
    dependencies=[Depends(require_role("admin"))],
)


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_system_broadcast(
    payload: BroadcastCreateRequest,
    admin: CurrentUser = Depends(require_role("admin")),
    x_request_id: str | None = Header(default=None),
):
    """Phát thông báo cho toàn bộ user trong hệ thống (citizen, lawyer và cả chính admin đó khi audience='all')."""
    data = await broadcast_service.create_broadcast(admin_id=admin.id, req=payload)
    return {
        "data": data,
        "error": None,
        "request_id": x_request_id or f"req-{uuid.uuid4()}",
    }


@router.get("")
def list_system_broadcasts(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    x_request_id: str | None = Header(default=None),
):
    """Xem lịch sử các đợt phát thông báo toàn hệ thống và số lượt đã đọc."""
    data = broadcast_service.list_broadcasts(status=status_filter, limit=limit, offset=offset)
    return {
        "data": data,
        "error": None,
        "request_id": x_request_id or f"req-{uuid.uuid4()}",
    }


@router.get("/{broadcast_id}")
def get_system_broadcast_detail(
    broadcast_id: str,
    x_request_id: str | None = Header(default=None),
):
    """Xem chi tiết một đợt phát thông báo toàn hệ thống."""
    data = broadcast_service.get_broadcast_detail(broadcast_id=broadcast_id)
    return {
        "data": data,
        "error": None,
        "request_id": x_request_id or f"req-{uuid.uuid4()}",
    }


@router.post("/{broadcast_id}/cancel")
def cancel_system_broadcast(
    broadcast_id: str,
    x_request_id: str | None = Header(default=None),
):
    """Hủy thông báo toàn hệ thống đang ở trạng thái hẹn giờ ('scheduled')."""
    data = broadcast_service.cancel_broadcast(broadcast_id=broadcast_id)
    return {
        "data": data,
        "error": None,
        "request_id": x_request_id or f"req-{uuid.uuid4()}",
    }
