import math
import uuid
from typing import Literal, Optional
from urllib.parse import quote
from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, status
from fastapi.responses import JSONResponse, Response
from common_auth.core import CurrentUser
from common_auth.fastapi_dep import get_current_user, require_role
from app.core.exceptions import (
    ApplicationLockedError,
    ApplicationNotFoundError,
    ApplicationPermissionError,
    ApplicationValidationError,
)
from app.core.security import get_optional_user
from app.schemas.procedure import ProcedureSearchRequest
from app.schemas.form_schema import ApplicationSubmitRequest, ApplicationUpdateRequest
from app.repositories import procedure_record_repository
from app.repositories.template_repository import TemplateRepository
from app.services.procedure_catalog_service import ProcedureCatalogService
from app.services.procedure_application_service import ProcedureApplicationService
from app.services.procedure_service import ProcedureService
from app.core.rabbitmq_procedure import procedure_mq
from app.services import procedure_form_service

router = APIRouter()


def make_request_id(header_id: str | None) -> str:
    return header_id or f"req-{uuid.uuid4()}"


def error_response(status_code: int, code: str, message: str, request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"data": None, "error": {"code": code, "message": message}, "request_id": request_id},
    )


@router.get("/health")
def health():
    return {"status": "ok", "service": "procedure-service"}


@router.get("/domains")
def list_domains(x_request_id: str | None = Header(default=None)):
    """Lấy danh sách các lĩnh vực thủ tục kèm số lượng (phục vụ bộ lọc tab trên FE)."""
    domains = ProcedureCatalogService.get_domains()
    total_procedures = sum(d.get("count", 0) for d in domains)
    return {
        "data": domains,
        "total_procedures": total_procedures,
        "error": None,
        "request_id": make_request_id(x_request_id),
    }


@router.get("/reference-classifications")
def reference_classifications(x_request_id: str | None = Header(default=None)):
    """Document groups and existing project procedures, without requiring the DB."""
    from app.services.reference_classifications import document_classifications
    return {"data": document_classifications(), "error": None, "request_id": make_request_id(x_request_id)}


@router.get("/domains/{domain}/procedures")
@router.get("/domains/{domain}")
def get_procedures_by_domain(
    domain: str,
    include_details: bool = Query(default=True, description="Bao gồm chi tiết hồ sơ, biểu phí, căn cứ pháp lý"),
    limit: int = Query(default=100, ge=1, le=200, description="Số lượng thủ tục tối đa"),
    x_request_id: str | None = Header(default=None),
):
    """Lấy danh sách tên và thông tin chi tiết thủ tục dựa vào lĩnh vực thủ tục (land, business)."""
    items = ProcedureCatalogService.get_procedures_by_domain(domain=domain, limit=limit, include_details=include_details)
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
    items = ProcedureCatalogService.get_procedure_names_by_domain(domain=domain)
    return {
        "data": {
            "domain": domain,
            "total": len(items),
            "items": items,
        },
        "error": None,
        "request_id": make_request_id(x_request_id),
    }


@router.get("/templates")
def list_templates(
    domain: Optional[str] = Query(default=None, description="Lọc theo lĩnh vực (land, business, all)"),
    procedure_code: Optional[str] = Query(default=None, description="Lọc theo mã thủ tục (DD-001, HKD-001...)"),
    q: Optional[str] = Query(default=None, description="Từ khóa tìm kiếm mẫu đơn"),
    x_request_id: str | None = Header(default=None),
):
    """
    Lấy danh sách các biểu mẫu trong Kho mẫu đơn tham khảo.
    Hỗ trợ lọc theo Lĩnh vực (domain), Thủ tục chi tiết (procedure_code) và tìm kiếm từ khóa (q).
    """
    items = TemplateRepository.list_templates(domain=domain, procedure_code=procedure_code, q=q)
    return {
        "data": {
            "items": items,
            "total": len(items),
        },
        "error": None,
        "request_id": make_request_id(x_request_id),
    }


