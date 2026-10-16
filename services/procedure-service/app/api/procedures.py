import math
import uuid
from typing import Literal

from common_auth import CurrentUser
from common_auth.fastapi_dep import get_current_user, require_role
from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from app.schemas.procedure import ProcedureSearchRequest
from app.schemas.form_schema import ApplicationSubmitRequest
from app.repositories import procedure_record_repository
from app.services.procedure_service import ProcedureService

router = APIRouter()


def make_request_id(header_id: str | None) -> str:
    return header_id or f"req-{uuid.uuid4()}"


@router.get("/health")
def health():
    return {"status": "ok", "service": "procedure-service"}


@router.get("/domains")
def list_domains(x_request_id: str | None = Header(default=None)):
    """Lấy danh sách các lĩnh vực thủ tục kèm số lượng (phục vụ bộ lọc tab trên FE)."""
    domains = ProcedureService.get_domains()
    total_procedures = sum(d.get("count", 0) for d in domains)
    return {
        "data": domains,
        "total_procedures": total_procedures,
        "error": None,
        "request_id": make_request_id(x_request_id),
    }


@router.get("/domains/{domain}/procedures")
@router.get("/domains/{domain}")
def get_procedures_by_domain(
    domain: str,
    include_details: bool = Query(default=True, description="Bao gồm chi tiết hồ sơ, biểu phí, căn cứ pháp lý"),
    limit: int = Query(default=100, ge=1, le=200, description="Số lượng thủ tục tối đa"),
    x_request_id: str | None = Header(default=None),
):
    """Lấy danh sách tên và thông tin chi tiết thủ tục dựa vào lĩnh vực thủ tục (land, business)."""
    items = ProcedureService.get_procedures_by_domain(domain=domain, limit=limit, include_details=include_details)
    return {
        "data": {
            "domain": domain,
            "total": len(items),
            "items": items,
        },
        "error": None,
        "request_id": make_request_id(x_request_id),
    }


@router.get("/domains/{domain}/names")
def get_procedure_names_by_domain(
    domain: str,
    x_request_id: str | None = Header(default=None),
):
    """Lấy danh sách tên và mã thủ tục gọn nhẹ dựa vào lĩnh vực (cho Select box / Dropdown)."""
    items = ProcedureService.get_procedure_names_by_domain(domain=domain)
    return {
        "data": {
            "domain": domain,
            "total": len(items),
            "items": items,
        },
        "error": None,
        "request_id": make_request_id(x_request_id),
    }


@router.get("/form-schema/{procedure_code}")
@router.get("/{procedure_code}/form-schema")
def get_procedure_form_schema(
    procedure_code: str,
    x_request_id: str | None = Header(default=None),
):
    """
    HÀM TRUNG GIAN (DISPATCHER):
    Lấy cấu trúc biểu mẫu (Form Schema) của thủ tục cho Frontend render.
    Nhận mã thủ tục (ví dụ: DD-001 hoặc HKD-001) và điều phối đến đúng 1 trong 25 hàm chi tiết.
    Trả về: danh sách cột/trường, nhãn tiếng Việt, kiểu dữ liệu, bắt buộc, bảng đích...
    """
    req_id = make_request_id(x_request_id)
    try:
        schema = ProcedureService.get_form_schema(procedure_code)
        return {
            "data": schema.model_dump(),
            "error": None,
            "request_id": req_id,
        }
    except ValueError as e:
        return JSONResponse(
            status_code=404,
            content={
                "data": None,
                "error": {
                    "code": "SCHEMA_NOT_FOUND",
                    "message": str(e),
                },
                "request_id": req_id,
            },
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "data": None,
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": f"Lỗi xử lý schema: {str(e)}",
                },
                "request_id": req_id,
            },
        )


def error_response(status_code: int, code: str, message: str, request_id: str) -> JSONResponse:
    return JSONResponse(status_code=status_code,
                        content={"data": None, "error": {"code": code, "message": message}, "request_id": request_id})


