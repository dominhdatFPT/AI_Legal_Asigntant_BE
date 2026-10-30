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
from sqlalchemy.exc import SQLAlchemyError
from app.api.procedures import router as procedures_router
from app.api.templates import router as templates_router
from app.api.dynamic_form_templates import (
    admin_router as dft_admin_router,
    citizen_router as dft_citizen_router,
)
from app.core.exceptions import (
    TemplateNotFoundError,
    TemplateNotDraftError,
    TemplateInvalidStatusError,
)
from app.repositories.procedure_repository import (
    FormDataInvalid,
    ProcedureDbUnavailable,
    ProcedureInvalidData,
)

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
    logger.warning("Database thủ tục lỗi: %s - %s", type(exc).__name__, exc)
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


# Repository mẫu đơn dùng chung không tự bắt lỗi DB (mất kết nối, chưa chạy 007...) → cùng 503 như trên.
app.add_exception_handler(SQLAlchemyError, db_unavailable_handler)


@app.exception_handler(TemplateNotFoundError)
async def template_not_found_handler(request: Request, exc: TemplateNotFoundError):
    req_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
    return JSONResponse(
        status_code=404,
        content={
            "data": None,
            "error": {
                "code": exc.code,
                "message": exc.message,
            },
            "request_id": req_id,
        },
    )


@app.exception_handler(TemplateNotDraftError)
async def template_not_draft_handler(request: Request, exc: TemplateNotDraftError):
    req_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
    return JSONResponse(
        status_code=409,
        content={
            "data": None,
            "error": {
                "code": exc.code,
                "message": exc.message,
            },
            "request_id": req_id,
        },
    )


@app.exception_handler(TemplateInvalidStatusError)
async def template_invalid_status_handler(request: Request, exc: TemplateInvalidStatusError):
    req_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
    return JSONResponse(
        status_code=409,
        content={
            "data": None,
            "error": {
                "code": exc.code,
                "message": exc.message,
            },
            "request_id": req_id,
        },
    )


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
    logger.error(f"SQLAlchemy database error: {exc}")
    req_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
    return JSONResponse(
        status_code=503,
        content={
            "data": None,
            "error": {
                "code": "DATABASE_UNAVAILABLE",
                "message": "Không kết nối được cơ sở dữ liệu, vui lòng thử lại sau giây lát.",
            },
            "request_id": req_id,
        },
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


# === MẪU ĐƠN DÙNG CHUNG (Dynamic Form Templates) ===
# Mount TRƯỚC templates_router và procedures_router.
# Lý do: procedures_router có route "/{procedure_id}" (catch-all) ở cuối;
# nếu mount sau, request GET /form-templates bị nhầm thành procedure_id="form-templates" -> 404.
app.include_router(dft_admin_router, prefix="/api/v1/procedures", tags=["Dynamic Form Templates - Admin"])
app.include_router(dft_citizen_router, prefix="/api/v1/procedures", tags=["Dynamic Form Templates - Citizen"])
# Alias không có /v1 (Nginx rewrite /api/procedures/ -> /api/v1/procedures/)
app.include_router(dft_admin_router, prefix="/api/procedures", tags=["DFT Admin Alias"], include_in_schema=False)
app.include_router(dft_citizen_router, prefix="/api/procedures", tags=["DFT Citizen Alias"], include_in_schema=False)

# Kho mẫu đơn tham khảo file PDF/DOCX (templates_router)
app.include_router(templates_router, prefix="/api/v1/procedures", tags=["Form templates"])
app.include_router(templates_router, prefix="/api/procedures", tags=["Form templates Alias"], include_in_schema=False)
# Thủ tục hành chính (procedures_router)
app.include_router(procedures_router, prefix="/api/v1/procedures", tags=["Procedures"])
app.include_router(procedures_router, prefix="/api/procedures", tags=["Procedures-Alias"], include_in_schema=False)

# Mock Auth cho môi trường dev local độc lập (đáp ứng protectPage của FE khi chạy không có NGINX gateway)
if True:
    from fastapi import APIRouter
    dev_router = APIRouter(prefix="/api/users", tags=["Dev Mock Auth"])

    @dev_router.get("/me")
    async def dev_me(request: Request):
        role = request.cookies.get("dev_role") or request.headers.get("X-Dev-Role") or "admin"
        return {
            "data": {
                "id": "dev-user-01",
                "role": role,
                "full_name": "Quản Trị Viên Hệ Thống" if role == "admin" else "Nguyễn Văn Công Dân",
                "username": role,
                "email": f"{role}@dichvucong.gov.vn",
            }
        }

    @dev_router.post("/auth/login")
    async def dev_login(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        username = body.get("username", "admin")
        role = "admin" if "admin" in username else "citizen"
        return {
            "data": {
                "token": "dev-mock-jwt-token",
                "user": {
                    "id": "dev-user-01",
                    "role": role,
                    "full_name": "Quản Trị Viên" if role == "admin" else "Nguyễn Văn Công Dân",
                    "username": username,
                    "email": f"{username}@dichvucong.gov.vn",
                }
            }
        }

    @dev_router.get("/me/citizen-profile")
    async def dev_citizen_profile():
        return {
            "data": {
                "full_name": "Nguyễn Văn Công Dân",
                "identity_number": "001200000001",
                "date_of_birth": "1990-01-01",
                "issue_date": "2021-01-01",
                "issue_place": "Cục CS QLHC về TTXH",
                "permanent_address": "Số 1 Tràng Tiền, Hoàn Kiếm, Hà Nội",
                "phone": "0912345678",
                "email": "congdan@example.com",
            }
        }

    @dev_router.put("/me/citizen-profile")
    async def dev_put_citizen_profile():
        return {"data": {"success": True}}

    app.include_router(dev_router)