@router.get("/templates/by-procedure/{procedure_code}/word")
def download_procedure_word_template(
    procedure_code: str,
    download: bool = Query(default=True, description="True để tải về, False để xem trước"),
    x_request_id: str | None = Header(default=None),
):
    """
    Tải nhanh file Word (.docx / .doc) chuẩn tương ứng với mã thủ tục chi tiết.
    """
    file_info = TemplateRepository.get_template_by_procedure_code(procedure_code)
    if not file_info:
        return JSONResponse(
            status_code=404,
            content={
                "data": None,
                "error": {
                    "code": "TEMPLATE_NOT_FOUND",
                    "message": f"Không tìm thấy biểu mẫu Word cho mã thủ tục '{procedure_code}'.",
                },
                "request_id": make_request_id(x_request_id),
            },
        )
    disposition = "attachment" if download else "inline"
    filename_encoded = quote(file_info["file_name"])
    return Response(
        content=file_info["file_bytes"],
        media_type=file_info["media_type"],
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{filename_encoded}",
            "X-Request-ID": make_request_id(x_request_id),
        },
    )


@router.get("/templates/by-procedure/{procedure_code}/pdf")
def download_procedure_pdf_template(
    procedure_code: str,
    download: bool = Query(default=False, description="True để tải về, False để xem trước trực tiếp trên trình duyệt"),
    x_request_id: str | None = Header(default=None),
):
    """
    Xem trước hoặc tải file PDF chuẩn tương ứng với mã thủ tục chi tiết.
    """
    file_info = TemplateRepository.get_template_by_procedure_code(procedure_code, preferred_format="PDF")
    if not file_info:
        return JSONResponse(
            status_code=404,
            content={
                "data": None,
                "error": {
                    "code": "TEMPLATE_NOT_FOUND",
                    "message": f"Không tìm thấy biểu mẫu PDF cho mã thủ tục '{procedure_code}'.",
                },
                "request_id": make_request_id(x_request_id),
            },
        )
    disposition = "attachment" if download else "inline"
    filename_encoded = quote(file_info["file_name"])
    return Response(
        content=file_info["file_bytes"],
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{filename_encoded}",
            "X-Request-ID": make_request_id(x_request_id),
        },
    )


@router.get("/templates/{template_id}/file")
def download_template_file(
    template_id: str,
    download: bool = Query(default=False, description="True để tải về, False để xem trước"),
    x_request_id: str | None = Header(default=None),
):
    """
    Stream file biểu mẫu (Word/PDF) từ Database để người dùng tải về hoặc xem trước.
    """
    file_info = TemplateRepository.get_template_file(template_id)
    if not file_info:
        return JSONResponse(
            status_code=404,
            content={
                "data": None,
                "error": {
                    "code": "TEMPLATE_NOT_FOUND",
                    "message": "Không tìm thấy file biểu mẫu yêu cầu.",
                },
                "request_id": make_request_id(x_request_id),
            },
        )
    is_viewable = file_info.get("format", "").upper() in ("PDF", "PNG", "JPG", "JPEG")
    disposition = "attachment" if (download or not is_viewable) else "inline"
    filename_encoded = quote(file_info["file_name"])
    return Response(
        content=file_info["file_bytes"],
        media_type=file_info["media_type"],
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{filename_encoded}",
            "X-Request-ID": make_request_id(x_request_id),
        },
    )


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
        schema = procedure_form_service.get_procedure_form_schema(procedure_code)
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


ApplicationDomain = Literal["land", "business"]
ApplicationStatus = Literal["draft", "submitted", "processing", "completed", "cancelled"]


def application_page(owner_id, domain, status, q, page, page_size, request_id):
    items, total = procedure_record_repository.list_records(owner_id, domain, status, q, page, page_size)
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    return {
        "data": {
            "items": items,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
        },
        "error": None,
        "request_id": request_id,
    }