@router.post("/applications")
def submit_application(
    payload: ApplicationSubmitRequest,
    current_user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Lưu hồ sơ của người đang đăng nhập vào land_procedure_records hoặc business_procedure_records."""
    req_id = make_request_id(x_request_id)
    record = ProcedureService.submit_application(
        procedure_code=payload.procedure_code,
        user_id=current_user.id,
        status=payload.status,
        form_data=payload.form_data,
        extra_data=payload.extra_data,
    )
    if record is None:
        return error_response(404, "PROCEDURE_NOT_FOUND", "Không tìm thấy thủ tục.", req_id)
    return {"data": record, "error": None, "request_id": req_id}


@router.get("/applications/{domain}/{record_id}")
def get_application_record(
    domain: str,
    record_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Chi tiết hồ sơ: chủ hồ sơ hoặc admin. Hồ sơ của người khác trả 404."""
    req_id = make_request_id(x_request_id)
    domain_clean = domain.strip().lower()
    if domain_clean not in ("land", "business"):
        return error_response(400, "INVALID_DOMAIN", "Domain phải là 'land' hoặc 'business'", req_id)

    owner_id = None if current_user.role == "admin" else current_user.id
    record = ProcedureService.get_application_record(domain=domain_clean, record_id=record_id, owner_id=owner_id)
    if not record:
        return error_response(404, "APPLICATION_NOT_FOUND", "Không tìm thấy hồ sơ đã lưu.", req_id)
    return {"data": record, "error": None, "request_id": req_id}


ApplicationDomain = Literal["land", "business"]
ApplicationStatus = Literal["draft", "submitted", "processing", "completed", "cancelled"]


def application_page(owner_id, domain, status, q, page, page_size, request_id):
    items, total = procedure_record_repository.list_records(owner_id, domain, status, q, page, page_size)
    return {
        "data": {
            "items": items,
            "pagination": {"page": page, "page_size": page_size, "total": total,
                           "total_pages": math.ceil(total / page_size)},
        },
        "error": None,
        "request_id": request_id,
    }


# Các route /applications phải khai báo trước "/{procedure_id}" để "applications" không bị hiểu là id thủ tục.
@router.get("/applications/me")
def list_my_applications(
    domain: ApplicationDomain | None = None,
    status: ApplicationStatus | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    current_user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Hồ sơ của người đang đăng nhập (chủ lấy từ JWT, không nhận user_id từ client)."""
    return application_page(current_user.id, domain, status, None, page, page_size, make_request_id(x_request_id))


@router.get("/applications/summary")
def application_summary(
    _: CurrentUser = Depends(require_role("admin")),
    x_request_id: str | None = Header(default=None),
):
    """Số hồ sơ theo lĩnh vực và trạng thái (chỉ admin)."""
    return {"data": procedure_record_repository.count_records(), "error": None, "request_id": make_request_id(x_request_id)}


@router.get("/applications")
def list_all_applications(
    domain: ApplicationDomain | None = None,
    status: ApplicationStatus | None = None,
    q: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    _: CurrentUser = Depends(require_role("admin")),
    x_request_id: str | None = Header(default=None),
):
    """Hồ sơ của mọi người (chỉ admin); q tìm theo tên, CCCD hoặc mã thủ tục."""
    return application_page(None, domain, status, q, page, page_size, make_request_id(x_request_id))


@router.get("")
def list_procedures(
    domain: str | None = Query(default=None, description="Lọc theo lĩnh vực: 'business', 'land'"),
    page: int = Query(default=1, ge=1, description="Số trang (bắt đầu từ 1)"),
    page_size: int = Query(default=20, ge=1, le=100, description="Số lượng phần tử mỗi trang"),
    q: str | None = Query(default=None, description="Từ khóa tìm kiếm nhanh"),
    search: str | None = Query(default=None, description="Từ khóa tìm kiếm (tùy chọn)"),
    x_request_id: str | None = Header(default=None),
):
    """Lấy danh sách thủ tục có phân trang, lọc theo lĩnh vực và từ khóa."""
    keyword = q or search
    result = ProcedureService.list_procedures(domain=domain, page=page, page_size=page_size, search=keyword)
    return {
        "data": result,
        "error": None,
        "request_id": make_request_id(x_request_id),
    }


@router.post("/search")
def search_procedures(
    payload: ProcedureSearchRequest,
    limit: int = Query(default=20, ge=1, le=50),
    x_request_id: str | None = Header(default=None),
):
    """Tìm kiếm thủ tục theo câu hỏi hoặc từ khóa của người dùng."""
    result = ProcedureService.search_procedures(payload.query, limit=limit)
    return {
        "data": result,
        "error": None,
        "request_id": make_request_id(x_request_id),
    }


@router.get("/{procedure_id}")
def get_procedure(
    procedure_id: str,
    x_request_id: str | None = Header(default=None),
):
    """Lấy chi tiết thủ tục theo ID (UUID) hoặc Mã thủ tục (HKD-001, DD-001...)."""
    req_id = make_request_id(x_request_id)
    procedure = ProcedureService.get_procedure(procedure_id)
    if not procedure:
        return JSONResponse(
            status_code=404,
            content={
                "data": None,
                "error": {
                    "code": "PROCEDURE_NOT_FOUND",
                    "message": "Không tìm thấy thủ tục.",
                },
                "request_id": req_id,
            },
        )

    return {
        "data": procedure,
        "error": None,
        "request_id": req_id,
    }


@router.get("/{procedure_id}/requirements")
def get_procedure_requirements(
    procedure_id: str,
    x_request_id: str | None = Header(default=None),
):
    """Lấy danh sách hồ sơ, giấy tờ cần chuẩn bị riêng cho thủ tục."""
    req_id = make_request_id(x_request_id)
    result = ProcedureService.get_procedure_requirements(procedure_id)
    if not result:
        return JSONResponse(
            status_code=404,
            content={
                "data": None,
                "error": {
                    "code": "PROCEDURE_NOT_FOUND",
                    "message": "Không tìm thấy thủ tục.",
                },
                "request_id": req_id,
            },
        )

    return {
        "data": result,
        "error": None,
        "request_id": req_id,
    }


@router.get("/{procedure_id}/conditions")
def get_procedure_conditions(
    procedure_id: str,
    x_request_id: str | None = Header(default=None),
):
    """Lấy danh sách điều kiện thực hiện của thủ tục."""
    req_id = make_request_id(x_request_id)
    result = ProcedureService.get_procedure_conditions(procedure_id)
    if not result:
        return JSONResponse(
            status_code=404,
            content={
                "data": None,
                "error": {
                    "code": "PROCEDURE_NOT_FOUND",
                    "message": "Không tìm thấy thủ tục.",
                },
                "request_id": req_id,
            },
        )

    return {
        "data": result,
        "error": None,
        "request_id": req_id,
    }


@router.get("/{procedure_id}/fees")
def get_procedure_fees(
    procedure_id: str,
    x_request_id: str | None = Header(default=None),
):
    """Lấy chi tiết bảng phí và lệ phí của thủ tục."""
    req_id = make_request_id(x_request_id)
    result = ProcedureService.get_procedure_fees(procedure_id)
    if not result:
        return JSONResponse(
            status_code=404,
            content={
                "data": None,
                "error": {
                    "code": "PROCEDURE_NOT_FOUND",
                    "message": "Không tìm thấy thủ tục.",
                },
                "request_id": req_id,
            },
        )

    return {
        "data": result,
        "error": None,
        "request_id": req_id,
    }
