"""Classification catalogue for source documents, querying directly from PostgreSQL with fallback."""
import logging
import time
from sqlalchemy import text

logger = logging.getLogger(__name__)

DEFAULT_DOMAINS = [
    {"code": "land", "name": "Đất đai & Bất động sản"},
    {"code": "business", "name": "Doanh nghiệp & Hộ kinh doanh"},
    {"code": "cooperative", "name": "Hợp tác xã"},
    {"code": "social_insurance", "name": "Bảo hiểm xã hội"}
]

FALLBACK_ITEMS = [
    {"key": "DN-006", "code": "DN-006", "procedure_id": "0a2e901e-51d7-4df4-8e5c-2fb720628374", "name": "Thủ tục thuê nhà/mặt bằng làm Trụ sở chính doanh nghiệp", "domain": "business", "kind": "project_procedure"},
    {"key": "HKD-001", "code": "HKD-001", "procedure_id": "e3b2c140-6b1a-4f51-8d2c-3965158a8a41", "name": "Đăng ký thành lập hộ kinh doanh", "domain": "business", "kind": "project_procedure"},
    {"key": "DN-009", "code": "DN-009", "procedure_id": "773b5490-514a-4cf4-bbe0-21252db35e7a", "name": "Thủ tục đăng ký giải thể doanh nghiệp", "domain": "business", "kind": "project_procedure"},
    {"key": "HKD-002", "code": "HKD-002", "procedure_id": "b2c3d4e5-f6a7-4b5c-9d0e-1f2a3b4c5d6e", "name": "Thay đổi nội dung đăng ký hộ kinh doanh", "domain": "business", "kind": "project_procedure"},
    {"key": "DN-003", "code": "DN-003", "procedure_id": "4be0988c-2da5-4f18-a641-a0ffd8de1f10", "name": "Thủ tục đăng ký thành lập Công ty Cổ phần", "domain": "business", "kind": "project_procedure"},
    {"key": "DN-008", "code": "DN-008", "procedure_id": "b5f61819-a0da-41fd-a7bf-bc8c4e02dfb2", "name": "Thủ tục đăng ký thành lập Công ty Hợp danh", "domain": "business", "kind": "project_procedure"},
    {"key": "DN-004", "code": "DN-004", "procedure_id": "4e4fb6f1-4420-46ce-824b-8e7a00f917e1", "name": "Thủ tục đăng ký thành lập Công ty Trách nhiệm hữu hạn", "domain": "business", "kind": "project_procedure"},
    {"key": "DN-005", "code": "DN-005", "procedure_id": "cd401f44-dbcd-4e55-ab6e-e81da3b4d7e1", "name": "Thủ tục đăng ký thành lập Doanh nghiệp tư nhân", "domain": "business", "kind": "project_procedure"},
    {"key": "DD-008", "code": "DD-008", "procedure_id": "f8a9b0c1-d2e3-4f4a-b5c6-7d8e9f0a1b2c", "name": "Thủ tục chuyển mục đích sử dụng đất", "domain": "land", "kind": "project_procedure"},
    {"key": "DD-009", "code": "DD-009", "procedure_id": "a9b0c1d2-e3f4-4a5b-c6d7-8e9f0a1b2c3d", "name": "Thủ tục gia hạn thời hạn sử dụng đất", "domain": "land", "kind": "project_procedure"},
    {"key": "DD-003", "code": "DD-003", "procedure_id": "a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d", "name": "Tách thửa hoặc hợp thửa đất", "domain": "land", "kind": "project_procedure"},
    {"key": "DD-011", "code": "DD-011", "procedure_id": "c1d2e3f4-a5b6-4c7d-e8f9-0a1b2c3d4e5f", "name": "Thủ tục xóa đăng ký thế chấp quyền sử dụng đất (Giải chấp Sổ đỏ)", "domain": "land", "kind": "project_procedure"},
    {"key": "DD-006", "code": "DD-006", "procedure_id": "d6e7f8a9-b0c1-4d2e-3f4a-5b6c7d8e9f0a", "name": "Thủ tục đăng ký biến động do tặng cho, thừa kế quyền sử dụng đất, nhà ở", "domain": "land", "kind": "project_procedure"},
    {"key": "DD-001", "code": "DD-001", "procedure_id": "d5d9c792-5b9e-4e42-9a3b-2856247c7c30", "name": "Đăng ký biến động quyền sử dụng đất do chuyển nhượng", "domain": "land", "kind": "project_procedure"},
    {"key": "DD-010", "code": "DD-010", "procedure_id": "b0c1d2e3-f4a5-4b6c-d7e8-9f0a1b2c3d4e", "name": "Thủ tục đăng ký thế chấp quyền sử dụng đất, tài sản gắn liền với đất", "domain": "land", "kind": "project_procedure"},
    {"key": "DD-004", "code": "DD-004", "procedure_id": "b4c5d6e7-f8a9-4b0c-1d2e-3f4a5b6c7d8e", "name": "Thủ tục đăng ký đất đai, cấp Giấy chứng nhận lần đầu", "domain": "land", "kind": "project_procedure"},
    {"key": "coop-branch-registration", "code": "HTX-001", "procedure_id": None, "name": "Đăng ký chi nhánh, văn phòng đại diện hoặc địa điểm kinh doanh của hợp tác xã", "domain": "cooperative", "kind": "document_classification"},
    {"key": "vat-declaration", "code": "DN-012", "procedure_id": None, "name": "Kê khai thuế giá trị gia tăng theo phương pháp khấu trừ", "domain": "business", "kind": "document_classification"},
    {"key": "household-certificate-reissue", "code": "HKD-003", "procedure_id": None, "name": "Cấp lại Giấy chứng nhận đăng ký hộ kinh doanh", "domain": "business", "kind": "document_classification"},
    {"key": "social-insurance-authorization", "code": "BHXH-001", "procedure_id": None, "name": "Ủy quyền thực hiện thủ tục bảo hiểm xã hội", "domain": "social_insurance", "kind": "document_classification"},
    {"key": "land-allocation-rental", "code": "DD-013", "procedure_id": None, "name": "Giao đất, thuê đất, chuyển mục đích sử dụng đất; giao đất và giao rừng, thuê đất và thuê rừng", "domain": "land", "kind": "document_classification"},
    {"key": "land-survey", "code": "DD-015", "procedure_id": None, "name": "Yêu cầu đo đạc đất", "domain": "land", "kind": "document_classification"},
    {"key": "agricultural-land-term-confirmation", "code": "DD-016", "procedure_id": None, "name": "Xác nhận lại thời hạn sử dụng đất nông nghiệp", "domain": "land", "kind": "document_classification"},
    {"key": "land-allocation-method-change", "code": "DD-017", "procedure_id": None, "name": "Chuyển hình thức giao đất, cho thuê đất", "domain": "land", "kind": "document_classification"},
    {"key": "land-dispute-mediation", "code": "DD-014", "procedure_id": None, "name": "Hòa giải tranh chấp đất đai", "domain": "land", "kind": "document_classification"}
]

