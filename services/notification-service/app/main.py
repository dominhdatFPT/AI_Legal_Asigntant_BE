import uuid

from fastapi import APIRouter, Depends, FastAPI, Header

from common_auth import CurrentUser
from common_auth.fastapi_dep import add_auth_error_handler, get_current_user


app = FastAPI(title="Notification Service", version="0.1.0")
add_auth_error_handler(app)

# Chỉ router API user dùng JWT; event nội bộ sau này dùng router/token riêng.
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


app.include_router(authenticated_router)

