from contextlib import asynccontextmanager
from fastapi import FastAPI

from common_auth import revocation
from common_auth.fastapi_dep import add_auth_error_handler

from app.api import admin_users, auth, internal_users, profile, users
from app.core import firebase
from app.core.responses import install_error_handlers

# User Service tự kiểm tra trạng thái khoá trong DB (403 kèm lý do), chỉ cần thu hồi token đã logout.
revocation.CHECK_BLOCKED = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tải trước Google public keys để request đăng nhập đầu tiên không bị độ trễ
    firebase.warmup_certs()
    yield


app = FastAPI(title="User Service", version="0.1.0", lifespan=lifespan)
add_auth_error_handler(app)
install_error_handlers(app)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(profile.router)
app.include_router(profile.internal_router)
app.include_router(internal_users.router)
app.include_router(admin_users.router)


@app.get("/api/v1/users/health")
def health():
    return {"status": "ok", "service": "user-service"}
