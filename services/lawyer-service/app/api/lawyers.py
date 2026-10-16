"""API cho FE (qua Gateway /api/lawyers/...). Đường dẫn và mã lỗi theo openapi.yaml."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import Caller, get_caller, require_admin
from app.core.database import get_db
from app.core.responses import ok
from app.repositories import lawyer_repository as repo
from app.schemas.lawyer import (
    ApplicationCreateRequest,
    ApplicationUpdateRequest,
    CreateLawyerAccountRequest,
    ProfileUpdateRequest,
    RejectRequest,
)
from app.services import admin_service, application_service, profile_service

router = APIRouter(prefix="/api/v1/lawyers")

ApplicationType = Literal["new", "card_change"]


# ---------------------------------------------------------------- Public
@router.get("/specializations", tags=["Public"])
def list_specializations(request: Request, db: Session = Depends(get_db)):
    return ok(request, {"items": [{"code": s.code, "name": s.name} for s in repo.specializations(db)]})


# ---------------------------------------------------------------- My lawyer profile
@router.get("/me", tags=["My Lawyer Profile"])
def get_my_profile(request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    return ok(request, profile_service.get_my_profile(db, caller.id))


@router.patch("/me", tags=["My Lawyer Profile"])
def update_my_profile(body: ProfileUpdateRequest, request: Request, caller: Caller = Depends(get_caller),
                      db: Session = Depends(get_db)):
    return ok(request, profile_service.update_my_profile(db, caller.id, caller.role, body))


# ---------------------------------------------------------------- My applications
@router.get("/me/applications", tags=["My Applications"])
def list_my_applications(request: Request, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                         caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    return ok(request, application_service.list_mine(db, caller.id, page, page_size))


@router.post("/me/applications", status_code=201, tags=["My Applications"])
def create_my_application(body: ApplicationCreateRequest, request: Request, caller: Caller = Depends(get_caller),
                          db: Session = Depends(get_db)):
    return ok(request, application_service.create(db, caller.id, caller.role, body), status=201)


@router.get("/me/applications/{application_id}", tags=["My Applications"])
def get_my_application(application_id: uuid.UUID, request: Request, caller: Caller = Depends(get_caller),
                       db: Session = Depends(get_db)):
    return ok(request, application_service.get_mine(db, caller.id, application_id))


@router.patch("/me/applications/{application_id}", tags=["My Applications"])
def update_my_application(application_id: uuid.UUID, body: ApplicationUpdateRequest, request: Request,
                          caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    return ok(request, application_service.update(db, caller.id, application_id, body))


@router.post("/me/applications/{application_id}/submit", tags=["My Applications"])
def submit_my_application(application_id: uuid.UUID, request: Request, caller: Caller = Depends(get_caller),
                          db: Session = Depends(get_db)):
    return ok(request, application_service.submit(db, caller.id, application_id))


@router.post("/me/applications/{application_id}/withdraw", tags=["My Applications"])
def withdraw_my_application(application_id: uuid.UUID, request: Request, caller: Caller = Depends(get_caller),
                            db: Session = Depends(get_db)):
    return ok(request, application_service.withdraw(db, caller.id, application_id))


# ---------------------------------------------------------------- Admin
@router.post("/accounts", status_code=201, tags=["Admin"])
def create_lawyer_account(body: CreateLawyerAccountRequest, request: Request, admin: Caller = Depends(require_admin),
                          db: Session = Depends(get_db)):
    return ok(request, admin_service.create_lawyer_account(db, admin.id, body), status=201)


@router.get("/applications", tags=["Admin"])
def list_applications(
    request: Request,
    status: Literal["pending", "approved", "rejected", "withdrawn"] = "pending",
    type: ApplicationType | None = None,
    order: Literal["asc", "desc"] = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: Caller = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return ok(request, admin_service.list_applications(db, status, type, order, page, page_size))


@router.get("/applications/summary", tags=["Admin"])
def application_summary(request: Request, type: ApplicationType | None = None, admin: Caller = Depends(require_admin),
                        db: Session = Depends(get_db)):
    return ok(request, admin_service.summary(db, type))


@router.get("/applications/{application_id}", tags=["Admin"])
def review_application(application_id: uuid.UUID, request: Request, admin: Caller = Depends(require_admin),
                       db: Session = Depends(get_db)):
    return ok(request, admin_service.review(db, application_id))


@router.post("/applications/{application_id}/approve", tags=["Admin"])
def approve_application(application_id: uuid.UUID, request: Request, admin: Caller = Depends(require_admin),
                        db: Session = Depends(get_db)):
    return ok(request, admin_service.approve(db, admin.id, application_id))


@router.post("/applications/{application_id}/reject", tags=["Admin"])
def reject_application(application_id: uuid.UUID, body: RejectRequest, request: Request,
                       admin: Caller = Depends(require_admin), db: Session = Depends(get_db)):
    return ok(request, admin_service.reject(db, admin.id, application_id, body.reason))
