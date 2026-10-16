import math
from typing import Any, Dict, Optional

from app.repositories.procedure_repository import FormDataInvalid, ProcedureRepository, db_connection
from app.repositories import procedure_record_repository
from app.services import procedure_form_service
from app.schemas.form_schema import ProcedureFormSchema


class ProcedureService:
    @staticmethod
    def list_procedures(domain: str | None = None, page: int = 1, page_size: int = 20, search: str | None = None):
        offset = (page - 1) * page_size
        items, total = ProcedureRepository.get_procedures(domain=domain, offset=offset, limit=page_size, search=search)
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
    def search_procedures(query: str, limit: int = 20):
        items = ProcedureRepository.search_procedures(query_text=query, limit=limit)
        return {"items": items}

    @staticmethod
    def get_procedure(procedure_id_or_code: str):
        return ProcedureRepository.get_procedure_by_id_or_code(procedure_id_or_code)

    @staticmethod
    def get_domains():
        return ProcedureRepository.get_domains()

    @staticmethod
    def get_procedures_by_domain(domain: str, limit: int = 100, include_details: bool = True):
        return ProcedureRepository.get_procedures_by_domain(domain=domain, limit=limit, include_details=include_details)

    @staticmethod
    def get_procedure_names_by_domain(domain: str):
        return ProcedureRepository.get_procedure_names_by_domain(domain=domain)

    @staticmethod
    def get_procedure_requirements(procedure_id_or_code: str):
        proc = ProcedureRepository.get_procedure_by_id_or_code(procedure_id_or_code)
        if not proc:
            return None
        return {
            "procedure_id": proc["id"],
            "procedure_code": proc["code"],
            "procedure_name": proc["name"],
            "requirements": proc.get("requirements", []),
        }

    @staticmethod
    def get_procedure_conditions(procedure_id_or_code: str):
        proc = ProcedureRepository.get_procedure_by_id_or_code(procedure_id_or_code)
        if not proc:
            return None
        return {
            "procedure_id": proc["id"],
            "procedure_code": proc["code"],
            "procedure_name": proc["name"],
            "conditions": proc.get("conditions", []),
        }

    @staticmethod
    def get_procedure_fees(procedure_id_or_code: str):
        proc = ProcedureRepository.get_procedure_by_id_or_code(procedure_id_or_code)
        if not proc:
            return None
        return {
            "procedure_id": proc["id"],
            "procedure_code": proc["code"],
            "procedure_name": proc["name"],
            "fee": proc.get("fee"),
            "fee_note": proc.get("fee_note"),
            "fees": proc.get("fees", []),
        }

    @staticmethod
    def get_form_schema(procedure_code: str) -> ProcedureFormSchema:
        """Hàm trung gian gọi Dispatcher lấy form schema của thủ tục"""
        return procedure_form_service.get_procedure_form_schema(procedure_code)

    @staticmethod
    def submit_application(
        procedure_code: str,
        user_id: str,
        status: str = "draft",
        form_data: Optional[Dict[str, Any]] = None,
        extra_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Lưu hồ sơ; None khi mã thủ tục không tồn tại. form_data sai schema → FormDataInvalid (422)."""
        try:
            schema = procedure_form_service.get_procedure_form_schema(procedure_code)
        except ValueError:
            with db_connection() as conn:
                if not procedure_record_repository.get_procedure_id_by_code(conn, procedure_code):
                    return None
            raise FormDataInvalid([{"field": "procedure_code", "message": "Thủ tục này chưa có biểu mẫu nên chưa nhận hồ sơ."}])
        errors = procedure_form_service.validate_form_data(schema, form_data or {})
        if errors:
            raise FormDataInvalid(errors)
        return procedure_record_repository.save_procedure_record(
            procedure_code=procedure_code,
            user_id=user_id,
            status=status,
            form_data=form_data,
            extra_data=extra_data,
        )

    @staticmethod
    def get_application_record(domain: str, record_id: str, owner_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Lấy lại hồ sơ đã lưu; owner_id=None chỉ dành cho admin."""
        return procedure_record_repository.get_procedure_record_by_id(domain=domain, record_id=record_id, owner_id=owner_id)
