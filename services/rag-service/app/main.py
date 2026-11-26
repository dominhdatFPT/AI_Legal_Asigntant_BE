import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from common_auth import revocation

from app.api.v1 import documents, internal
from app.core.config import get_settings
from app.core.database import get_engine, get_session
from app.services import hybrid
from app.services.document_approval import recover_on_startup
from app.services.embedding import FakeEmbedder, close_async_embedding_client, get_embedder

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def warmup_database() -> None:
    started = time.perf_counter()
    try:
        settings = get_settings()
        if settings.hybrid_enabled:
            model = settings.embedding_model
            if settings.embedding_provider.lower() == "fake":
                model = FakeEmbedder(settings.embedding_dim).model_name
            with get_session() as db:
                hybrid.warmup_lexical(db, model)
        else:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        logger.info("rag DB warmup %.2fs", time.perf_counter() - started)
    except Exception:
        logger.warning("rag DB warmup failed", exc_info=True)


def warmup_provider() -> None:
    try:
        get_embedder().embed_query("khởi động")
    except Exception:
        logger.warning("rag embedding provider warmup failed", exc_info=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    warmups = []
    if settings.rag_database_url:
        try:
            get_engine()
        except Exception as exc:
            logger.warning("rag DB engine initialization failed (%s)", type(exc).__name__)
        else:
            # Văn bản còn processing lúc khởi động có thể là việc nền đã bị ngắt.
            await run_in_threadpool(recover_on_startup)
            if settings.warmup_on_startup:
                warmups = [
                    asyncio.create_task(run_in_threadpool(warmup_database)),
                    asyncio.create_task(run_in_threadpool(warmup_provider)),
                ]
    if os.getenv("RUN_RAG_INDEXING_WORKER_IN_API", "").strip().lower() in ("1", "true", "yes"):
        from common_auth.events import rabbitmq_url

        if rabbitmq_url():
            from app import rag_indexing_worker

            warmups.append(asyncio.create_task(rag_indexing_worker.main()))
    try:
        yield
    finally:
        for task in warmups:
            task.cancel()
        await asyncio.gather(*warmups, return_exceptions=True)
        await close_async_embedding_client()


app = FastAPI(title="RAG Service", version="0.2.0", lifespan=lifespan)
revocation.ensure_started()
app.include_router(internal.router)
app.include_router(documents.router)


def _request_id(request: Request) -> str:
    return request.headers.get("X-Request-ID") or f"req-{uuid.uuid4().hex[:8]}"


def _error(status_code: int, request_id: str, error: dict) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"data": None, "error": error, "request_id": request_id},
        headers={"X-Request-ID": request_id},
    )


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    details = [
        {
            "field": ".".join(str(p) for p in err["loc"] if p != "body") or "body",
            "message": err["msg"],
        }
        for err in exc.errors()
    ]
    return _error(422, _request_id(request), {
        "code": "VALIDATION_ERROR",
        "message": "Dữ liệu gửi lên không hợp lệ.",
        "details": details,
    })


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    error = (
        exc.detail
        if isinstance(exc.detail, dict)
        else {"code": "ERROR", "message": str(exc.detail)}
    )
    return _error(exc.status_code, _request_id(request), error)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Lỗi không lường trước: %s", exc)
    return _error(500, _request_id(request), {
        "code": "INTERNAL_ERROR",
        "message": "Hệ thống đang gặp lỗi, vui lòng thử lại sau.",
    })


@app.get("/api/v1/rag/health")
def health():
    return {"status": "ok", "service": "rag-service"}


# ── Endpoint demo cũ (giữ để không vỡ test hệ thống tests/test_api.py) ─────────
# agent-service dùng POST /internal/v1/rag/search (app/api/v1/internal.py), không dùng endpoint này.

class DemoSearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=2000)


@app.post("/api/v1/rag/search")
def demo_search(payload: DemoSearchRequest, x_request_id: str | None = Header(default=None)):
    return {
        "data": {
            "results": [
                {
                    "content": "Đây là kết quả mẫu. Tìm kiếm thật nằm ở /internal/v1/rag/search.",
                    "score": 1.0,
                    "source": {
                        "id": "SRC-DEMO",
                        "title": "Nguồn mẫu",
                        "url": None,
                        "page": None,
                        "section": None,
                    },
                }
            ]
        },
        "error": None,
        "request_id": x_request_id or f"req-{uuid.uuid4()}",
    }
