"""API cho FE (qua Gateway /api/lawyers/...). Đường dẫn theo trang lawyer-registration và tab duyệt luật sư của admin."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from common_auth.core import ACCESS_COOKIE, extract_token

from app.api.deps import Caller, get_caller, require_admin
from app.core.database import get_db
from app.core.responses import ok
from app.repositories import lawyer_repository as repo
from app.schemas.lawyer import LawyerSubmitRequest, RejectRequest
from app.services import admin_service, application_service

router = APIRouter(prefix="/api/v1/lawyers")


def _access_token(request: Request) -> str:
    # get_caller đã kiểm token; lấy lại chuỗi token để đọc giấy tờ ở Document Service dưới quyền người gửi.
    return extract_token(request.headers.get("Authorization"), request.cookies.get(ACCESS_COOKIE))


# ---------------------------------------------------------------- Public
@router.get("/specializations", tags=["Public"])
def list_specializations(request: Request, db: Session = Depends(get_db)):
    return ok(request, {"items": [{"code": s.code, "name": s.name} for s in repo.specializations(db)]})


# ---------------------------------------------------------------- Hồ sơ của tôi
@router.post("", status_code=201, tags=["My Lawyer Profile"])
def submit_lawyer_application(body: LawyerSubmitRequest, request: Request, caller: Caller = Depends(get_caller),
                              db: Session = Depends(get_db)):
    data = application_service.submit(db, caller.id, caller.role, body, _access_token(request), resubmit=False)
    return ok(request, data, status=201)


@router.get("/me", tags=["My Lawyer Profile"])
def get_my_lawyer_profile(request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    return ok(request, application_service.get_mine(db, caller.id))


@router.put("/me", tags=["My Lawyer Profile"])
def resubmit_my_lawyer_profile(body: LawyerSubmitRequest, request: Request, caller: Caller = Depends(get_caller),
                               db: Session = Depends(get_db)):
    data = application_service.submit(db, caller.id, caller.role, body, _access_token(request), resubmit=True)
    return ok(request, data)


# ---------------------------------------------------------------- Admin
@router.get("/applications", tags=["Admin"])
def list_lawyer_applications(
    request: Request,
    approval_status: Literal["pending", "approved", "rejected"] = "pending",
    order: Literal["asc", "desc"] = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: Caller = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return ok(request, admin_service.list_applications(db, approval_status, order, page, page_size))


@router.post("/{lawyer_id}/approve", tags=["Admin"])
def approve_lawyer_application(lawyer_id: uuid.UUID, request: Request, admin: Caller = Depends(require_admin),
                               db: Session = Depends(get_db)):
    return ok(request, admin_service.approve(db, admin.id, lawyer_id))


@router.post("/{lawyer_id}/reject", tags=["Admin"])
def reject_lawyer_application(lawyer_id: uuid.UUID, body: RejectRequest, request: Request,
                              admin: Caller = Depends(require_admin), db: Session = Depends(get_db)):
    return ok(request, admin_service.reject(db, admin.id, lawyer_id, body.reason))


@router.get("/{lawyer_id}/credentials/{document_id}", tags=["Admin"])
def view_lawyer_credential(lawyer_id: uuid.UUID, document_id: uuid.UUID, admin: Caller = Depends(require_admin),
                           db: Session = Depends(get_db)):
    """Admin mở giấy tờ (ảnh thẻ, chứng chỉ...) đính kèm hồ sơ để đối chiếu trước khi duyệt."""
    content, media_type, disposition = admin_service.credential_file(db, lawyer_id, document_id)
    return Response(content=content, media_type=media_type, headers={
        "Content-Disposition": disposition, "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})
