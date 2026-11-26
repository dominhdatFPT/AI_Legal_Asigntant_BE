import asyncio
from contextlib import asynccontextmanager
import ipaddress
import logging
import os
import uuid

import httpx
from common_auth import revocation
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict, ValidationError

from app.api.v1.conversations import router as conversations_router
from app.api.v1.form_check import router as form_check_router
from app.api.v1.internal import minutes_router, router as internal_router
from app.core.config import get_settings
from app.core.database import get_engine
from app.services import admission, agent, case_budget, conversation_cleanup, llm_client, quota, rag_client

# Hiện log INFO của app (ví dụ "agent timing") trong docker logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_proc_client: httpx.AsyncClient | None = None
_proc_loop: asyncio.AbstractEventLoop | None = None


async def _get_procedure_client() -> httpx.AsyncClient:
    global _proc_client, _proc_loop
    loop = asyncio.get_running_loop()
    if _proc_client is not None and not _proc_client.is_closed and _proc_loop is loop:
        return _proc_client
    old_client, old_loop = _proc_client, _proc_loop
    _proc_client = httpx.AsyncClient(
        timeout=5.0,
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
    )
    _proc_loop = loop
    if old_client is not None and not old_client.is_closed and old_loop is loop and not loop.is_closed():
        await old_client.aclose()
    return _proc_client


async def _close_procedure_client() -> None:
    global _proc_client, _proc_loop
    client, loop = _proc_client, _proc_loop
    _proc_client, _proc_loop = None, None
    if client is not None and not client.is_closed and loop is asyncio.get_running_loop():
        await client.aclose()


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    cleanup_task = None
    if settings.agent_database_url:
        get_engine()
        if (
            settings.conversation_retention_days > 0
            and settings.conversation_cleanup_interval_seconds > 0
        ):
            cleanup_task = asyncio.create_task(conversation_cleanup.cleanup_loop())
    revocation.ensure_started()
    try:
        yield
    finally:
        if cleanup_task is not None:
            cleanup_task.cancel()
            await asyncio.gather(cleanup_task, return_exceptions=True)
        await _close_procedure_client()
        await rag_client.close_http_client()


app = FastAPI(title="Agent Service", version="0.1.0", lifespan=lifespan)
app.include_router(conversations_router)
app.include_router(form_check_router)
app.include_router(internal_router)
app.include_router(minutes_router)
procedure_service_url = os.getenv(
    "PROCEDURE_SERVICE_URL",
    "http://procedure-service:8000",
)


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=20000)


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
    request_id = (
        request.headers.get("X-Request-ID")
        or getattr(request.state, "request_id", None)
        or f"req-{uuid.uuid4()}"
    )
    # Vượt độ dài câu hỏi (content/message) là 413 QUESTION_TOO_LONG, không gộp chung vào 422 VALIDATION_ERROR
    if any(err.get("type") == "string_too_long" and err.get("loc") and err["loc"][-1] in {"content", "message"} for err in exc.errors()):
        return JSONResponse(status_code=413, content={
            "data": None,
            "error": {"code": "QUESTION_TOO_LONG", "message": "Câu hỏi quá dài. Vui lòng rút gọn hoặc tách thành nhiều câu hỏi. Lượt này chưa trừ hạn mức."},
            "request_id": request_id,
        }, headers={"X-Request-ID": request_id})
    details = [
        {"field": str(err["loc"][-1]) if err.get("loc") else "body", "message": err.get("msg", "")}
        for err in exc.errors()
    ]
    return JSONResponse(status_code=422, content={
        "data": None,
        "error": {"code": "VALIDATION_ERROR", "message": "Dữ liệu gửi lên không hợp lệ.", "details": details},
        "request_id": request_id,
    }, headers={"X-Request-ID": request_id})


@app.exception_handler(admission.AdmissionError)
async def admission_rejected(request: Request, exc: admission.AdmissionError) -> JSONResponse:
    request_id = getattr(exc, "request_id", None) or request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
    return JSONResponse(status_code=exc.status_code, content={
        "data": None, "error": {"code": exc.code, "message": exc.message}, "request_id": request_id,
    }, headers={"X-Request-ID": request_id})


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    request_id = (
        request.headers.get("X-Request-ID")
        or getattr(request.state, "request_id", None)
        or f"req-{uuid.uuid4().hex[:8]}"
    )
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
        client = await _get_procedure_client()
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


def _normalize_ip(raw: str | None) -> str | None:
    """Chuẩn hoá địa chỉ IPv4/IPv6 hợp lệ (dạng nén chuẩn); trả None nếu chuỗi rỗng, quá dài hoặc không phải IP."""
    if not raw:
        return None
    candidate = raw.strip()
    if not candidate or len(candidate) > 64:
        return None
    try:
        return ipaddress.ip_address(candidate).compressed
    except ValueError:
        return None


