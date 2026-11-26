import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Optional

from common_auth import CurrentUser
from common_auth.fastapi_dep import add_auth_error_handler, get_current_user
from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.exc import OperationalError

from app.api.admin_broadcasts import router as admin_broadcasts_router
from app.api.admin_reports import router as admin_reports_router
from app.api.reports import router as reports_router
from app.core.notification_store import (
    count_unread_notifications,
    get_engine,
    get_notification_feed,
    hide_notification,
    mark_all_as_read,
    mark_as_read,
)
from app.core.rabbitmq_notification_hub import start_notification_hub
from app.core.realtime_broker import realtime_broker, start_redis_listener
from app.services.recipient_service import close_http_client
from app.services.report_service import ReportDomainError

logger = logging.getLogger("NotificationService")


class NotificationHeadersMiddleware:
    """Pure ASGI middleware (an toàn với SSE stream) gắn X-Request-ID và Cache-Control."""

    def __init__(self, asgi_app) -> None:
        self.app = asgi_app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        req_headers = {k.lower(): v for k, v in scope.get("headers") or ()}
        raw_rid = req_headers.get(b"x-request-id")
        request_id = raw_rid.decode("latin-1").strip() if raw_rid else ""
        if not request_id:
            request_id = f"req-{uuid.uuid4()}"
        scope.setdefault("state", {})["request_id"] = request_id
        path = scope.get("path") or ""

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                names = {k.lower() for k, _ in headers}
                if b"x-request-id" not in names:
                    headers.append((b"x-request-id", request_id.encode("latin-1")))
                if (
                    b"cache-control" not in names
                    and path.startswith("/api/v1/")
                    and path not in ("/api/v1/notifications/health", "/api/v1/notifications/stream")
                ):
                    headers.append((b"cache-control", b"private, no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


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

    enrichment_task = None
    if os.getenv("RUN_REPORT_ENRICHMENT_IN_API", "").strip().lower() in ("1", "true", "yes"):
        from app.report_enrichment_worker import start_enrichment_worker

        enrichment_task = asyncio.create_task(start_enrichment_worker())

    redis_task = asyncio.create_task(start_redis_listener(os.environ["REDIS_URL"])) if os.getenv("REDIS_URL") else None
    try:
        yield
    finally:
        if enrichment_task is not None:
            enrichment_task.cancel()
            await asyncio.gather(enrichment_task, return_exceptions=True)
        realtime_broker.close()
        await close_http_client()
        if redis_task:
            redis_task.cancel()
            try:
                await redis_task
            except asyncio.CancelledError:
                pass
        if hub_task and not hub_task.done():
            logger.info("Đang dừng Notification Hub consumer lifespan task...")
            hub_task.cancel()
            try:
                await hub_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Notification & Support Report Service", version="0.2.0", lifespan=lifespan)
app.add_middleware(NotificationHeadersMiddleware)
add_auth_error_handler(app)


@app.exception_handler(ReportDomainError)
async def _report_domain_error_handler(request: Request, exc: ReportDomainError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "data": None,
            "error": {"code": exc.code, "message": exc.message},
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id, "Cache-Control": "private, no-store"},
    )


_FIELD_LABELS_VI = {
    "title": "Tiêu đề",
    "content": "Nội dung",
    "description": "Mô tả",
    "note": "Ghi chú",
    "category_code": "Nhóm vấn đề",
    "reason_code": "Lý do",
    "reject_reason_code": "Lý do từ chối",
    "audience": "Đối tượng nhận",
    "priority": "Mức độ ưu tiên",
    "channels": "Kênh gửi",
    "recipient_id": "Người nhận",
    "recipient_ids": "Danh sách người nhận",
    "attachment_ids": "Ảnh đính kèm",
    "action_url": "Đường dẫn đích",
    "limit": "Giới hạn số lượng",
    "offset": "Vị trí bắt đầu",
    "page": "Số trang",
    "page_size": "Kích thước trang",
    "before": "Mốc thời gian",
    "before_id": "Mã phân trang",
}


def _format_validation_message(err: dict[str, Any], field: str) -> str:
    leaf = field.split(".")[-1]
    label = _FIELD_LABELS_VI.get(leaf, field)
    err_type = str(err.get("type") or "")
    ctx = err.get("ctx") or {}
    if err_type == "string_too_short" and ctx.get("min_length"):
        return f"{label} phải có ít nhất {ctx['min_length']} ký tự."
    if err_type == "string_too_long" and ctx.get("max_length"):
        return f"{label} không được vượt quá {ctx['max_length']} ký tự."
    if err_type == "too_long" and ctx.get("max_length"):
        return f"{label} vượt quá giới hạn tối đa ({ctx['max_length']})."
    if err_type == "too_short" and ctx.get("min_length"):
        return f"{label} cần tối thiểu {ctx['min_length']} mục."
    if err_type == "missing":
        return f"Thiếu trường bắt buộc: {label}."
    if err_type == "extra_forbidden":
        return f"Trường '{field}' không được hỗ trợ."
    if err_type == "greater_than_equal" and ctx.get("ge") is not None:
        return f"{label} phải lớn hơn hoặc bằng {ctx['ge']}."
    if err_type == "less_than_equal" and ctx.get("le") is not None:
        return f"{label} phải nhỏ hơn hoặc bằng {ctx['le']}."
    if err_type in ("int_parsing", "bool_parsing", "datetime_from_date_parsing", "datetime_parsing"):
        return f"{label} không đúng định dạng."
    if err_type == "literal_error":
        return f"Giá trị của {label} không hợp lệ."
    raw_msg = str(err.get("msg") or "Dữ liệu không hợp lệ.")
    return f"{label}: {raw_msg}" if field != "request" else raw_msg


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
    details = []
    for err in exc.errors():
        loc = [str(part) for part in (err.get("loc") or ()) if part not in ("body", "query", "path")]
        field = ".".join(loc) if loc else "request"
        details.append({"field": field, "message": _format_validation_message(err, field)})
    return JSONResponse(
        status_code=422,
        content={
            "data": None,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Dữ liệu gửi lên không hợp lệ.",
                "details": details,
            },
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id, "Cache-Control": "private, no-store"},
    )


