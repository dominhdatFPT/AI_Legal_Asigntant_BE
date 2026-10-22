"""
Unified Facade Service for Procedure Service.
Tích hợp các service chuyên biệt thành một điểm truy cập duy nhất để duy trì tính tương thích ngược 100%.

Các service thành phần:
- ProcedureCatalogService: Tra cứu danh mục thủ tục, tìm kiếm, lĩnh vực, lệ phí, điều kiện.
- ProcedureApplicationService: Quản lý vòng đời hồ sơ người dùng (bản nháp, cập nhật, nộp hồ sơ).
- procedure_form_service: Điều phối và cung cấp cấu trúc biểu mẫu (Form Schema) cho 25 thủ tục.
"""

from app.schemas.form_schema import ProcedureFormSchema
from app.services import procedure_form_service
from app.services.procedure_application_service import ProcedureApplicationService
from app.services.procedure_catalog_service import ProcedureCatalogService


class ProcedureService(ProcedureCatalogService, ProcedureApplicationService):
    """
    Unified Facade Service:
    Kế thừa đầy đủ các phương thức từ ProcedureCatalogService và ProcedureApplicationService.
    Bảo đảm tất cả các endpoint API và test suite gọi qua ProcedureService.<method> hoạt động ổn định.
    """

    @staticmethod
    def get_form_schema(procedure_code: str) -> ProcedureFormSchema:
        """Hàm trung gian gọi Dispatcher lấy form schema của thủ tục"""
        return procedure_form_service.get_procedure_form_schema(procedure_code)
