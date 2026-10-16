import logging

from fastapi import FastAPI

from common_auth.fastapi_dep import add_auth_error_handler

from app.api import internal, lawyers
from app.core.responses import install_error_handlers

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Lawyer Service", version="0.2.0")
add_auth_error_handler(app)
install_error_handlers(app)
app.include_router(lawyers.router)
app.include_router(internal.router)


@app.get("/api/v1/lawyers/health")
def health():
    return {"status": "ok", "service": "lawyer-service"}
