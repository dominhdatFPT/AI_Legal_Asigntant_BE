import uuid
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Header, Query
from fastapi.responses import Response

from app.api.procedures import error_response, make_request_id
from app.repositories.template_repository import TemplateRepository

# Công khai như các API tra cứu thủ tục khác: khách chưa đăng nhập vẫn xem được mẫu đơn.
# Dữ liệu lấy từ bảng form_templates (database/006); DB lỗi -> 503 PROCEDURE_DB_UNAVAILABLE như các API khác.
router = APIRouter()


@router.get("/templates")
def list_form_templates(
    domain: Literal["land", "business"] | None = None,
    procedure_code: str | None = Query(default=None, max_length=50),
    q: str | None = Query(default=None, max_length=200),
    x_request_id: str | None = Header(default=None),
):
    """Danh sách mẫu đơn tham khảo (đơn, tờ khai, hợp đồng mẫu) kèm lĩnh vực và định dạng file."""
    items = TemplateRepository.list_templates(domain=domain, procedure_code=procedure_code, q=q)
    return {
        "data": {"items": items, "total": len(items)},
        "error": None,
        "request_id": make_request_id(x_request_id),
    }


@router.get("/templates/{template_id}/file")
def get_form_template_file(
    template_id: str,
    download: bool = False,
    x_request_id: str | None = Header(default=None),
):
    """Xem (inline) hoặc tải (download=true) file mẫu.

    Xem mẫu Word thì trả bản PDF xem trước nếu có; tải về luôn trả file gốc.
    """
    try:
        template_id = str(uuid.UUID(template_id))
    except ValueError:
        template_id = None
    file = TemplateRepository.get_file(template_id, preview=not download) if template_id else None
    if file is None:
        return error_response(404, "TEMPLATE_NOT_FOUND", "Không tìm thấy mẫu đơn.", make_request_id(x_request_id))
    disposition = "attachment" if download else "inline"
    return Response(
        content=file["content"],
        media_type=file["media_type"],
        headers={"Content-Disposition": f"{disposition}; filename*=utf-8''{quote(file['file_name'])}"},
    )
