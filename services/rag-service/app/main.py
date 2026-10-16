import logging
import uuid

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1 import internal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="RAG Service", version="0.2.0")
app.include_router(internal.router)


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
