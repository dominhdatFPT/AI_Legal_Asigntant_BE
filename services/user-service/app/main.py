import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI

from common_auth import outbox, revocation
from common_auth.fastapi_dep import add_auth_error_handler

from app.api import admin_users, auth, internal_users, profile, users
from app.core import event_outbox, firebase
from app.core.responses import install_error_handlers

# User Service tự kiểm tra trạng thái khoá trong DB (403 kèm lý do), chỉ cần thu hồi token đã logout.
revocation.CHECK_BLOCKED = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tải trước Google public keys để request đăng nhập đầu tiên không bị độ trễ
    firebase.warmup_certs()
    relay_task = None
    audit_task = None
    try:
        if os.getenv("RUN_LOGOUT_AUDIT_IN_API", "").strip().lower() in ("1", "true", "yes"):
            from app.services.logout_worker import (
                QUEUE_AUDIT, ROUTING_KEY_USER_LOGGED_OUT, consume_forever, process_audit_log,
            )

            audit_task = asyncio.create_task(
                consume_forever(QUEUE_AUDIT, [ROUTING_KEY_USER_LOGGED_OUT], process_audit_log),
            )
        if outbox.enabled():
            relay_task = asyncio.create_task(event_outbox.relay.run_forever())
            # Give the relay a turn to register its cleanup before it can be cancelled.
            await asyncio.sleep(0)
        yield
    finally:
        if audit_task is not None:
            audit_task.cancel()
        try:
            if relay_task is not None:
                await event_outbox.relay.shutdown(relay_task)
        finally:
            if audit_task is not None:
                await asyncio.gather(audit_task, return_exceptions=True)


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
