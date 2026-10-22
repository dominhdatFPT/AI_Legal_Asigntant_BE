import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Header

from app.core.notification_store import mark_as_read
from app.core.rabbitmq_notification_hub import get_user_notifications, start_notification_hub
from common_auth import CurrentUser
from common_auth.fastapi_dep import add_auth_error_handler, get_current_user

logger = logging.getLogger("NotificationService")


@asynccontextmanager
async def lifespan(app: FastAPI):
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


app = FastAPI(title="Notification Service", version="0.1.0", lifespan=lifespan)
add_auth_error_handler(app)

# Router API user dùng JWT
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
    user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Lấy danh sách thông báo của người dùng từ Event Hub (Persistent Store)."""
    items = get_user_notifications(user.id)
    return {
        "data": {
            "items": items,
            "unread_count": sum(1 for n in items if not n.get("is_read")),
        },
        "error": None,
        "request_id": x_request_id or f"req-{uuid.uuid4()}",
    }


@authenticated_router.patch("/{notification_id}/read")
def read_notification(
    notification_id: str,
    user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Đánh dấu thông báo là đã đọc."""
    success = mark_as_read(user.id, notification_id)
    return {
        "data": {"success": success},
        "error": None if success else "Notification not found",
        "request_id": x_request_id or f"req-{uuid.uuid4()}",
    }


app.include_router(authenticated_router)