_CACHE = {"data": None, "timestamp": 0.0}
_CACHE_TTL = 30.0


def load_classifications_from_db():
    global _CACHE
    now = time.time()
    if _CACHE["data"] is not None and (now - _CACHE["timestamp"]) < _CACHE_TTL:
        return _CACHE["data"]

    try:
        from app.repositories.procedure_repository import db_connection, is_postgres_circuit_open
        if is_postgres_circuit_open():
            return None
        with db_connection(write=False) as conn:
            rows = [dict(r) for r in conn.execute(text("""
                SELECT key, code, name, domain, kind, procedure_id::text
                FROM public.reference_classifications
                ORDER BY code, name
            """)).mappings().all()]
            if rows and len(rows) > 0:
                result = {
                    "domains": DEFAULT_DOMAINS,
                    "items": rows,
                    "data_source": "database"
                }
                _CACHE["data"] = result
                _CACHE["timestamp"] = now
                return result
    except Exception as e:
        logger.warning(f"Could not load classifications from procedure DB: {e}")
    return None


def document_classifications():
    """Tải danh mục phân loại thủ tục: ưu tiên Database, fallback dữ liệu chuẩn nếu DB bận."""
    db_data = load_classifications_from_db()
    if db_data:
        return db_data
    return {
        "domains": DEFAULT_DOMAINS,
        "items": FALLBACK_ITEMS,
        "data_source": "fallback"
    }
