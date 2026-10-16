import logging
import uuid
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from common_auth.fastapi_dep import add_auth_error_handler
from app.api.procedures import router as procedures_router
from app.repositories.procedure_repository import FormDataInvalid, ProcedureDbUnavailable, ProcedureInvalidData

logger = logging.getLogger(__name__)

app = FastAPI(title="Procedure Service", version="0.1.0")

add_auth_error_handler(app)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    details = []
    for err in exc.errors():
        loc = err.get("loc", [])
        field_name = str(loc[-1]) if loc else "body"
        msg = err.get("msg", "Dữ liệu không hợp lệ")
        details.append({"field": field_name, "message": msg})

    return JSONResponse(
        status_code=422,
        content={
            "data": None,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Dữ liệu gửi lên không hợp lệ.",
                "details": details,
            },
            "request_id": request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}",
        },
    )


@app.exception_handler(ProcedureInvalidData)
async def invalid_data_handler(request: Request, exc: ProcedureInvalidData):
    if isinstance(exc, FormDataInvalid):
        error = {"code": "VALIDATION_ERROR", "message": "Hồ sơ còn thiếu hoặc sai thông tin, vui lòng kiểm tra các ô được đánh dấu.",
                 "details": exc.details}
    else:
        error = {"code": "VALIDATION_ERROR",
                 "message": "Dữ liệu hồ sơ không hợp lệ (quá dài hoặc sai định dạng), vui lòng kiểm tra lại."}
    return JSONResponse(
        status_code=422,
        content={
            "data": None,
            "error": error,
            "request_id": request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}",
        },
    )


@app.exception_handler(ProcedureDbUnavailable)
async def db_unavailable_handler(request: Request, exc: ProcedureDbUnavailable):
    return JSONResponse(
        status_code=503,
        content={
            "data": None,
            "error": {
                "code": "PROCEDURE_DB_UNAVAILABLE",
                "message": "Không kết nối được cơ sở dữ liệu thủ tục, vui lòng thử lại sau.",
            },
            "request_id": request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}",
        },
    )


@app.exception_handler(Exception)
async def internal_error_handler(request: Request, exc: Exception):
    logger.error(f"Internal server error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "data": None,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Hệ thống đang gặp lỗi, vui lòng thử lại sau.",
            },
            "request_id": request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}",
        },
    )


app.include_router(procedures_router, prefix="/api/v1/procedures", tags=["Procedures"])
