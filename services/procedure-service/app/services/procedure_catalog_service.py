import logging
import math
from typing import Any, Dict, List, Optional

from app.repositories.procedure_repository import ProcedureRepository

logger = logging.getLogger(__name__)


class ProcedureCatalogService:
    """
    Dịch vụ chuyên trách tra cứu danh mục thủ tục hành chính công:
    - Danh sách thủ tục phân trang & lọc theo lĩnh vực
    - Tìm kiếm toàn văn thủ tục tiếng Việt có dấu/không dấu
    - Chi tiết thông tin thủ tục, giấy tờ yêu cầu, điều kiện, lệ phí
    - Thống kê các lĩnh vực (land, business...)
    - Kiểm tra sức khỏe kết nối cơ sở dữ liệu
    """

    @staticmethod
    def check_health() -> Dict[str, Any]:
        """Kiểm tra kết nối cơ sở dữ liệu PostgreSQL / Supabase"""
        return ProcedureRepository.check_database_health()

    @staticmethod
    def get_domains() -> List[Dict[str, Any]]:
        """Lấy danh sách các lĩnh vực hỗ trợ (land, business...) kèm số lượng thủ tục"""
        return ProcedureRepository.get_domains()

    @staticmethod
    def list_procedures(
        domain: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Lấy danh sách thủ tục hành chính có phân trang và bộ lọc"""
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        offset = (page - 1) * page_size
        items, total = ProcedureRepository.get_procedures(
            domain=domain, offset=offset, limit=page_size, search=search
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
    def search_procedures(query: str, limit: int = 20) -> Dict[str, Any]:
        """Tìm kiếm thủ tục theo từ khóa tiếng Việt (hỗ trợ cả không dấu và có dấu)"""
        items = ProcedureRepository.search_procedures(query_text=query, limit=limit)
        return {"items": items}

    @staticmethod
    def get_procedure(procedure_id_or_code: str) -> Optional[Dict[str, Any]]:
        """Lấy toàn bộ thông tin chi tiết thủ tục theo ID hoặc Mã (HKD-001, DD-001...)"""
        return ProcedureRepository.get_procedure_by_id_or_code(procedure_id_or_code)

    @staticmethod
    def get_procedures_by_domain(
        domain: str, limit: int = 100, include_details: bool = True
    ) -> List[Dict[str, Any]]:
        """Lấy danh sách các thủ tục chi tiết thuộc một lĩnh vực cụ thể"""
        return ProcedureRepository.get_procedures_by_domain(
            domain=domain, limit=limit, include_details=include_details
        )

    @staticmethod
    def get_procedure_names_by_domain(domain: str) -> List[Dict[str, Any]]:
        """Lấy danh sách rút gọn (ID, Mã, Tên) cho Select box / Dropdown"""
        return ProcedureRepository.get_procedure_names_by_domain(domain=domain)

    @staticmethod
    def get_procedure_requirements(procedure_id: str) -> Optional[Dict[str, Any]]:
        """Lấy danh sách các giấy tờ, tài liệu bắt buộc và bản sao yêu cầu"""
        proc = ProcedureRepository.get_procedure_by_id_or_code(procedure_id)
        if not proc:
            return None
        return {
            "procedure_id": proc["id"],
            "procedure_code": proc["code"],
            "procedure_name": proc["name"],
            "requirements": proc.get("requirements", []),
        }

    @staticmethod
    def get_procedure_conditions(procedure_id: str) -> Optional[Dict[str, Any]]:
        """Lấy danh sách các điều kiện bắt buộc để thực hiện thủ tục"""
        proc = ProcedureRepository.get_procedure_by_id_or_code(procedure_id)
        if not proc:
            return None
        return {
            "procedure_id": proc["id"],
            "procedure_code": proc["code"],
            "procedure_name": proc["name"],
            "conditions": proc.get("conditions", []),
        }

    @staticmethod
    def get_procedure_fees(procedure_id: str) -> Optional[Dict[str, Any]]:
        """Lấy thông tin biểu phí, lệ phí thực hiện thủ tục"""
        proc = ProcedureRepository.get_procedure_by_id_or_code(procedure_id)
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
