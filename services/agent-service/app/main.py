import asyncio
import logging
import os
import uuid

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict, ValidationError

from app.api.v1.conversations import router as conversations_router
from app.services import agent, llm_client, rag_client

logger = logging.getLogger(__name__)


app = FastAPI(title="Agent Service", version="0.1.0")
app.include_router(conversations_router)
procedure_service_url = os.getenv(
    "PROCEDURE_SERVICE_URL",
    "http://procedure-service:8000",
)


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=2000)


class Procedure(BaseModel):
    id: str
    code: str
    name: str


def failure(status, code, message, request_id):
    return JSONResponse(status_code=status, content={
        "data": None, "error": {"code": code, "message": message},
        "request_id": request_id,
    })


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    request_id = request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
    details = [
        {"field": str(err["loc"][-1]) if err.get("loc") else "body", "message": err.get("msg", "")}
        for err in exc.errors()
    ]
    return JSONResponse(status_code=422, content={
        "data": None,
        "error": {"code": "VALIDATION_ERROR", "message": "Dữ liệu gửi lên không hợp lệ.", "details": details},
        "request_id": request_id,
    }, headers={"X-Request-ID": request_id})


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    request_id = request.headers.get("X-Request-ID") or f"req-{uuid.uuid4().hex[:8]}"
    error = (
        exc.detail
        if isinstance(exc.detail, dict)
        else {"code": "ERROR", "message": str(exc.detail)}
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"data": None, "error": error, "request_id": request_id},
        headers={"X-Request-ID": request_id},
    )


@app.get("/api/v1/agent/health")
def health():
    return {"status": "ok", "service": "agent-service"}


async def find_procedure(message: str, request_id: str) -> dict | None:
    """Tìm thủ tục khớp câu hỏi ở procedure-service. Lỗi thì trả None, không chặn câu trả lời AI."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{procedure_service_url.rstrip('/')}/api/v1/procedures/search",
                json={"query": message},
                headers={"X-Request-ID": request_id},
            )
            response.raise_for_status()
            items = response.json()["data"]["items"]
            if not isinstance(items, list):
                raise ValueError("items must be a list")
            procedures = [Procedure.model_validate(item).model_dump() for item in items]
    except (httpx.HTTPError, ValueError, KeyError, TypeError, ValidationError):
        logger.warning("Không tìm được thủ tục, request_id=%s", request_id, exc_info=True)
        return None
    return procedures[0] if procedures else None


@app.post("/api/v1/agent/chat")
async def chat(
    payload: ChatRequest,
    x_request_id: str | None = Header(default=None),
):
    """
    Chatbox công khai: KHÔNG cần đăng nhập, không lưu lịch sử.
    Hỏi đáp pháp luật bằng RAG (rag-service) + Gemini, kèm thủ tục phù hợp nếu có.
    Người dùng đã đăng nhập muốn lưu hội thoại thì dùng /api/v1/agent/conversations.
    """
    request_id = x_request_id or f"req-{uuid.uuid4()}"

    procedure, ai_result = await asyncio.gather(
        find_procedure(payload.message, request_id),
        agent.process_message(payload.message, [], request_id=request_id),
        return_exceptions=True,
    )
    if isinstance(procedure, BaseException):
        procedure = None

    if isinstance(ai_result, BaseException):
        if isinstance(ai_result, rag_client.RAGError):
            code, message = "RAG_SERVICE_ERROR", "Không thể truy xuất dữ liệu pháp luật lúc này, vui lòng thử lại."
        elif isinstance(ai_result, llm_client.LLMError):
            code, message = "AI_SERVICE_ERROR", "Dịch vụ AI đang gặp sự cố, vui lòng thử lại sau."
        else:
            raise ai_result
        logger.error("Chat lỗi %s, request_id=%s: %s", code, request_id, ai_result)
        if procedure is None:
            return failure(503, code, message, request_id)
        # AI lỗi nhưng vẫn tìm được thủ tục: trả thủ tục như trước đây
        answer, sources = "Đã tìm thấy thủ tục phù hợp.", []
    else:
        answer = ai_result["content"]
        sources = ai_result["metadata"].get("rag_chunk_ids", [])

    return {
        "data": {"answer": answer, "procedure": procedure, "sources": sources},
        "error": None,
        "request_id": request_id,
    }
