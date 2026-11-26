"""API Endpoints cho Admin tiếp nhận, xử lý (Chấp nhận / Từ chối / Đã khắc phục) các Report."""

from __future__ import annotations

import uuid
from typing import Optional

from common_auth import CurrentUser
from common_auth.fastapi_dep import require_role
from fastapi import APIRouter, Depends, Header, Query, Request, Response

from app.schemas.reports import (
    AdminAcceptResolveRequest,
    AdminClaimReleaseRequest,
    AdminRejectRequest,
)
from app.services import report_service

router = APIRouter(
    prefix="/api/v1/admin/reports",
    tags=["Admin Reports"],
    dependencies=[Depends(require_role("admin"))],
)


@router.get("")
def list_reports(
    request: Request,
    response: Response,
    status: Optional[str] = Query(default=None, description="VD: pending,in_review"),
    kind: Optional[str] = Query(default=None, description="ticket | quick_error"),
    category: Optional[str] = Query(default=None, description="Mã nhóm vấn đề"),
    priority: Optional[str] = Query(default=None, description="low,normal,high,critical"),
    reporter_role: Optional[str] = Query(default=None, description="citizen | lawyer"),
    assignee: Optional[str] = Query(default=None, description="'me' hoặc user_id của admin"),
    q: Optional[str] = Query(default=None, description="Tìm kiếm theo mã RPT, tiêu đề, mô tả hoặc reporter_id"),
    date_from: Optional[str] = Query(default=None, alias="from", description="Từ ngày (YYYY-MM-DD hoặc ISO-8601)"),
    date_to: Optional[str] = Query(default=None, alias="to", description="Đến ngày (YYYY-MM-DD hoặc ISO-8601)"),
    sort: Optional[str] = Query(default=None, description="Sắp xếp: 'priority' hoặc mặc định theo thời gian"),
    page: Optional[int] = Query(default=None, ge=1),
    page_size: Optional[int] = Query(default=None, ge=1, le=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    admin: CurrentUser = Depends(require_role("admin")),
    x_request_id: str | None = Header(default=None),
):
    """Liệt kê và lọc toàn bộ báo cáo trong hệ thống dành cho Admin."""
    assignee_id = admin.id if assignee == "me" else assignee
    effective_limit = page_size if page_size is not None else limit
    effective_offset = ((page - 1) * effective_limit) if page is not None else offset
    data = report_service.list_admin_reports(
        status=status,
        kind=kind,
        category_code=category,
        priority=priority,
        reporter_role=reporter_role,
        assignee_id=assignee_id,
        q_text=q,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
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


@router.get("/summary")
def get_reports_summary(
    request: Request,
    response: Response,
    x_request_id: str | None = Header(default=None),
):
    """Thống kê số lượng báo cáo theo trạng thái và mức độ ưu tiên."""
    data = report_service.get_admin_reports_summary()
    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "private, no-store"
    return {
        "data": data,
        "error": None,
        "request_id": request_id,
    }


@router.get("/attachments/{attachment_id}")
def download_admin_attachment(attachment_id: str, user: CurrentUser = Depends(require_role("admin"))):
    return report_service.get_attachment_response(attachment_id, user.id, user.role)


@router.get("/{report_id}")
def get_admin_report_detail(
    report_id: str,
    request: Request,
    response: Response,
    admin: CurrentUser = Depends(require_role("admin")),
    x_request_id: str | None = Header(default=None),
):
    """Xem chi tiết đầy đủ của báo cáo (bao gồm context lỗi kỹ thuật, request_id, ảnh chụp và lịch sử)."""
    data = report_service.get_report_detail(
        report_id=report_id,
        viewer_id=admin.id,
        viewer_role="admin",
    )
    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "private, no-store"
    return {
        "data": data,
        "error": None,
        "request_id": request_id,
    }


@router.post("/{report_id}/claim")
def claim_report_endpoint(
    report_id: str,
    payload: AdminClaimReleaseRequest,
    request: Request,
    response: Response,
    admin: CurrentUser = Depends(require_role("admin")),
    x_request_id: str | None = Header(default=None),
):
    """Admin nhận xử lý báo cáo (chuyển từ 'pending' sang 'in_review')."""
    data = report_service.claim_report(report_id=report_id, admin_id=admin.id, req=payload)
    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    response.headers["X-Request-ID"] = request_id
    return {
        "data": data,
        "error": None,
        "request_id": request_id,
    }


@router.post("/{report_id}/release")
def release_report_endpoint(
    report_id: str,
    payload: AdminClaimReleaseRequest,
    request: Request,
    response: Response,
    admin: CurrentUser = Depends(require_role("admin")),
    x_request_id: str | None = Header(default=None),
):
    """Admin trả báo cáo về hàng đợi chờ ('pending')."""
    data = report_service.release_report(report_id=report_id, admin_id=admin.id, req=payload)
    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    response.headers["X-Request-ID"] = request_id
    return {
        "data": data,
        "error": None,
        "request_id": request_id,
    }


@router.post("/{report_id}/accept")
async def accept_report_endpoint(
    report_id: str,
    payload: AdminAcceptResolveRequest,
    request: Request,
    response: Response,
    admin: CurrentUser = Depends(require_role("admin")),
    x_request_id: str | None = Header(default=None),
):
    """Admin CHẤP NHẬN báo cáo -> gửi thông báo kết quả về cho người dùng/luật sư."""
    data = await report_service.accept_report(
        report_id=report_id,
        admin_id=admin.id,
        req=payload,
    )
    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    response.headers["X-Request-ID"] = request_id
    return {
        "data": data,
        "error": None,
        "request_id": request_id,
    }


@router.post("/{report_id}/reject")
async def reject_report_endpoint(
    report_id: str,
    payload: AdminRejectRequest,
    request: Request,
    response: Response,
    admin: CurrentUser = Depends(require_role("admin")),
    x_request_id: str | None = Header(default=None),
):
    """Admin TỪ CHỐI báo cáo (kèm lý do) -> gửi thông báo kết quả về cho người dùng/luật sư."""
    data = await report_service.reject_report(
        report_id=report_id,
        admin_id=admin.id,
        req=payload,
    )
    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    response.headers["X-Request-ID"] = request_id
    return {
        "data": data,
        "error": None,
        "request_id": request_id,
    }


@router.post("/{report_id}/resolve")
async def resolve_report_endpoint(
    report_id: str,
    payload: AdminAcceptResolveRequest,
    request: Request,
    response: Response,
    admin: CurrentUser = Depends(require_role("admin")),
    x_request_id: str | None = Header(default=None),
):
    """Admin đánh dấu sự cố đã được khắc phục hoàn tất ('resolved') -> gửi thông báo về cho người báo cáo."""
    data = await report_service.resolve_report(
        report_id=report_id,
        admin_id=admin.id,
        req=payload,
    )
    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    response.headers["X-Request-ID"] = request_id
    return {
        "data": data,
        "error": None,
        "request_id": request_id,
    }
