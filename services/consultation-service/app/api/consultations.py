"""API cho FE (qua Gateway /api/consultations/...). Đường dẫn cố định phải đăng ký trước /{consultation_id}."""

import uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy.orm import Session

from common_auth.core import ACCESS_COOKIE, extract_token

from app.api.deps import Caller, get_caller
from app.core.database import get_db
from app.core.responses import ok
from app.schemas.consultation import AcceptTermsRequest, CreateConsultationRequest, SendMessageRequest
from app.services import consultation_service as service
from app.services import realtime, transcript_service

router = APIRouter(prefix="/api/v1/consultations")


def _event_data(view: dict) -> dict:
    # unread_count là của riêng từng người xem nên không đưa vào sự kiện gửi chung; FE gọi REST khi cần.
    return {**view, "unread_count": 0}


def _access_token(request: Request) -> str:
    # get_caller đã kiểm token; lấy lại chuỗi token để chuyển tiếp cho User Service dưới quyền người gọi.
    return extract_token(request.headers.get("Authorization"), request.cookies.get(ACCESS_COOKIE))


# ---------------------------------------------------------------- Đường dẫn cố định
@router.get("/terms", tags=["Terms"])
def get_consultation_terms(request: Request, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    return ok(request, service.terms_status(db, caller))


# Phải đứng trước POST /{consultation_id}/accept: cùng số đoạn đường dẫn.
@router.post("/terms/accept", tags=["Terms"])
def accept_consultation_terms(body: AcceptTermsRequest, request: Request, caller: Caller = Depends(get_caller),
                              db: Session = Depends(get_db)):
    return ok(request, service.accept_terms(db, caller, body.terms_version))


@router.get("/lobby", tags=["Lobby"])
def get_lobby(request: Request, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
              caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    return ok(request, service.lobby(db, caller, _access_token(request), page, page_size))


# ---------------------------------------------------------------- Cuộc tư vấn
@router.post("", status_code=201, tags=["Consultations"])
def create_consultation(body: CreateConsultationRequest, request: Request, background: BackgroundTasks,
                        caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    data = service.create(db, caller, body)
    background.add_task(realtime.publish, "consultation.created", _event_data(data), realtime.LOBBY)
    if data["agent_session_id"] is not None:
        background.add_task(service.summarize_in_background, data["id"])
    return ok(request, data, status=201)


@router.get("", tags=["Consultations"])
def list_my_consultations(
    request: Request,
    status: Literal["waiting", "in_progress", "closed"] | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    caller: Caller = Depends(get_caller),
    db: Session = Depends(get_db),
):
    return ok(request, service.list_mine(db, caller, status, page, page_size))


@router.get("/{consultation_id}", tags=["Consultations"])
def get_consultation(consultation_id: uuid.UUID, request: Request, caller: Caller = Depends(get_caller),
                     db: Session = Depends(get_db)):
    return ok(request, service.get_one(db, caller, _access_token(request), consultation_id))


@router.get("/{consultation_id}/summary", tags=["Consultations"])
def get_consultation_summary(consultation_id: uuid.UUID, request: Request, caller: Caller = Depends(get_caller),
                             db: Session = Depends(get_db)):
    return ok(request, service.get_summary(db, caller, _access_token(request), consultation_id))


@router.post("/{consultation_id}/accept", tags=["Lobby"])
def accept_consultation(consultation_id: uuid.UUID, request: Request, background: BackgroundTasks,
                        caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    data, removed = service.accept(db, caller, _access_token(request), consultation_id)
    accepted = {"consultation_id": data["id"], "lawyer_id": data["lawyer_id"]}
    background.add_task(realtime.publish, "consultation.accepted", accepted, realtime.LOBBY,
                        realtime.chat(data["id"]), realtime.me(data["citizen_id"]), realtime.me(data["lawyer_id"]))
    if removed:
        background.add_task(realtime.publish, "consultation.removed", {"consultation_ids": removed}, realtime.LOBBY)
    return ok(request, data)


@router.post("/{consultation_id}/close", tags=["Consultations"])
def close_consultation(consultation_id: uuid.UUID, request: Request, background: BackgroundTasks,
                       caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    data = service.close(db, caller, consultation_id)
    background.add_task(realtime.publish, "consultation.closed", _event_data(data), realtime.chat(data["id"]),
                        realtime.me(data["citizen_id"]), realtime.me(data["lawyer_id"]))
    background.add_task(transcript_service.summarize_all, data["id"])  # biên bản lần cuối, không chờ 10 phút
    return ok(request, data)


# ---------------------------------------------------------------- Tin nhắn
@router.get("/{consultation_id}/messages", tags=["Messages"])
def list_messages(
    consultation_id: uuid.UUID,
    request: Request,
    after: uuid.UUID | None = None,
    before: uuid.UUID | None = None,
    limit: int = Query(30, ge=1, le=100),
    caller: Caller = Depends(get_caller),
    db: Session = Depends(get_db),
):
    return ok(request, service.list_messages(db, caller, consultation_id, limit, before, after))


@router.post("/{consultation_id}/messages", status_code=201, tags=["Messages"])
def send_message(consultation_id: uuid.UUID, body: SendMessageRequest, request: Request, background: BackgroundTasks,
                 caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    message, recipient = service.send_message(db, caller, _access_token(request), consultation_id, body.content)
    background.add_task(realtime.publish, "message.created", message, realtime.chat(consultation_id),
                        realtime.me(recipient))
    return ok(request, message, status=201)


@router.post("/{consultation_id}/read", tags=["Messages"])
def mark_consultation_read(consultation_id: uuid.UUID, request: Request, caller: Caller = Depends(get_caller),
                           db: Session = Depends(get_db)):
    return ok(request, service.mark_read(db, caller, consultation_id))
