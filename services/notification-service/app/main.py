import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager

from common_auth import CurrentUser
from common_auth.fastapi_dep import add_auth_error_handler, get_current_user
from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.admin_broadcasts import router as admin_broadcasts_router
from app.api.admin_reports import router as admin_reports_router
from app.api.reports import router as reports_router
from app.core.notification_store import (
    count_unread_notifications,
    get_engine,
    hide_notification,
    mark_all_as_read,
    mark_as_read,
)
from app.core.rabbitmq_notification_hub import get_user_notifications, start_notification_hub
from app.core.realtime_broker import realtime_broker
from app.services.report_service import ReportDomainError

logger = logging.getLogger("NotificationService")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Khởi tạo DB & seed sẵn danh mục Report Categories / Reasons
    get_engine()

    # Khởi động background consumer ngay trong process FastAPI nếu bật (mặc định bật)
    run_in_api = os.getenv("RUN_NOTIFICATION_HUB_IN_API", "true").lower() in ("true", "1", "yes")
    hub_task = None
    if run_in_api:
        logger.info("Khởi chạy Notification Hub consumer bên trong FastAPI app lifespan...")
        hub_task = asyncio.create_task(start_notification_hub())

    try:
        yield
    finally:
        if hub_task and not hub_task.done():
            logger.info("Đang dừng Notification Hub consumer lifespan task...")
            hub_task.cancel()
            try:
                await hub_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Notification & Support Report Service", version="0.2.0", lifespan=lifespan)
add_auth_error_handler(app)


@app.exception_handler(ReportDomainError)
async def _report_domain_error_handler(request: Request, exc: ReportDomainError) -> JSONResponse:
    request_id = request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "data": None,
            "error": {"code": exc.code, "message": exc.message},
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )


# Router API user dùng JWT cho Thông báo
authenticated_router = APIRouter(
    prefix="/api/v1/notifications", dependencies=[Depends(get_current_user)]
)


@app.get("/api/v1/notifications/health")
def health():
    return {"status": "ok", "service": "notification-service"}


@authenticated_router.get("/me")
def auth_context(
    user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Kiểm tra JWT đã gắn; không giả lập danh sách thông báo."""
    return {
        "data": {"user_id": user.id, "role": user.role},
        "error": None,
        "request_id": x_request_id or f"req-{uuid.uuid4()}",
    }


@authenticated_router.get("")
def list_notifications(
    limit: int = Query(default=50, ge=1, le=200),
    unread_only: bool = Query(default=False),
    user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Lấy danh sách thông báo của người dùng (gộp thông báo cá nhân + thông báo nhóm/broadcast theo role)."""
    items = get_user_notifications(
        user.id,
        limit=limit,
        user_role=user.role,
        unread_only=unread_only,
    )
    return {
        "data": {
            "items": items,
            "unread_count": sum(1 for n in items if not n.get("is_read")),
        },
        "error": None,
        "request_id": x_request_id or f"req-{uuid.uuid4()}",
    }


@authenticated_router.get("/unread-count")
def get_unread_count(
    user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Đếm số lượng thông báo chưa đọc của người dùng."""
    count = count_unread_notifications(user.id, user_role=user.role)
    return {
        "data": {"unread_count": count},
        "error": None,
        "request_id": x_request_id or f"req-{uuid.uuid4()}",
    }


@authenticated_router.patch("/{notification_id}/read")
def read_notification(
    notification_id: str,
    user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Đánh dấu 1 thông báo (cá nhân hoặc nhóm/broadcast) là đã đọc."""
    success = mark_as_read(user.id, notification_id, user_role=user.role)
    return {
        "data": {"success": success},
        "error": None if success else "Notification not found",
        "request_id": x_request_id or f"req-{uuid.uuid4()}",
    }


@authenticated_router.post("/read-all")
def read_all_notifications(
    user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Đánh dấu toàn bộ thông báo của người dùng là đã đọc."""
    updated = mark_all_as_read(user.id, user_role=user.role)
    return {
        "data": {"updated_count": updated},
        "error": None,
        "request_id": x_request_id or f"req-{uuid.uuid4()}",
    }


@authenticated_router.delete("/{notification_id}")
def delete_notification(
    notification_id: str,
    user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Ẩn / xóa thông báo khỏi danh sách của người dùng."""
    success = hide_notification(user.id, notification_id, user_role=user.role)
    return {
        "data": {"success": success},
        "error": None if success else "Notification not found",
        "request_id": x_request_id or f"req-{uuid.uuid4()}",
    }


@authenticated_router.get("/stream")
async def stream_notifications(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """Kênh Server-Sent Events (SSE) đẩy thông báo thời gian thực tới chuông người dùng/luật sư/admin."""
    sub = realtime_broker.subscribe(user.id, user.role)
    _, _, queue = sub

    async def event_generator():
        try:
            yield f"event: connected\ndata: {json.dumps({'user_id': user.id, 'role': user.role})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    notif = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"event: notification\ndata: {json.dumps(notif, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            realtime_broker.unsubscribe(sub)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


app.include_router(authenticated_router)
app.include_router(reports_router)
app.include_router(admin_reports_router)
app.include_router(admin_broadcasts_router)
