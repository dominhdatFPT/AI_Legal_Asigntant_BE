import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common_auth.fastapi_dep import add_auth_error_handler

from app.api import consultations, ws
from app.core import database
from app.core.responses import install_error_handlers
from app.services import transcript_service

logging.basicConfig(level=logging.INFO)

@asynccontextmanager
async def lifespan(_: FastAPI):
    # Không có database thì không quét (môi trường chưa cấu hình).
    task = asyncio.create_task(transcript_service.sweep_loop()) if database.database_url() else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Consultation Service", version="0.2.0", lifespan=lifespan)
add_auth_error_handler(app)
install_error_handlers(app)


# Đăng ký trước router: nếu không, /consultations/health bị hiểu là /{consultation_id}.
@app.get("/api/v1/consultations/health")
def health():
    return {"status": "ok", "service": "consultation-service"}


app.include_router(consultations.router)
app.include_router(ws.router)
