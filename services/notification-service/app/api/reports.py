"""API Endpoints cho Người dùng (citizen) & Luật sư (lawyer) tạo và quản lý Report (Ticket & Quick Error)."""

from __future__ import annotations

import uuid
from typing import Optional

from common_auth import CurrentUser
from common_auth.fastapi_dep import get_current_user, require_role
from fastapi import APIRouter, Depends, File, Header, Query, Response, UploadFile, status

from app.schemas.reports import QuickErrorCreateRequest, TicketCreateRequest
from app.services import report_service

router = APIRouter(prefix="/api/v1/reports", tags=["Reports (User & Lawyer)"])


@router.get("/catalog")
def get_catalog(
    kind: Optional[str] = Query(default=None, description="Lọc theo 'ticket' hoặc 'quick_error'"),
    user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Lấy danh mục nhóm vấn đề (categories) và nguyên nhân (reasons) theo vai trò người dùng."""
    data = report_service.get_report_catalog(user_role=user.role, kind=kind)
    return {
        "data": data,
        "error": None,
        "request_id": x_request_id or f"req-{uuid.uuid4()}",
    }


@router.post("/attachments", status_code=status.HTTP_201_CREATED)
async def upload_report_attachment(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_role("citizen", "lawyer")),
    x_request_id: str | None = Header(default=None),
):
    """Tải lên ảnh chụp màn hình đính kèm cho báo cáo (tối đa 5MB, PNG/JPEG/WEBP)."""
    content = await file.read()
    att = report_service.save_attachment(
        uploader_id=user.id,
        file_name=file.filename or "screenshot.png",
        mime_type=file.content_type or "image/png",
        content=content,
    )
    return {
        "data": att,
        "error": None,
        "request_id": x_request_id or f"req-{uuid.uuid4()}",
    }


@router.post("/tickets", status_code=status.HTTP_201_CREATED)
async def submit_ticket_report(
    payload: TicketCreateRequest,
    user: CurrentUser = Depends(require_role("citizen", "lawyer")),
    x_request_id: str | None = Header(default=None),
):
    """Cách 1: Tạo Ticket Report cho một vấn đề trong hệ thống."""
    report_data = await report_service.create_ticket_report(
        reporter_id=user.id,
        reporter_role=user.role,
        req=payload,
    )
    return {
        "data": report_data,
        "error": None,
        "request_id": x_request_id or f"req-{uuid.uuid4()}",
    }


@router.post("/quick-errors", status_code=status.HTTP_201_CREATED)
async def submit_quick_error_report(
    payload: QuickErrorCreateRequest,
    response: Response,
    user: CurrentUser = Depends(require_role("citizen", "lawyer")),
    x_request_id: str | None = Header(default=None),
):
    """Cách 2: Bấm vào chữ 'Báo lỗi' ngay trong luồng thao tác để gửi lỗi nhanh lên cho Admin."""
    report_data, duplicated = await report_service.create_quick_error_report(
        reporter_id=user.id,
        reporter_role=user.role,
        req=payload,
    )
    if duplicated:
        response.status_code = status.HTTP_200_OK
    return {
        "data": report_data,
        "error": None,
        "request_id": x_request_id or f"req-{uuid.uuid4()}",
    }


@router.get("/mine")
def list_my_reports(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    kind: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: CurrentUser = Depends(require_role("citizen", "lawyer")),
    x_request_id: str | None = Header(default=None),
):
    """Xem danh sách các báo cáo do chính mình đã gửi."""
    data = report_service.list_my_reports(
        reporter_id=user.id,
        status=status_filter,
        kind=kind,
        limit=limit,
        offset=offset,
    )
    return {
        "data": data,
        "error": None,
        "request_id": x_request_id or f"req-{uuid.uuid4()}",
    }


@router.get("/{report_id}")
def get_my_report_detail(
    report_id: str,
    user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Xem chi tiết một báo cáo (kèm lịch sử xử lý và phản hồi của Admin)."""
    data = report_service.get_report_detail(
        report_id=report_id,
        viewer_id=user.id,
        viewer_role=user.role,
    )
    return {
        "data": data,
        "error": None,
        "request_id": x_request_id or f"req-{uuid.uuid4()}",
    }


@router.post("/{report_id}/cancel")
def cancel_report(
    report_id: str,
    user: CurrentUser = Depends(require_role("citizen", "lawyer")),
    x_request_id: str | None = Header(default=None),
):
    """Hủy báo cáo của mình khi đang ở trạng thái chờ xử lý ('pending')."""
    data = report_service.cancel_my_report(
        report_id=report_id,
        reporter_id=user.id,
        reporter_role=user.role,
    )
    return {
        "data": data,
        "error": None,
        "request_id": x_request_id or f"req-{uuid.uuid4()}",
    }