@app.exception_handler(OperationalError)
async def _db_operational_error_handler(request: Request, exc: OperationalError) -> JSONResponse:
    logger.error("Database operational error on %s: %s", request.url.path, exc)
    request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
    return JSONResponse(
        status_code=503,
        content={
            "data": None,
            "error": {
                "code": "DATABASE_UNAVAILABLE",
                "message": "Dịch vụ thông báo tạm thời bận, vui lòng thử lại sau.",
            },
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id, "Cache-Control": "private, no-store"},
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
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Kiểm tra JWT đã gắn; không giả lập danh sách thông báo."""
    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    return {
        "data": {"user_id": user.id, "role": user.role},
        "error": None,
        "request_id": request_id,
    }


@authenticated_router.get("")
def list_notifications(
    request: Request,
    response: Response,
    limit: int = Query(default=20, ge=1, le=100),
    is_read: Optional[bool] = Query(default=None),
    unread_only: bool = Query(default=False),
    include_unread_count: bool = Query(default=True),
    before: Optional[datetime] = Query(default=None),
    before_id: Optional[str] = Query(default=None, max_length=64),
    user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Lấy danh sách thông báo của người dùng (gộp thông báo cá nhân + thông báo nhóm/broadcast theo role)."""
    if before_id is not None and before is None:
        raise ReportDomainError(422, "INVALID_CURSOR", "Cursor không hợp lệ.")
    feed = get_notification_feed(
        user.id,
        limit=limit,
        user_role=user.role,
        is_read=is_read,
        unread_only=unread_only,
        before=before,
        before_id=before_id,
        include_unread_count=include_unread_count,
    )
    items = feed["items"]
    has_more = feed["has_more"]
    data: dict[str, Any] = {"items": items, "has_more": has_more}
    if has_more and items:
        data["next_before"] = items[-1]["created_at"]
        data["next_before_id"] = items[-1]["id"]
    if include_unread_count:
        data["unread_count"] = feed["unread_count"]

    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "private, no-store"
    return {
        "data": data,
        "error": None,
        "request_id": request_id,
    }


@authenticated_router.get("/unread-count")
def get_unread_count(
    request: Request,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Đếm số lượng thông báo chưa đọc của người dùng."""
    count = count_unread_notifications(user.id, user_role=user.role)
    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "private, no-store"
    return {
        "data": {"count": count, "unread_count": count},
        "error": None,
        "request_id": request_id,
    }


@authenticated_router.patch("/{notification_id}/read")
def read_notification(
    notification_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Đánh dấu 1 thông báo (cá nhân hoặc nhóm/broadcast) là đã đọc."""
    success = mark_as_read(user.id, notification_id, user_role=user.role)
    if not success:
        raise ReportDomainError(404, "NOTIFICATION_NOT_FOUND", "Không tìm thấy thông báo.")
    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    return {
        "data": {"success": True},
        "error": None,
        "request_id": request_id,
    }


@authenticated_router.post("/read-all")
def read_all_notifications(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Đánh dấu toàn bộ thông báo của người dùng là đã đọc."""
    updated = mark_all_as_read(user.id, user_role=user.role)
    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    return {
        "data": {"updated_count": updated},
        "error": None,
        "request_id": request_id,
    }


@authenticated_router.delete("/{notification_id}")
def delete_notification(
    notification_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Ẩn / xóa thông báo khỏi danh sách của người dùng."""
    success = hide_notification(user.id, notification_id, user_role=user.role)
    if not success:
        raise ReportDomainError(404, "NOTIFICATION_NOT_FOUND", "Không tìm thấy thông báo.")
    request_id = getattr(request.state, "request_id", None) or x_request_id or f"req-{uuid.uuid4()}"
    return {
        "data": {"success": True},
        "error": None,
        "request_id": request_id,
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
                    if notif is None:
                        break
                    nid = notif.get("id") if isinstance(notif, dict) else None
                    id_line = f"id: {nid}\n" if nid else ""
                    yield f"{id_line}event: notification\ndata: {json.dumps(notif, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            realtime_broker.unsubscribe(sub)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


app.include_router(authenticated_router)
app.include_router(reports_router)
app.include_router(admin_reports_router)
app.include_router(admin_broadcasts_router)
