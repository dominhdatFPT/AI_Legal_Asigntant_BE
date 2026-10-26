import logging
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, Header, Query, Response, status
from fastapi.responses import JSONResponse

from common_auth.core import CurrentUser
from common_auth.fastapi_dep import require_role
from app.core.security import get_current_user
from app.api.procedures import make_request_id
from app.core.exceptions import TemplateNotFoundError
from app.schemas.dynamic_form_template import (
    ContentIn,
    DomainEnum,
    TemplateStatus,
    validate_for_publish,
)
from app.repositories.dynamic_form_template_repository import DynamicFormTemplateRepository

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/admin/form-templates", tags=["Dynamic Form Templates - Admin"])
citizen_router = APIRouter(prefix="/form-templates", tags=["Dynamic Form Templates - Citizen"])


# ==============================================================================
# ADMIN API (Bắt buộc quyền admin qua require_role("admin"))
# ==============================================================================

@admin_router.get("")
def list_admin_templates(
    domain: Optional[DomainEnum] = Query(default=None),
    status_filter: Optional[TemplateStatus] = Query(default=None, alias="status"),
    include_deleted: bool = Query(default=False),
    x_request_id: Optional[str] = Header(default=None),
    admin_user: CurrentUser = Depends(require_role("admin")),
):
    """Danh sách tất cả version, mọi trạng thái cho Admin."""
    req_id = make_request_id(x_request_id)
    items, total = DynamicFormTemplateRepository.list_admin(
        domain=domain.value if domain else None,
        status=status_filter.value if status_filter else None,
        include_deleted=include_deleted,
    )
    return {
        "data": {"items": items, "total": total},
        "error": None,
        "request_id": req_id,
    }


@admin_router.get("/{template_id}")
def get_admin_template(
    template_id: UUID,
    x_request_id: Optional[str] = Header(default=None),
    admin_user: CurrentUser = Depends(require_role("admin")),
):
    """Xem chi tiết 1 version (kể cả nháp hoặc đã xóa)."""
    req_id = make_request_id(x_request_id)
    template = DynamicFormTemplateRepository.get_by_id(str(template_id),include_deleted=True)
    return {
        "data": template,
        "error": None,
        "request_id": req_id,
    }


@admin_router.post("", status_code=status.HTTP_201_CREATED)
def create_template(
    body: ContentIn,
    x_request_id: Optional[str] = Header(default=None),
    admin_user: CurrentUser = Depends(require_role("admin")),
):
    """Tạo mẫu đơn mới (version 1, status = draft)."""
    req_id = make_request_id(x_request_id)
    created = DynamicFormTemplateRepository.create(
        content_dict=body.model_dump(),
        user_id=getattr(admin_user, "id", None),
    )
    return {
        "data": created,
        "error": None,
        "request_id": req_id,
    }


@admin_router.put("/{template_id}")
def update_template(
    template_id: UUID,
    body: ContentIn,
    x_request_id: Optional[str] = Header(default=None),
    admin_user: CurrentUser = Depends(require_role("admin")),
):
    """Chỉnh sửa bản nháp (status = draft). Trả về 409 nếu không phải draft."""
    req_id = make_request_id(x_request_id)
    updated = DynamicFormTemplateRepository.update(
        template_id=str(template_id),
        content_dict=body.model_dump(),
        user_id=getattr(admin_user, "id", None),
    )
    return {
        "data": updated,
        "error": None,
        "request_id": req_id,
    }


@admin_router.post("/{template_id}/publish")
def publish_template(
    template_id: UUID,
    x_request_id: Optional[str] = Header(default=None),
    admin_user: CurrentUser = Depends(require_role("admin")),
):
    """Xuất bản mẫu đơn (draft hoặc hidden -> published).
    Kiểm tra nghiêm ngặt cấu trúc trước khi xuất bản.
    """
    req_id = make_request_id(x_request_id)
    current = DynamicFormTemplateRepository.get_by_id(str(template_id),include_deleted=False)

    try:
        content_obj = ContentIn(**(current.get("content") or {}))
    except Exception as exc:
        return JSONResponse(
            status_code=422,
            content={
                "data": None,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Nội dung mẫu đơn không đúng định dạng.",
                    "details": [{"field": "content", "message": str(exc)}],
                },
                "request_id": req_id,
            },
        )

    val_errors = validate_for_publish(content_obj)
    if val_errors:
        return JSONResponse(
            status_code=422,
            content={
                "data": None,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Mẫu đơn chưa đủ điều kiện xuất bản.",
                    "details": val_errors,
                },
                "request_id": req_id,
            },
        )

    published = DynamicFormTemplateRepository.publish(
        template_id=str(template_id),
        user_id=getattr(admin_user, "id", None),
    )
    return {
        "data": published,
        "error": None,
        "request_id": req_id,
    }


