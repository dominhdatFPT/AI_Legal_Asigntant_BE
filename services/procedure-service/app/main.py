import logging
import os
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.database import get_engine
from app.api.procedures import router as procedures_router
from app.api.templates import router as templates_router
from app.api.mau18 import router as mau18_router
from app.api.custom_form_templates import router as custom_form_templates_router
from app.api.office_editor import router as office_editor_router
from app.api.dynamic_form_templates import (
    admin_router as dft_admin_router,
    citizen_router as dft_citizen_router,
)
from app.core.exceptions import (
    TemplateNotFoundError,
    TemplateNotDraftError,
    TemplateInvalidStatusError,
)
from app.repositories.procedure_repository import FormDataInvalid, ProcedureDbUnavailable, ProcedureInvalidData

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Quản lý vòng đời khởi động và giải phóng tài nguyên của ứng dụng (M11)"""
    logger.info("Khởi động Procedure Service...")
    # Kiểm tra khởi tạo engine
    try:
        engine = get_engine()
        if engine:
            logger.info("Kết nối cơ sở dữ liệu đã sẵn sàng.")
            import threading
            def _bg_seed():
                try:
                    from app.api.custom_form_templates import ensure_builtin_seeded
                    from app.repositories.procedure_repository import db_connection
                    with db_connection(write=True) as conn:
                        ensure_builtin_seeded(conn)
                    logger.info("Hoàn tất kiểm tra seed mẫu đơn ban đầu.")
                except Exception as err:
                    logger.warning(f"Seed mẫu đơn ban đầu gặp lỗi: {err}")
            threading.Thread(target=_bg_seed, daemon=True).start()
    except Exception as e:
        logger.warning(f"Chưa thể kết nối CSDL khi khởi động: {e}")
    yield
    # Shutdown: giải phóng connection pool
    logger.info("Đang tắt Procedure Service và giải phóng connection pool...")
    engine = get_engine()
    if engine:
        engine.dispose()
        logger.info("Đã giải phóng connection pool thành công.")


app = FastAPI(
    title="Procedure Service",
    description="Microservice quản lý và xử lý thủ tục hành chính Đất đai & Doanh nghiệp",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS được quản lý tập trung an toàn tại API Gateway (NGINX cổng 8080).
# Chỉ kích hoạt CORSMiddleware cục bộ nếu biến môi trường STANDALONE_CORS=true.
if os.getenv("STANDALONE_CORS", "false").lower() in ("true", "1"):
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# Middleware đồng bộ Request ID tự động (L2, L3)
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    req_id = request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
    request.state.request_id = req_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = req_id
    return response


# Đăng ký handler lỗi của common_auth nếu có
try:
    from common_auth.fastapi_dep import add_auth_error_handler
    add_auth_error_handler(app)
except ImportError:
    logger.warning("Không tìm thấy common_auth handler, bỏ qua.")


# Exception handler cho HTTPException tiêu chuẩn (M2)
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    req_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "data": None,
            "error": {
                "code": f"HTTP_{exc.status_code}",
                "message": exc.detail,
            },
            "request_id": req_id,
        },
    )


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    details = []
    for err in exc.errors():
        loc = err.get("loc", [])
        field_name = str(loc[-1]) if loc else "body"
        msg = err.get("msg", "Dữ liệu không hợp lệ")
        details.append({"field": field_name, "message": msg})

    req_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
    return JSONResponse(
        status_code=422,
        content={
            "data": None,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Dữ liệu gửi lên không hợp lệ.",
                "details": details,
            },
            "request_id": req_id,
        },
    )


@app.exception_handler(ProcedureInvalidData)
async def invalid_data_handler(request: Request, exc: ProcedureInvalidData):
    req_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
    if isinstance(exc, FormDataInvalid):
        error = {
            "code": "VALIDATION_ERROR",
            "message": "Hồ sơ còn thiếu hoặc sai thông tin, vui lòng kiểm tra các ô được đánh dấu.",
            "details": exc.details,
        }
    else:
        error = {
            "code": "VALIDATION_ERROR",
            "message": "Dữ liệu hồ sơ không hợp lệ (quá dài hoặc sai định dạng), vui lòng kiểm tra lại.",
        }
    return JSONResponse(
        status_code=422,
        content={
            "data": None,
            "error": error,
            "request_id": req_id,
        },
    )


@app.exception_handler(ProcedureDbUnavailable)
async def db_unavailable_handler(request: Request, exc: ProcedureDbUnavailable):
    req_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
    return JSONResponse(
        status_code=503,
        content={
            "data": None,
            "error": {
                "code": "PROCEDURE_DB_UNAVAILABLE",
                "message": "Không kết nối được cơ sở dữ liệu thủ tục, vui lòng thử lại sau.",
            },
            "request_id": req_id,
        },
    )


# Dynamic form repository dùng SQLAlchemy và đẩy lỗi DB lên cùng response 503.
app.add_exception_handler(SQLAlchemyError, db_unavailable_handler)


@app.exception_handler(TemplateNotFoundError)
async def template_not_found_handler(request: Request, exc: TemplateNotFoundError):
    req_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
    return JSONResponse(
        status_code=404,
        content={"data": None, "error": {"code": exc.code, "message": exc.message}, "request_id": req_id},
    )


@app.exception_handler(TemplateNotDraftError)
async def template_not_draft_handler(request: Request, exc: TemplateNotDraftError):
    req_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
    return JSONResponse(
        status_code=409,
        content={"data": None, "error": {"code": exc.code, "message": exc.message}, "request_id": req_id},
    )


@app.exception_handler(TemplateInvalidStatusError)
async def template_invalid_status_handler(request: Request, exc: TemplateInvalidStatusError):
    req_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
    return JSONResponse(
        status_code=409,
        content={"data": None, "error": {"code": exc.code, "message": exc.message}, "request_id": req_id},
    )


@app.exception_handler(Exception)
async def internal_error_handler(request: Request, exc: Exception):
    logger.error(f"Internal server error: {exc}", exc_info=True)
    req_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
    return JSONResponse(
        status_code=500,
        content={
            "data": None,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Hệ thống đang gặp lỗi, vui lòng thử lại sau.",
            },
            "request_id": req_id,
        },
    )


# Dynamic form phải đăng ký trước templates/procedures router để route "/form-templates" không bị bắt nhầm bởi "/{procedure_id}".
app.include_router(dft_admin_router, prefix="/api/v1/procedures", tags=["Dynamic Form Templates - Admin"])
app.include_router(dft_citizen_router, prefix="/api/v1/procedures", tags=["Dynamic Form Templates - Citizen"])
app.include_router(dft_admin_router, prefix="/api/procedures", tags=["DFT Admin Alias"], include_in_schema=False)
app.include_router(dft_citizen_router, prefix="/api/procedures", tags=["DFT Citizen Alias"], include_in_schema=False)

# Kho mẫu đơn tham khảo file PDF/DOCX.
app.include_router(templates_router, prefix="/api/v1/procedures", tags=["Form templates"])
app.include_router(templates_router, prefix="/api/procedures", tags=["Form templates Alias"], include_in_schema=False)
app.include_router(mau18_router, prefix="/api/v1/procedures", tags=["Form templates"])
app.include_router(custom_form_templates_router, prefix="/api/v1/procedures", tags=["User configured forms"])
app.include_router(office_editor_router, prefix="/api/v1/procedures", tags=["Word editor"])
app.include_router(custom_form_templates_router, prefix="/api/procedures", tags=["User configured forms alias"], include_in_schema=False)
app.include_router(procedures_router, prefix="/api/v1/procedures", tags=["Procedures"])
app.include_router(procedures_router, prefix="/api/procedures", tags=["Procedures-Alias"], include_in_schema=False)
