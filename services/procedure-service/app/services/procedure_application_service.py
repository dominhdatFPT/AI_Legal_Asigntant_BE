import logging
import math
from typing import Any, Dict, Optional

from app.core.exceptions import (
    ApplicationLockedError,
    ApplicationNotFoundError,
    ApplicationPermissionError,
    ApplicationValidationError,
)
from app.repositories import procedure_record_repository
from app.services import procedure_form_service

logger = logging.getLogger(__name__)


from app.repositories.procedure_repository import FormDataInvalid, db_connection


class ProcedureApplicationService:
    """
    Dịch vụ chuyên trách quản lý vòng đời hồ sơ người dùng (Citizen Applications):
    - Tạo bản nháp hồ sơ đất đai và hộ kinh doanh
    - Cập nhật, chỉnh sửa nội dung hồ sơ nháp
    - Xóa bản nháp khi không còn nhu cầu
    - Lấy danh sách hồ sơ cá nhân có phân trang và bộ lọc
    - Kiểm tra hợp lệ dữ liệu biểu mẫu (Form Schema Validation) và nộp chính thức
    - Khóa an toàn hồ sơ đã nộp để chống can thiệp trái phép
    """

    @staticmethod
    def submit_application(
        procedure_code: str,
        user_id: Optional[str] = None,
        status: str = "draft",
        form_data: Optional[Dict[str, Any]] = None,
        extra_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Lưu hồ sơ; None khi mã thủ tục không tồn tại. form_data sai schema → FormDataInvalid (422)."""
        form_data = form_data or {}
        try:
            schema = procedure_form_service.get_procedure_form_schema(procedure_code)
        except ValueError:
            with db_connection() as conn:
                if not procedure_record_repository.get_procedure_id_by_code(conn, procedure_code):
                    return None
            raise FormDataInvalid([{"field": "procedure_code", "message": "Thủ tục này chưa có biểu mẫu nên chưa nhận hồ sơ."}])

        errors = procedure_form_service.validate_form_data(schema, form_data)
        if errors:
            raise FormDataInvalid(errors)

        return procedure_record_repository.save_procedure_record(
            procedure_code=procedure_code,
            user_id=user_id,
            status=status,
            form_data=form_data,
            extra_data=extra_data,
        )

    # Alias rõ nghĩa hơn cho hành vi tạo bản nháp (L6)
    create_draft_application = submit_application

    @staticmethod
    def get_application_record(domain: str, record_id: str, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Lấy lại dữ liệu hồ sơ đã lưu theo domain, ID và owner_id (None nếu admin)"""
        return procedure_record_repository.get_procedure_record_by_id(
            domain=domain, record_id=record_id, owner_id=owner_id
        )

    @staticmethod
    def lookup_application(code_or_id: str, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Tra cứu chi tiết tờ đơn hồ sơ bằng mã hồ sơ (ví dụ DD-001-811F7E21), tiền tố ID, hoặc UUID"""
        record = procedure_record_repository.lookup_procedure_record(code_or_id=code_or_id, owner_id=owner_id)
        if not record:
            return None

        schema = None
        try:
            s = procedure_form_service.get_procedure_form_schema(record["procedure_code"])
            schema = s.model_dump()
        except Exception:
            schema = None

        proc_meta = None
        try:
            with db_connection() as conn:
                from sqlalchemy import text
                row = conn.execute(
                    text("SELECT name, fee, receiving_agency_level, processing_days, description FROM public.procedures WHERE UPPER(code) = :code LIMIT 1"),
                    {"code": str(record["procedure_code"]).upper()},
                ).mappings().fetchone()
                if row:
                    proc_meta = dict(row)
        except Exception:
            proc_meta = None

        short_id = str(record["id"])[:8].upper()
        app_code = f"{record['procedure_code']}-{short_id}"
        return {
            "application_code": app_code,
            "record": record,
            "procedure": proc_meta,
            "schema": schema,
        }

    @staticmethod
    def list_applications(
        owner_id: Optional[str] = None,
        domain: Optional[str] = None,
        status: Optional[str] = None,
        q: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        user_id: Optional[str] = None,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Lấy danh sách hồ sơ người dùng có phân trang và bộ lọc"""
        eff_owner = owner_id if owner_id is not None else user_id
        eff_q = q if q is not None else search
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        items, total = procedure_record_repository.list_records(
            owner_id=eff_owner,
            domain=domain,
            status=status,
            q=eff_q,
            page=page,
            page_size=page_size,
        )
        total_pages = math.ceil(total / page_size) if total > 0 else 0
        return {
            "items": items,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
        }

    @staticmethod
    def update_application(
        domain: str,
        record_id: str,
        user_id: Optional[str] = None,
        form_data: Optional[Dict[str, Any]] = None,
        extra_data: Optional[Dict[str, Any]] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cập nhật dữ liệu bản nháp hồ sơ người dùng (chặn bypass chuyển sang submitted)"""
        # Chặn bypass validation khi chuyển sang submitted (C6)
        if status and status.strip().lower() == "submitted":
            raise ApplicationValidationError(
                "Không thể chuyển trạng thái sang 'submitted' qua API cập nhật. "
                "Vui lòng sử dụng endpoint /submit để nộp hồ sơ chính thức."
            )

        return procedure_record_repository.update_procedure_record(
            domain=domain,
            record_id=record_id,
            user_id=user_id,
            form_data=form_data,
            extra_data=extra_data,
            status=status,
        )

    @staticmethod
    def delete_application(domain: str, record_id: str, user_id: Optional[str] = None) -> bool:
        """Xóa bản nháp hồ sơ người dùng (chỉ cho phép xóa hồ sơ nháp của chính mình)"""
        return procedure_record_repository.delete_procedure_record(
            domain=domain,
            record_id=record_id,
            user_id=user_id,
        )

    @staticmethod
    def submit_application_by_id(
        domain: str, record_id: str, user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Kiểm tra tính hợp lệ dữ liệu biểu mẫu và nộp hồ sơ chính thức:
        1. Kiểm tra tồn tại hồ sơ (404)
        2. Kiểm tra phân quyền sở hữu BOLA (403)
        3. Kiểm tra trạng thái hồ sơ phải là 'draft' (400)
        4. Đối chiếu toàn bộ các trường bắt buộc (required: true) theo Schema biểu mẫu
        5. Cập nhật status = 'submitted' và khóa hồ sơ
        """
        rec = procedure_record_repository.get_procedure_record_by_id(
            domain=domain, record_id=record_id
        )
        if not rec:
            raise ApplicationNotFoundError("Không tìm thấy hồ sơ yêu cầu.")

        owner_id = rec.get("user_id")
        if owner_id:
            if not user_id or str(owner_id) != str(user_id):
                raise ApplicationPermissionError("FORBIDDEN: Bạn không có quyền nộp hồ sơ này.")

        current_status = rec.get("status") or "draft"
        if current_status != "draft":
            raise ApplicationLockedError(
                f"Chỉ có thể nộp hồ sơ ở trạng thái nháp ('draft'). Trạng thái hiện tại: '{current_status}'."
            )

        # Kiểm tra validation các trường bắt buộc theo schema
        procedure_code = rec.get("procedure_code")
        if procedure_code:
            try:
                schema = procedure_form_service.get_procedure_form_schema(procedure_code)
                form_data = rec.get("form_data") or {}
                missing_fields = []
                for field in schema.fields:
                    if field.required:
                        val = form_data.get(field.name)
                        if val is None or (isinstance(val, str) and not val.strip()):
                            missing_fields.append(f"{field.label} ({field.name})")

                if missing_fields:
                    raise ApplicationValidationError(
                        f"Hồ sơ còn thiếu các trường bắt buộc: {', '.join(missing_fields)}"
                    )
            except ApplicationValidationError:
                raise
            except Exception as e:
                logger.warning(f"Lỗi khi validate form schema trước khi nộp: {e}")

        return procedure_record_repository.submit_procedure_record(
            domain=domain,
            record_id=record_id,
            user_id=user_id,
        )