@router.get("/applications/me")
def list_my_applications(
    domain: ApplicationDomain | None = None,
    status: ApplicationStatus | None = None,
    q: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    current_user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Hồ sơ của người đang đăng nhập (chủ lấy từ JWT, không nhận user_id từ client)."""
    return application_page(current_user.id, domain, status, q, page, page_size, make_request_id(x_request_id))


@router.get("/applications/summary")
def application_summary(
    _: CurrentUser = Depends(require_role("admin")),
    x_request_id: str | None = Header(default=None),
):
    """Số hồ sơ theo lĩnh vực và trạng thái (chỉ admin)."""
    return {
        "data": procedure_record_repository.count_records(),
        "error": None,
        "request_id": make_request_id(x_request_id),
    }


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


@router.post("/applications")
def submit_application(
    payload: ApplicationSubmitRequest,
    current_user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """Lưu hồ sơ của người đang đăng nhập vào land_procedure_records hoặc business_procedure_records."""
    req_id = make_request_id(x_request_id)
    record = ProcedureApplicationService.submit_application(
        procedure_code=payload.procedure_code,
        user_id=current_user.id,
        status=payload.status,
        form_data=payload.form_data,
        extra_data=payload.extra_data,
    )
    if record is None:
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
    return {"data": record, "error": None, "request_id": req_id}


@router.get("/applications/lookup/{code_or_id}")
def lookup_application(
    code_or_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """
    Tra cứu chi tiết tờ đơn hồ sơ bằng mã hồ sơ (ví dụ: DD-001-811F7E21),
    tiền tố ID hoặc UUID đầy đủ.
    Trả về toàn bộ dữ liệu form_data công dân đã kê khai và cấu trúc biểu mẫu chính thức.
    """
    req_id = make_request_id(x_request_id)
    owner_id = None if current_user.role == "admin" else current_user.id
    result = ProcedureApplicationService.lookup_application(code_or_id=code_or_id, owner_id=owner_id)
    if not result:
        return JSONResponse(
            status_code=404,
            content={
                "data": None,
                "error": {
                    "code": "APPLICATION_NOT_FOUND",
                    "message": f"Không tìm thấy hồ sơ với mã '{code_or_id}'. Vui lòng kiểm tra lại mã tra cứu.",
                },
                "request_id": req_id,
            },
        )
    return {
        "data": result,
        "error": None,
        "request_id": req_id,
    }


@router.get("/applications/lookup/{code_or_id}/export-pdf")
def export_application_pdf(
    code_or_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    x_request_id: str | None = Header(default=None),
):
    """
    Xuất tờ đơn hồ sơ đã kê khai hoàn chỉnh dưới dạng file PDF chuẩn A4 chính thức.
    Sử dụng server-side rendering với font tiếng Việt, không chứa URL hay thành phần web.
    """
    req_id = make_request_id(x_request_id)
    owner_id = None if current_user.role == "admin" else current_user.id
    result = ProcedureApplicationService.lookup_application(code_or_id=code_or_id, owner_id=owner_id)
    if not result:
        return JSONResponse(
            status_code=404,
            content={
                "data": None,
                "error": {
                    "code": "APPLICATION_NOT_FOUND",
                    "message": f"Không tìm thấy hồ sơ với mã '{code_or_id}'. Vui lòng kiểm tra lại mã tra cứu.",
                },
                "request_id": req_id,
            },
        )

    from app.services.pdf_export_service import generate_application_dossier_pdf

    pdf_bytes = generate_application_dossier_pdf(result)
    app_code = result.get("application_code") or code_or_id
    filename = f"To-don-{app_code}.pdf"
    filename_encoded = quote(filename)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename_encoded}",
            "X-Request-ID": req_id,
        },
    )


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
        return JSONResponse(
            status_code=400,
            content={
                "data": None,
                "error": {
                    "code": "INVALID_DOMAIN",
                    "message": "Domain phải là 'land' hoặc 'business'",
                },
                "request_id": req_id,
            },
        )

    owner_id = None if current_user.role == "admin" else current_user.id
    record = ProcedureApplicationService.get_application_record(domain=domain_clean, record_id=record_id, owner_id=owner_id)
    if not record:
        lookup_res = ProcedureApplicationService.lookup_application(code_or_id=record_id, owner_id=owner_id)
        if lookup_res and lookup_res.get("record"):
            record = lookup_res["record"]
    if not record:
        return JSONResponse(
            status_code=404,
            content={
                "data": None,
                "error": {
                    "code": "APPLICATION_NOT_FOUND",
                    "message": "Không tìm thấy hồ sơ đã lưu.",
                },
                "request_id": req_id,
            },
        )

    return {
        "data": record,
        "error": None,
        "request_id": req_id,
    }


@router.put("/applications/{domain}/{record_id}")
def update_application(
    domain: str,
    record_id: str,
    payload: ApplicationUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    x_request_id: Optional[str] = Header(default=None),
):
    """
    Cập nhật dữ liệu hồ sơ bản nháp (draft).
    Chỉ chủ sở hữu (user_id) mới có quyền sửa, và chỉ được sửa khi hồ sơ ở trạng thái nháp.
    """
    req_id = make_request_id(x_request_id)
    domain_clean = domain.strip().lower()
    if domain_clean not in ("land", "business"):
        return JSONResponse(
            status_code=400,
            content={
                "data": None,
                "error": {
                    "code": "INVALID_DOMAIN",
                    "message": "Domain phải là 'land' hoặc 'business'",
                },
                "request_id": req_id,
            },
        )

    try:
        updated = ProcedureApplicationService.update_application(
            domain=domain_clean,
            record_id=record_id,
            user_id=current_user.id,
            form_data=payload.form_data,
            extra_data=payload.extra_data,
            status=payload.status,
        )
        return {
            "data": updated,
            "error": None,
            "request_id": req_id,
        }
    except (ApplicationPermissionError, PermissionError) as pe:
        return JSONResponse(
            status_code=403,
            content={
                "data": None,
                "error": {
                    "code": "FORBIDDEN",
                    "message": str(pe),
                },
                "request_id": req_id,
            },
        )
    except ApplicationNotFoundError as ne:
        return JSONResponse(
            status_code=404,
            content={
                "data": None,
                "error": {
                    "code": ne.code,
                    "message": ne.message,
                },
                "request_id": req_id,
            },
        )
    except (ApplicationLockedError, ApplicationValidationError) as ce:
        return JSONResponse(
            status_code=400,
            content={
                "data": None,
                "error": {
                    "code": ce.code,
                    "message": ce.message,
                },
                "request_id": req_id,
            },
        )
    except ValueError as ve:
        err_msg = str(ve)
        status_code = 404 if "APPLICATION_NOT_FOUND" in err_msg else 400
        return JSONResponse(
            status_code=status_code,
            content={
                "data": None,
                "error": {
                    "code": "APPLICATION_NOT_FOUND" if status_code == 404 else "BAD_REQUEST",
                    "message": err_msg,
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
                    "code": "APPLICATION_UPDATE_ERROR",
                    "message": f"Không thể cập nhật hồ sơ: {str(e)}",
                },
                "request_id": req_id,
            },
        )


@router.delete("/applications/{domain}/{record_id}")
def delete_application(
    domain: str,
    record_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    x_request_id: Optional[str] = Header(default=None),
):
    """
    Xóa hồ sơ bản nháp (draft).
    Chỉ chủ sở hữu (user_id) mới có quyền xóa và hồ sơ chưa được nộp chính thức.
    """
    req_id = make_request_id(x_request_id)
    domain_clean = domain.strip().lower()
    if domain_clean not in ("land", "business"):
        return JSONResponse(
            status_code=400,
            content={
                "data": None,
                "error": {
                    "code": "INVALID_DOMAIN",
                    "message": "Domain phải là 'land' hoặc 'business'",
                },
                "request_id": req_id,
            },
        )

    try:
        ProcedureApplicationService.delete_application(
            domain=domain_clean,
            record_id=record_id,
            user_id=current_user.id,
        )
        return {
            "data": {"success": True, "message": "Đã xóa bản nháp hồ sơ thành công"},
            "error": None,
            "request_id": req_id,
        }
    except (ApplicationPermissionError, PermissionError) as pe:
        return JSONResponse(
            status_code=403,
            content={
                "data": None,
                "error": {
                    "code": "FORBIDDEN",
                    "message": str(pe),
                },
                "request_id": req_id,
            },
        )
    except ApplicationNotFoundError as ne:
        return JSONResponse(
            status_code=404,
            content={
                "data": None,
                "error": {
                    "code": ne.code,
                    "message": ne.message,
                },
                "request_id": req_id,
            },
        )
    except (ApplicationLockedError, ApplicationValidationError) as ce:
        return JSONResponse(
            status_code=400,
            content={
                "data": None,
                "error": {
                    "code": ce.code,
                    "message": ce.message,
                },
                "request_id": req_id,
            },
        )
    except ValueError as ve:
        err_msg = str(ve)
        status_code = 404 if "APPLICATION_NOT_FOUND" in err_msg else 400
        return JSONResponse(
            status_code=status_code,
            content={
                "data": None,
                "error": {
                    "code": "APPLICATION_NOT_FOUND" if status_code == 404 else "BAD_REQUEST",
                    "message": err_msg,
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
                    "code": "APPLICATION_DELETE_ERROR",
                    "message": f"Không thể xóa hồ sơ: {str(e)}",
                },
                "request_id": req_id,
            },
        )


@router.post("/applications/{domain}/{record_id}/submit")
def submit_draft_application(
    domain: str,
    record_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
    x_request_id: Optional[str] = Header(default=None),
):
    """
    Nộp chính thức một bộ hồ sơ đang ở trạng thái bản nháp.
    Thực hiện kiểm tra validation các trường bắt buộc theo biểu mẫu của thủ tục.
    """
    req_id = make_request_id(x_request_id)
    domain_clean = domain.strip().lower()
    if domain_clean not in ("land", "business"):
        return JSONResponse(
            status_code=400,
            content={
                "data": None,
                "error": {
                    "code": "INVALID_DOMAIN",
                    "message": "Domain phải là 'land' hoặc 'business'",
                },
                "request_id": req_id,
            },
        )

    try:
        submitted = ProcedureApplicationService.submit_application_by_id(
            domain=domain_clean,
            record_id=record_id,
            user_id=current_user.id,
        )
        # Route là `def` (chạy trong threadpool, không có event loop) nên không dùng asyncio.create_task;
        # BackgroundTasks chạy coroutine trên event loop sau khi trả response.
        background_tasks.add_task(
            procedure_mq.publish_application_submitted,
            record_id=str(record_id),
            domain=domain_clean,
            user_id=str(current_user.id),
        )
        return {
            "data": submitted,
            "error": None,
            "request_id": req_id,
        }
    except (ApplicationPermissionError, PermissionError) as pe:
        return JSONResponse(
            status_code=403,
            content={
                "data": None,
                "error": {
                    "code": "FORBIDDEN",
                    "message": str(pe),
                },
                "request_id": req_id,
            },
        )
    except ApplicationNotFoundError as ne:
        return JSONResponse(
            status_code=404,
            content={
                "data": None,
                "error": {
                    "code": ne.code,
                    "message": ne.message,
                },
                "request_id": req_id,
            },
        )
    except (ApplicationLockedError, ApplicationValidationError) as ce:
        return JSONResponse(
            status_code=400,
            content={
                "data": None,
                "error": {
                    "code": ce.code,
                    "message": ce.message,
                },
                "request_id": req_id,
            },
        )
    except ValueError as ve:
        err_msg = str(ve)
        status_code = 404 if "APPLICATION_NOT_FOUND" in err_msg else 400
        return JSONResponse(
            status_code=status_code,
            content={
                "data": None,
                "error": {
                    "code": "VALIDATION_ERROR" if "VALIDATION_ERROR" in err_msg else ("APPLICATION_NOT_FOUND" if status_code == 404 else "BAD_REQUEST"),
                    "message": err_msg,
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
                    "code": "APPLICATION_SUBMIT_ERROR",
                    "message": f"Không thể nộp hồ sơ: {str(e)}",
                },
                "request_id": req_id,
            },
        )



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
    result = ProcedureCatalogService.list_procedures(domain=domain, page=page, page_size=page_size, search=keyword)
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
    result = ProcedureCatalogService.search_procedures(payload.query, limit=limit)
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
    procedure = ProcedureCatalogService.get_procedure(procedure_id)
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
    result = ProcedureCatalogService.get_procedure_requirements(procedure_id)
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
    result = ProcedureCatalogService.get_procedure_conditions(procedure_id)
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
    result = ProcedureCatalogService.get_procedure_fees(procedure_id)
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