@admin_router.post("/{template_id}/hide")
def hide_template(
    template_id: UUID,
    x_request_id: Optional[str] = Header(default=None),
    admin_user: CurrentUser = Depends(require_role("admin")),
):
    """Ẩn mẫu đơn khỏi công dân (published -> hidden)."""
    req_id = make_request_id(x_request_id)
    hidden = DynamicFormTemplateRepository.hide(
        template_id=str(template_id),
        user_id=getattr(admin_user, "id", None),
    )
    return {
        "data": hidden,
        "error": None,
        "request_id": req_id,
    }


@admin_router.post("/{template_id}/new-version")
def new_version_template(
    template_id: UUID,
    response: Response,
    x_request_id: Optional[str] = Header(default=None),
    admin_user: CurrentUser = Depends(require_role("admin")),
):
    """Tạo bản nháp mới cùng nhóm.
    Nếu nhóm đã có bản nháp chưa xóa thì trả về bản nháp đó (HTTP 200).
    Nếu tạo bản mới thành công trả HTTP 201.
    """
    req_id = make_request_id(x_request_id)
    row, already_existed = DynamicFormTemplateRepository.new_version(
        template_id=str(template_id),
        user_id=getattr(admin_user, "id", None),
    )
    if already_existed:
        response.status_code = status.HTTP_200_OK
    else:
        response.status_code = status.HTTP_201_CREATED

    return {
        "data": row,
        "error": None,
        "request_id": req_id,
    }


@admin_router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(
    template_id: UUID,
    admin_user: CurrentUser = Depends(require_role("admin")),
):
    """Xóa mềm mẫu đơn (ghi nhận deleted_at)."""
    DynamicFormTemplateRepository.soft_delete(str(template_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@admin_router.post("/{template_id}/restore")
def restore_template(
    template_id: UUID,
    x_request_id: Optional[str] = Header(default=None),
    admin_user: CurrentUser = Depends(require_role("admin")),
):
    """Khôi phục mẫu đơn đã xóa mềm."""
    req_id = make_request_id(x_request_id)
    restored = DynamicFormTemplateRepository.restore(str(template_id))
    return {
        "data": restored,
        "error": None,
        "request_id": req_id,
    }


# ==============================================================================
# CITIZEN API (Công khai, chỉ trả bản published chưa xóa)
# ==============================================================================

@citizen_router.get("")
def list_published_templates(
    domain: Optional[DomainEnum] = Query(default=None),
    procedure_id: Optional[str] = Query(default=None),
    x_request_id: Optional[str] = Header(default=None),
):
    """Danh sách các mẫu đơn đang xuất bản (bản mới nhất mỗi nhóm)."""
    req_id = make_request_id(x_request_id)
    items, total = DynamicFormTemplateRepository.list_published(
        domain=domain.value if domain else None,
        procedure_id=procedure_id,
    )
    return {
        "data": {"items": items, "total": total},
        "error": None,
        "request_id": req_id,
    }


@citizen_router.get("/{template_id}")
def get_published_template(
    template_id: UUID,
    x_request_id: Optional[str] = Header(default=None),
):
    """Chi tiết mẫu đơn đang xuất bản. Trả 404 nếu không tồn tại hoặc chưa xuất bản."""
    req_id = make_request_id(x_request_id)
    template = DynamicFormTemplateRepository.get_by_id(str(template_id),include_deleted=False)
    if template.get("status") != "published":
        raise TemplateNotFoundError("Mẫu đơn không tồn tại hoặc đã ngừng sử dụng.")
    return {
        "data": template,
        "error": None,
        "request_id": req_id,
    }
