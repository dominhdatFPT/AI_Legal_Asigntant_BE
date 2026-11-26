import asyncio
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI

from common_auth import outbox
from common_auth.fastapi_dep import add_auth_error_handler

from app.api import internal, lawyers
from app.core import database, event_outbox
from app.core.responses import install_error_handlers
from app.services import http_client

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    relay_task = None
    if outbox.enabled() and database.database_url():
        relay_task = asyncio.create_task(event_outbox.relay.run_forever())
    try:
        if relay_task is not None:
            # Let the relay enter its cleanup scope before startup can be followed by shutdown.
            await asyncio.sleep(0)
        yield
    finally:
        try:
            if relay_task is not None:
                await event_outbox.relay.shutdown(relay_task)
        finally:
            http_client.close_shared_client()


app = FastAPI(title="Lawyer Service", version="0.2.0", lifespan=lifespan)
add_auth_error_handler(app)
install_error_handlers(app)
app.include_router(lawyers.router)
app.include_router(internal.router)


@app.get("/api/v1/lawyers/health")
def health():
    return {"status": "ok", "service": "lawyer-service"}