def _public_chat_subject(request: Request) -> str:
    """Định danh IP chống giả mạo cho chatbox công khai: chỉ tin CF-Connecting-IP khi bật TRUST_CLOUDFLARE_HEADERS,
    kiểm tra cú pháp IP chuẩn trên mọi header trước khi nhận làm khoá hạn mức."""
    if bool(getattr(get_settings(), "trust_cloudflare_headers", False)):
        cf_ip = _normalize_ip(request.headers.get("CF-Connecting-IP"))
        if cf_ip:
            return f"public-ip:{cf_ip}"
    real_ip = _normalize_ip(request.headers.get("X-Real-IP"))
    if real_ip:
        return f"public-ip:{real_ip}"
    raw_xff = request.headers.get("X-Forwarded-For") or ""
    if raw_xff:
        # Chỉ tin hop ngoài cùng bên phải do reverse proxy gắn thêm, không tin chuỗi đầu do client tự chèn.
        rightmost = _normalize_ip(raw_xff.split(",")[-1])
        if rightmost:
            return f"public-ip:{rightmost}"
    client_host = request.client.host if request.client else None
    valid_host = _normalize_ip(client_host)
    return f"public-ip:{valid_host or 'unknown'}"


@app.post("/api/v1/agent/chat")
async def chat(
    request: Request,
    payload: ChatRequest,
    x_request_id: str | None = Header(default=None),
):
    """
    Chatbox công khai: KHÔNG cần đăng nhập, không lưu lịch sử.
    Hỏi đáp pháp luật bằng RAG (rag-service) + Gemini, kèm thủ tục phù hợp nếu có.
    Áp dụng khóa nguyên tử in-flight và hạn mức token ngày theo IP để tránh người lạ spam cạn token AI.
    Người dùng đã đăng nhập muốn lưu hội thoại thì dùng /api/v1/agent/conversations.
    """
    request_id = x_request_id or f"req-{uuid.uuid4()}"
    subject = _public_chat_subject(request)
    today = quota.get_today_vn()
    slot_token = await run_in_threadpool(quota.acquire_inflight_slot, subject)
    try:
        await run_in_threadpool(
            quota.check_daily_quota,
            subject,
            target_date=today,
            limit_override=get_settings().public_chat_daily_token_limit,
        )

        # Admission TRƯỚC asyncio.gather: bị từ chối thì trả lỗi ngay, không để thủ tục tìm được che thành HTTP 200
        try:
            decision = await run_in_threadpool(
                admission.admit, payload.message, [], public=True, subject=subject, today=today,
                limit_override=get_settings().public_chat_daily_token_limit,
            )
        except admission.AdmissionError as exc:
            exc.request_id = request_id
            raise
        procedure, ai_result = await asyncio.gather(
            find_procedure(payload.message, request_id),
            agent.process_message(payload.message, decision.history, request_id=request_id,
                                  **({"budget": decision.budget} if decision.budget is not None else {})),
            return_exceptions=True,
        )
        if isinstance(procedure, BaseException):
            procedure = None

        if isinstance(ai_result, BaseException):
            if isinstance(ai_result, rag_client.RAGError):
                code, message = "RAG_SERVICE_ERROR", "Không thể truy xuất dữ liệu pháp luật lúc này, vui lòng thử lại."
            elif isinstance(ai_result, (llm_client.LLMError, case_budget.BudgetExhaustedError)):
                code, message = "AI_SERVICE_ERROR", "Dịch vụ AI đang gặp sự cố, vui lòng thử lại sau."
            else:
                raise ai_result
            logger.error("Chat lỗi %s, request_id=%s: %s", code, request_id, ai_result)
            if procedure is None:
                return failure(503, code, message, request_id)
            # AI lỗi nhưng vẫn tìm được thủ tục: trả thủ tục như trước đây
            answer, sources, source_details, truncated = "Đã tìm thấy thủ tục phù hợp.", [], {}, False
        else:
            meta = ai_result.get("metadata") or {}
            answer = ai_result["content"]
            sources = meta.get("rag_chunk_ids", [])
            source_details = meta.get("sources", {})
            truncated = bool(meta.get("truncated", False))
            await run_in_threadpool(
                quota.add_daily_tokens,
                subject,
                meta.get("tokens_used"),
                target_date=today,
                prompt=payload.message,
                completion=answer,
                metadata=meta,
            )

        data: dict = {"answer": answer, "procedure": procedure, "sources": sources}
        if source_details:
            data["source_details"] = source_details
        if truncated:
            data["truncated"] = truncated

        return {
            "data": data,
            "error": None,
            "request_id": request_id,
        }
    finally:
        quota.release_inflight_slot(subject, slot_token)
