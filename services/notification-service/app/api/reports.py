"""API Endpoints cho Người dùng (citizen) & Luật sư (lawyer) tạo và quản lý Report (Ticket & Quick Error)."""

from __future__ import annotations

import asyncio
import uuid
from typing import Optional

from common_auth import CurrentUser
from common_auth.fastapi_dep import get_current_user, require_role
from fastapi import APIRouter, Depends, File, Header, Query, Request, Response, UploadFile, status

from app.schemas.reports import QuickErrorCreateRequest, TicketCreateRequest
from app.services import report_service

router = APIRouter(prefix="/api/v1/reports", tags=["Reports (User & Lawyer)"])


@router.get("/catalog")
def get_catalog(
    request: Request,
    response: Response,
    kind: Optional[str] = Query(default=None, description="Lọc theo 'ticket' hoặc 'quick_error'"),
    user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Lấy danh mục nhóm vấn đề (categories) và nguyên nhân (reasons) theo vai trò người dùng."""
    data = report_service.get_report_catalog(user_role=user.role, kind=kind)
    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "private, no-store"
    return {
        "data": data,
        "error": None,
        "request_id": request_id,
    }


@router.post("/attachments", status_code=status.HTTP_201_CREATED)
async def upload_report_attachment(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_role("citizen", "lawyer")),
    x_request_id: str | None = Header(default=None),
):
    """Tải lên ảnh chụp màn hình đính kèm cho báo cáo (tối đa 5MB, PNG/JPEG/WEBP)."""
    content = await file.read(report_service.MAX_ATTACHMENT_BYTES + 1)
    att = await asyncio.to_thread(
        report_service.save_attachment,
        uploader_id=user.id,
        file_name=file.filename or "screenshot.png",
        mime_type=file.content_type or "image/png",
        content=content,
    )
    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    response.headers["X-Request-ID"] = request_id
    return {
        "data": att,
        "error": None,
        "request_id": request_id,
    }


@router.post("/tickets", status_code=status.HTTP_201_CREATED)
async def submit_ticket_report(
    payload: TicketCreateRequest,
    request: Request,
    response: Response,
    user: CurrentUser = Depends(require_role("citizen", "lawyer")),
    x_request_id: str | None = Header(default=None),
):
    """Cách 1: Tạo Ticket Report cho một vấn đề trong hệ thống."""
    report_data = await report_service.create_ticket_report(
        reporter_id=user.id,
        reporter_role=user.role,
        req=payload,
    )
    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    response.headers["X-Request-ID"] = request_id
    return {
        "data": report_data,
        "error": None,
        "request_id": request_id,
    }


@router.post("/quick-errors", status_code=status.HTTP_201_CREATED)
async def submit_quick_error_report(
    payload: QuickErrorCreateRequest,
    request: Request,
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
    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    response.headers["X-Request-ID"] = request_id
    return {
        "data": report_data,
        "error": None,
        "request_id": request_id,
    }


@router.get("/mine")
def list_my_reports(
    request: Request,
    response: Response,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    kind: Optional[str] = Query(default=None),
    page: Optional[int] = Query(default=None, ge=1),
    page_size: Optional[int] = Query(default=None, ge=1, le=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: CurrentUser = Depends(require_role("citizen", "lawyer")),
    x_request_id: str | None = Header(default=None),
):
    """Xem danh sách các báo cáo do chính mình đã gửi."""
    effective_limit = page_size if page_size is not None else limit
    effective_offset = ((page - 1) * effective_limit) if page is not None else offset
    data = report_service.list_my_reports(
        reporter_id=user.id,
        status=status_filter,
        kind=kind,
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


@router.get("/attachments/{attachment_id}")
def download_my_attachment(attachment_id: str, user: CurrentUser = Depends(require_role("citizen", "lawyer"))):
    return report_service.get_attachment_response(attachment_id, user.id, user.role)


@router.get("/{report_id}")
def get_my_report_detail(
    report_id: str,
    request: Request,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Xem chi tiết một báo cáo (kèm lịch sử xử lý và phản hồi của Admin)."""
    data = report_service.get_report_detail(
        report_id=report_id,
        viewer_id=user.id,
        viewer_role=user.role,
    )
    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "private, no-store"
    return {
        "data": data,
        "error": None,
        "request_id": request_id,
    }


@router.post("/{report_id}/cancel")
def cancel_report(
    report_id: str,
    request: Request,
    response: Response,
    user: CurrentUser = Depends(require_role("citizen", "lawyer")),
    x_request_id: str | None = Header(default=None),
):
    """Hủy báo cáo của mình khi đang ở trạng thái chờ xử lý ('pending')."""
    data = report_service.cancel_my_report(
        report_id=report_id,
        reporter_id=user.id,
        reporter_role=user.role,
    )
    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    response.headers["X-Request-ID"] = request_id
    return {
        "data": data,
        "error": None,
        "request_id": request_id,
    }
