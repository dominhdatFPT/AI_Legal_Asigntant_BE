import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common_auth.fastapi_dep import add_auth_error_handler

from app.api import consultations, ws
from app.core import database
from app.core.responses import install_error_handlers
from app.services import http_client, transcript_service, user_events

logging.basicConfig(level=logging.INFO)

@asynccontextmanager
async def lifespan(_: FastAPI):
    # Không có database thì không quét (môi trường chưa cấu hình).
    tasks = []
    if database.database_url():
        tasks = [
            asyncio.create_task(transcript_service.sweep_loop()),
            asyncio.create_task(user_events.start()),
        ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        http_client.close_shared_client()


app = FastAPI(title="Consultation Service", version="0.2.0", lifespan=lifespan)
add_auth_error_handler(app)
install_error_handlers(app)


# Đăng ký trước router: nếu không, /consultations/health bị hiểu là /{consultation_id}.
@app.get("/api/v1/consultations/health")
def health():
    return {"status": "ok", "service": "consultation-service"}


app.include_router(consultations.router)
app.include_router(ws.router)
