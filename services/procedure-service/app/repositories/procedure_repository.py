import copy
import logging
import time
import unicodedata
from contextlib import contextmanager

import requests
from requests.adapters import HTTPAdapter
from sqlalchemy import text
from sqlalchemy.exc import DataError, SQLAlchemyError

from app.database import (
    database_url,
    get_engine,
    get_supabase_config,
    is_postgres_circuit_open,
    mark_postgres_failed,
)

logger = logging.getLogger(__name__)

# Reusable HTTP Session cho PostgREST fallback
_proc_session = requests.Session()
_proc_adapter = HTTPAdapter(pool_connections=5, pool_maxsize=10, max_retries=1)
_proc_session.mount("http://", _proc_adapter)
_proc_session.mount("https://", _proc_adapter)

# In-Memory Cache cho danh mục thủ tục (giảm 99% tải DB)
_proc_cache: dict[str, tuple[float, any]] = {}
CACHE_TTL = 300.0  # 5 phút cache trong RAM


def _get_supabase_headers() -> dict:
    _, anon_key = get_supabase_config()
    return {
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}",
        "Content-Type": "application/json",
    }


class ProcedureDbUnavailable(Exception):
    """Không đọc/ghi được database thủ tục. Không bao giờ thay bằng dữ liệu giả."""


class ProcedureInvalidData(Exception):
    """Database từ chối giá trị (quá dài, vượt khoảng số, sai kiểu): lỗi dữ liệu gửi lên, không phải DB sập."""


class FormDataInvalid(ProcedureInvalidData):
    """form_data không khớp form schema của thủ tục; details: [{field, message}] để FE tô đỏ từng ô."""

    def __init__(self, details: list[dict]):
        super().__init__("FormDataInvalid")
        self.details = details


@contextmanager
def db_connection(write: bool = False):
    try:
        engine = get_engine()
    except SQLAlchemyError as exc:  # URL sai định dạng
        raise ProcedureDbUnavailable(type(exc).__name__) from exc
    if engine is None:
        raise ProcedureDbUnavailable("PROCEDURE_DATABASE_URL chưa cấu hình")
    try:
        with (engine.begin() if write else engine.connect()) as conn:
            yield conn
    except DataError as exc:
        raise ProcedureInvalidData(type(exc).__name__) from exc
    except SQLAlchemyError as exc:
        logger.warning("Lỗi truy vấn database thủ tục: %s - %s", type(exc).__name__, exc)
        raise ProcedureDbUnavailable(type(exc).__name__) from exc


def _normalize_domain(domain: str | None) -> str | None:
    if not domain:
        return domain
    d = domain.strip().lower()
    mapping = {
        "dat_dai": "land",
        "ho_kinh_doanh": "business",
        "doanh_nghiep": "business",
    }
    return mapping.get(d, d)


def remove_accents(input_str: str) -> str:
    """Loại bỏ dấu tiếng Việt để tìm kiếm không dấu"""
    if not input_str:
        return ""
    nfkd = unicodedata.normalize("NFKD", input_str)
    no_accent = "".join([c for c in nfkd if not unicodedata.combining(c)])
    return no_accent.replace("đ", "d").replace("Đ", "D")


def _fee_formatted(fee) -> str:
    return f"{fee:,} ₫".replace(",", ".") if fee and fee > 0 else "0 ₫ (Miễn phí)"


def _rows(conn, sql: str, params: dict | None = None) -> list[dict]:
    items = [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]
    for item in items:
        if item.get("id") is not None:
            item["id"] = str(item["id"])
    return items


SUMMARY_COLUMNS = "id, code, name, domain, description, fee, processing_days"


MOCK_PROCEDURES = [
    {
        "id": "e3b2c140-6b1a-4f51-8d2c-3965158a8a41",
        "code": "HKD-001",
        "name": "Đăng ký thành lập hộ kinh doanh",
        "domain": "business",
        "description": "Cấp giấy chứng nhận đăng ký hộ kinh doanh cá thể cho cá nhân hoặc các thành viên hộ gia đình.",
        "processing_days": 3,
        "submission_channels": ["direct", "online"],
        "receiving_agency_level": "district",
        "fee": 100000,
        "fee_note": "Miễn lệ phí nếu nộp hồ sơ đăng ký qua mạng điện tử",
        "effective_from": "2021-01-04",
        "updated_at": "2026-10-02T02:00:00Z",
        "status": "active",
        "legal_bases": [
            {
                "doc_number": "01/2021/NĐ-CP",
                "title": "Nghị định về đăng ký doanh nghiệp và hộ kinh doanh",
                "article_ref": "Điều 87",
                "source_url": "https://thuvienphapluat.vn/van-ban/Doanh-nghiep/Nghi-dinh-01-2021-ND-CP-dang-ky-doanh-nghiep-461408.aspx",
                "order_index": 1,
            },
            {
                "doc_number": "59/2020/QH14",
                "title": "Luật Doanh nghiệp 2020",
                "article_ref": "Chương VIII",
                "source_url": "https://thuvienphapluat.vn/van-ban/Doanh-nghiep/Luat-Doanh-nghiep-so-59-2020-QH14-445831.aspx",
                "order_index": 2,
            },
        ],
        "requirements": [
            {
                "id": "e3b2c140-req-001",
                "name": "Giấy đề nghị đăng ký hộ kinh doanh",
                "description": "Bản chính Giấy đề nghị đăng ký hộ kinh doanh theo mẫu quy định",
                "is_required": True,
                "copies": 1,
                "document_form": "original",
                "form_code": "Phụ lục III-1",
                "note": "Kèm chữ ký của tất cả các thành viên tham gia hộ kinh doanh",
                "order_index": 1,
            },
            {
                "id": "e3b2c140-req-002",
                "name": "Bản sao CCCD/CMND/Hộ chiếu",
                "description": "Bản sao hợp lệ giấy tờ pháp lý của cá nhân/các thành viên hộ gia đình",
                "is_required": True,
                "copies": 1,
                "document_form": "copy",
                "form_code": None,
                "note": "Xuất trình bản chính để đối chiếu hoặc bản chứng thực",
                "order_index": 2,
            },
            {
                "id": "e3b2c140-req-003",
                "name": "Hợp đồng thuê địa điểm kinh doanh hoặc giấy chứng nhận quyền sở hữu",
                "description": "Văn bản chứng minh quyền sử dụng hợp pháp địa điểm kinh doanh",
                "is_required": False,
                "copies": 1,
                "document_form": "copy",
                "form_code": None,
                "note": "Cần nộp nếu địa điểm kinh doanh thuê hoặc mượn",
                "order_index": 3,
            },
        ],
        "conditions": [
            {
                "id": "e3b2c140-cond-001",
                "condition_code": "COND-01",
                "title": "Công dân Việt Nam đủ 18 tuổi",
                "description": "Cá nhân, thành viên hộ gia đình là công dân Việt Nam đủ 18 tuổi, có năng lực hành vi dân sự đầy đủ.",
                "is_mandatory": True,
                "order_index": 1,
            },
            {
                "id": "e3b2c140-cond-002",
                "condition_code": "COND-02",
                "title": "Không thuộc đối tượng bị cấm thành lập HKD",
                "description": "Không đồng thời là chủ hộ kinh doanh khác, không là chủ doanh nghiệp tư nhân.",
                "is_mandatory": True,
                "order_index": 2,
            },
        ],
        "fees": [
            {
                "id": "e3b2c140-fee-001",
                "fee_type": "administrative_fee",
                "name": "Lệ phí cấp Giấy chứng nhận đăng ký hộ kinh doanh",
                "amount": 100000,
                "unit": "lần",
                "is_exemptible": True,
                "note": "Miễn lệ phí nếu đăng ký qua cổng dịch vụ công trực tuyến",
            }
        ],
    },
    {
        "id": "d5d9c792-5b9e-4e42-9a3b-2856247c7c30",
        "code": "DD-001",
        "name": "Đăng ký biến động quyền sử dụng đất do chuyển nhượng",
        "domain": "land",
        "description": "Thủ tục sang tên, chuyển nhượng quyền sử dụng đất, quyền sở hữu nhà ở và tài sản khác gắn liền với đất theo quy định của Luật Đất đai 2024.",
        "processing_days": 10,
        "submission_channels": ["direct", "online", "postal"],
        "receiving_agency_level": "district",
        "fee": 0,
        "fee_note": "Lệ phí địa chính và phí thẩm định hồ sơ theo mức thu do HĐND cấp tỉnh/thành phố quy định",
        "effective_from": "2024-08-01",
        "updated_at": "2026-10-02T02:00:00Z",
        "status": "active",
        "legal_bases": [
            {
                "doc_number": "31/2024/QH15",
                "title": "Luật Đất đai 2024",
                "article_ref": "Điều 133",
                "source_url": "https://thuvienphapluat.vn/van-ban/Bat-dong-san/Luat-Dat-dai-2024-31-2024-QH15-564560.aspx",
                "order_index": 1,
            },
            {
                "doc_number": "101/2024/NĐ-CP",
                "title": "Nghị định quy định về điều tra cơ bản đất đai; đăng ký, cấp Giấy chứng nhận quyền sử dụng đất",
                "article_ref": "Điều 37, 38",
                "source_url": "https://thuvienphapluat.vn/van-ban/Bat-dong-san/Nghi-dinh-101-2024-ND-CP-dieu-tra-co-ban-dat-dai-dang-ky-cap-Giay-chung-nhan-quyen-su-dung-dat-619460.aspx",
                "order_index": 2,
            },
        ],
        "requirements": [
            {
                "id": "d5d9c792-req-001",
                "name": "Giấy chứng nhận quyền sử dụng đất (bản gốc)",
                "description": "Bản gốc Giấy chứng nhận quyền sử dụng đất, quyền sở hữu nhà ở (Sổ đỏ / Sổ hồng)",
                "is_required": True,
                "copies": 1,
                "document_form": "original",
                "form_code": None,
                "note": "Phải là bản gốc hợp pháp",
                "order_index": 1,
            },
            {
                "id": "d5d9c792-req-002",
                "name": "Hợp đồng chuyển nhượng quyền sử dụng đất đã công chứng",
                "description": "Hợp đồng chuyển nhượng quyền sử dụng đất được công chứng hoặc chứng thực",
                "is_required": True,
                "copies": 1,
                "document_form": "certified_copy",
                "form_code": None,
                "note": "Công chứng tại tổ chức hành nghề công chứng hoặc chứng thực tại UBND cấp xã",
                "order_index": 2,
            },
            {
                "id": "d5d9c792-req-003",
                "name": "Đơn đăng ký biến động đất đai, tài sản gắn liền với đất",
                "description": "Đơn theo mẫu số 11/ĐK quy định tại Nghị định 101/2024/NĐ-CP",
                "is_required": True,
                "copies": 1,
                "document_form": "original",
                "form_code": "Mẫu 11/ĐK",
                "note": "Ký ghi rõ họ tên người nộp hồ sơ",
                "order_index": 3,
            },
            {
                "id": "d5d9c792-req-004",
                "name": "Tờ khai thuế thu nhập cá nhân và lệ phí trước bạ",
                "description": "Hồ sơ khai nghĩa vụ tài chính gồm tờ khai thuế TNCN và lệ phí trước bạ",
                "is_required": True,
                "copies": 2,
                "document_form": "original",
                "form_code": "Mẫu 03/BĐS-TNCN",
                "note": "Nếu thuộc diện miễn thuế thì nộp giấy tờ chứng minh quan hệ nhân thân",
                "order_index": 4,
            },
        ],
        "conditions": [
            {
                "id": "d5d9c792-cond-001",
                "condition_code": "COND-01",
                "title": "Đất có Giấy chứng nhận và không có tranh chấp",
                "description": "Thửa đất có Giấy chứng nhận quyền sử dụng đất, không có tranh chấp, quyền sử dụng đất không bị kê biên để bảo đảm thi hành án.",
                "is_mandatory": True,
                "order_index": 1,
            },
            {
                "id": "d5d9c792-cond-002",
                "condition_code": "COND-02",
                "title": "Trong thời hạn sử dụng đất",
                "description": "Thửa đất chuyển nhượng đang còn trong thời hạn sử dụng đất theo quy định.",
                "is_mandatory": True,
                "order_index": 2,
            },
        ],
        "fees": [
            {
                "id": "d5d9c792-fee-001",
                "fee_type": "appraisal_fee",
                "name": "Phí thẩm định hồ sơ cấp quyền sử dụng đất",
                "amount": None,
                "unit": "hồ sơ",
                "is_exemptible": False,
                "note": "Theo bảng giá của HĐND tỉnh/thành phố trực thuộc Trung ương nơi có đất",
            },
            {
                "id": "d5d9c792-fee-002",
                "fee_type": "administrative_fee",
                "name": "Lệ phí cấp Giấy chứng nhận",
                "amount": 50000,
                "unit": "lần",
                "is_exemptible": False,
                "note": "Mức thu cho trường hợp chứng nhận biến động",
            },
        ],
    },
    {
        "id": "f4c3d250-7c2b-5e62-9e3d-4076269b9b52",
        "code": "HKD-002",
        "name": "Đăng ký thay đổi nội dung đăng ký hộ kinh doanh",
        "domain": "business",
        "description": "Thủ tục điều chỉnh thông tin ngành nghề, địa chỉ, vốn hoặc người đại diện hộ kinh doanh cá thể.",
        "processing_days": 3,
        "submission_channels": ["direct", "online"],
        "receiving_agency_level": "district",
        "fee": 50000,
        "fee_note": "Miễn phí khi thực hiện dịch vụ công trực tuyến",
        "effective_from": "2021-01-04",
        "updated_at": "2026-10-02T02:00:00Z",
        "status": "active",
        "legal_bases": [
            {
                "doc_number": "01/2021/NĐ-CP",
                "title": "Nghị định về đăng ký doanh nghiệp",
                "article_ref": "Điều 90",
                "source_url": "https://thuvienphapluat.vn/van-ban/Doanh-nghiep/Nghi-dinh-01-2021-ND-CP-dang-ky-doanh-nghiep-461408.aspx",
                "order_index": 1,
            }
        ],
        "requirements": [
            {
                "id": "f4c3d250-req-001",
                "name": "Thông báo thay đổi nội dung đăng ký hộ kinh doanh",
                "description": "Bản chính Thông báo thay đổi nội dung đăng ký hộ kinh doanh",
                "is_required": True,
                "copies": 1,
                "document_form": "original",
                "form_code": "Phụ lục III-2",
                "note": "Người đại diện ký tên",
                "order_index": 1,
            },
            {
                "id": "f4c3d250-req-002",
                "name": "Bản gốc Giấy chứng nhận đăng ký hộ kinh doanh cũ",
                "description": "Nộp lại giấy chứng nhận đăng ký hộ kinh doanh đang lưu giữ",
                "is_required": True,
                "copies": 1,
                "document_form": "original",
                "form_code": None,
                "note": None,
                "order_index": 2,
            },
        ],
        "conditions": [
            {
                "id": "f4c3d250-cond-001",
                "condition_code": "COND-01",
                "title": "Hồ sơ hợp lệ và nộp đúng hạn",
                "description": "Hộ kinh doanh phải gửi Thông báo thay đổi trong thời hạn 10 ngày kể từ ngày có thay đổi.",
                "is_mandatory": True,
                "order_index": 1,
            }
        ],
        "fees": [
            {
                "id": "f4c3d250-fee-001",
                "fee_type": "administrative_fee",
                "name": "Lệ phí thay đổi thông tin hộ kinh doanh",
                "amount": 50000,
                "unit": "lần",
                "is_exemptible": True,
                "note": "Miễn lệ phí khi nộp trực tuyến",
            }
        ],
    },
    {
        "id": "b6c5d470-9e4d-7a84-1a5f-6298481d1d74",
        "code": "DD-002",
        "name": "Cấp đổi Giấy chứng nhận quyền sử dụng đất, quyền sở hữu tài sản gắn liền với đất",
        "domain": "land",
        "description": "Thủ tục cấp đổi sổ đỏ/sổ hồng khi bị rách, ố, nhòe, đổi sang mẫu phôi mới theo Luật Đất đai 2024 hoặc đo đạc lại diện tích.",
        "processing_days": 7,
        "submission_channels": ["direct", "postal", "online"],
        "receiving_agency_level": "district",
        "fee": 50000,
        "fee_note": "Lệ phí cấp đổi giấy chứng nhận theo quy định HĐND cấp tỉnh",
        "effective_from": "2024-08-01",
        "updated_at": "2026-10-02T02:00:00Z",
        "status": "active",
        "legal_bases": [
            {
                "doc_number": "31/2024/QH15",
                "title": "Luật Đất đai 2024",
                "article_ref": "Điều 152",
                "source_url": "https://thuvienphapluat.vn/van-ban/Bat-dong-san/Luat-Dat-dai-2024-31-2024-QH15-564560.aspx",
                "order_index": 1,
            }
        ],
        "requirements": [
            {
                "id": "b6c5d470-req-001",
                "name": "Đơn đề nghị cấp đổi Giấy chứng nhận",
                "description": "Đơn theo mẫu số 10/ĐK quy định tại Nghị định 101/2024/NĐ-CP",
                "is_required": True,
                "copies": 1,
                "document_form": "original",
                "form_code": "Mẫu 10/ĐK",
                "note": None,
                "order_index": 1,
            },
            {
                "id": "b6c5d470-req-002",
                "name": "Bản gốc Giấy chứng nhận đã cấp",
                "description": "Nộp lại bản gốc sổ đỏ / sổ hồng cũ để thu hồi đổi sang sổ mới",
                "is_required": True,
                "copies": 1,
                "document_form": "original",
                "form_code": None,
                "note": None,
                "order_index": 2,
            },
        ],
        "conditions": [
            {
                "id": "b6c5d470-cond-001",
                "condition_code": "COND-01",
                "title": "Có giấy chứng nhận cũ hợp pháp",
                "description": "Người sử dụng đất có nhu cầu đổi sang mẫu mới hoặc sổ cũ bị rách nát, hư hỏng.",
                "is_mandatory": True,
                "order_index": 1,
            }
        ],
        "fees": [
            {
                "id": "b6c5d470-fee-001",
                "fee_type": "administrative_fee",
                "name": "Lệ phí cấp đổi Giấy chứng nhận",
                "amount": 50000,
                "unit": "lần",
                "is_exemptible": False,
                "note": None,
            }
        ],
    },
    {
        "id": "a5b4c360-8d3c-6f73-0f4e-5187370c0c63",
        "code": "HKD-003",
        "name": "Chấm dứt hoạt động hộ kinh doanh",
        "domain": "business",
        "description": "Thủ tục thông báo giải thể, chấm dứt hoạt động hộ kinh doanh cá thể khi không còn nhu cầu kinh doanh.",
        "processing_days": 3,
        "submission_channels": ["direct", "online"],
        "receiving_agency_level": "district",
        "fee": 0,
        "fee_note": "Miễn lệ phí",
        "effective_from": "2021-01-04",
        "updated_at": "2026-10-02T02:00:00Z",
        "status": "active",
        "legal_bases": [
            {
                "doc_number": "01/2021/NĐ-CP",
                "title": "Nghị định về đăng ký doanh nghiệp",
                "article_ref": "Điều 92",
                "source_url": "https://thuvienphapluat.vn/van-ban/Doanh-nghiep/Nghi-dinh-01-2021-ND-CP-dang-ky-doanh-nghiep-461408.aspx",
                "order_index": 1,
            }
        ],
        "requirements": [
            {
                "id": "a5b4c360-req-001",
                "name": "Thông báo chấm dứt hoạt động hộ kinh doanh",
                "description": "Bản chính Thông báo về việc chấm dứt hoạt động hộ kinh doanh",
                "is_required": True,
                "copies": 1,
                "document_form": "original",
                "form_code": "Phụ lục III-5",
                "note": "Chủ hộ kinh doanh ký tên",
                "order_index": 1,
            },
            {
                "id": "a5b4c360-req-002",
                "name": "Bản gốc Giấy chứng nhận đăng ký hộ kinh doanh",
                "description": "Nộp lại bản gốc giấy chứng nhận để cơ quan ĐKKD thu hồi",
                "is_required": True,
                "copies": 1,
                "document_form": "original",
                "form_code": None,
                "note": None,
                "order_index": 2,
            },
            {
                "id": "a5b4c360-req-003",
                "name": "Xác nhận hoàn thành nghĩa vụ thuế",
                "description": "Thông báo hoàn thành nghĩa vụ thuế của Chi cục Thuế quản lý",
                "is_required": True,
                "copies": 1,
                "document_form": "copy",
                "form_code": None,
                "note": "Hộ kinh doanh phải đóng mã số thuế trước khi chấm dứt",
                "order_index": 3,
            },
        ],
        "conditions": [
            {
                "id": "a5b4c360-cond-001",
                "condition_code": "COND-01",
                "title": "Đã thanh toán đầy đủ các khoản nợ",
                "description": "Hộ kinh doanh phải thanh toán đầy đủ các khoản nợ gồm nợ thuế và nợ các tổ chức, cá nhân liên quan.",
                "is_mandatory": True,
                "order_index": 1,
            }
        ],
        "fees": [],
    },
]


class ProcedureRepository:
    @staticmethod
    def check_database_health() -> dict:
        """Kiểm tra kết nối PostgreSQL qua PROCEDURE_DATABASE_URL bằng SELECT 1."""
        engine = get_engine()
        url_display = database_url().split("@")[-1] if "@" in database_url() else "hidden"
        if not engine:
            return {
                "status": "error",
                "connected": False,
                "type": "postgresql",
                "connection": url_display,
                "message": "Chưa khởi tạo được engine kết nối PostgreSQL.",
            }
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1;"))
            return {
                "status": "connected",
                "connected": True,
                "type": "postgresql",
                "connection": url_display,
                "message": "Kết nối PostgreSQL qua PROCEDURE_DATABASE_URL hoạt động tốt.",
            }
        except Exception as e:
            logger.warning(f"Lỗi kết nối PostgreSQL qua PROCEDURE_DATABASE_URL: {e}")
            return {
                "status": "circuit_breaker" if "ECIRCUITBREAKER" in str(e) else "auth_failed",
                "connected": False,
                "type": "postgresql",
                "connection": url_display,
                "message": f"Không thể kết nối PostgreSQL: {str(e)}",
            }

    @staticmethod
    def get_procedures(domain: str | None = None, offset: int = 0, limit: int = 20, search: str | None = None) -> tuple[list[dict], int]:
        """Lấy danh sách thủ tục có phân trang và lọc."""
        domain = _normalize_domain(domain)
        where_clauses = ["status = 'active'"]
        params: dict = {"limit": limit, "offset": offset}
        if domain:
            where_clauses.append("domain = :domain")
            params["domain"] = domain
        if search:
            where_clauses.append("lower(name) LIKE :search")
            params["search"] = f"%{search.strip().lower()}%"
        where_sql = " AND ".join(where_clauses)
        with db_connection() as conn:
            total = conn.execute(text(f"SELECT count(*) FROM public.procedures WHERE {where_sql}"), params).scalar() or 0
            items = _rows(conn, f"SELECT {SUMMARY_COLUMNS} FROM public.procedures WHERE {where_sql} "
                                "ORDER BY code LIMIT :limit OFFSET :offset", params)
        return items, total

    @staticmethod
    def search_procedures(query_text: str, limit: int = 20) -> list[dict]:
        """Tìm theo tên/mã/mô tả, có dấu hoặc không dấu."""
        words = remove_accents(query_text.casefold()).split()
        with db_connection() as conn:
            rows = _rows(conn, f"SELECT {SUMMARY_COLUMNS} FROM public.procedures WHERE status = 'active' ORDER BY code")
        matched = [
            r for r in rows
            if all(w in remove_accents(" ".join(filter(None, [r["name"], r["code"], r.get("description")])).casefold()) for w in words)
        ]
        return matched[:limit]

    @staticmethod
    def get_procedure_by_id_or_code(procedure_id_or_code: str) -> dict | None:
        """Lấy chi tiết thủ tục theo ID hoặc mã nghiệp vụ (HKD-001, DD-001...) có cache & fallback."""
        target = procedure_id_or_code.strip()
        cache_key = f"proc_detail:{target.upper()}"
        now = time.time()
        if cache_key in _proc_cache:
            c_time, c_data = _proc_cache[cache_key]
            if now - c_time < CACHE_TTL:
                return copy.deepcopy(c_data)

        proc = None
        if not is_postgres_circuit_open():
            try:
                from app.repositories.procedure_record_repository import safe_uuid_str
                target_uuid = safe_uuid_str(target)
                where_id = "id = :target_uuid" if target_uuid else "CAST(id AS TEXT) = :target"
                p_dict = {"target": target, "code": target.upper()}
                if target_uuid:
                    p_dict["target_uuid"] = target_uuid
                with db_connection() as conn:
                    found = _rows(conn, f"SELECT * FROM public.procedures WHERE {where_id} OR upper(code) = :code LIMIT 1", p_dict)
                    if found:
                        proc = found[0]
                        p = {"p_id": proc["id"]}
                        proc["legal_bases"] = _rows(conn, "SELECT * FROM public.procedure_legal_bases WHERE procedure_id = :p_id ORDER BY order_index", p)
                        proc["requirements"] = _rows(conn, "SELECT * FROM public.procedure_requirements WHERE procedure_id = :p_id ORDER BY order_index", p)
                        proc["conditions"] = _rows(conn, "SELECT * FROM public.procedure_conditions WHERE procedure_id = :p_id ORDER BY order_index", p)
                        proc["fees"] = _rows(conn, "SELECT * FROM public.procedure_fees WHERE procedure_id = :p_id", p)
            except Exception as e:
                logger.warning(f"Lỗi SQL get_procedure_by_id_or_code: {e}. Thử fallback...")
                mark_postgres_failed(60.0)
                proc = None

        if proc is None:
            for p in MOCK_PROCEDURES:
                if str(p.get("id")) == target or p.get("code", "").upper() == target.upper():
                    proc = copy.deepcopy(p)
                    break

        if proc:
            _proc_cache[cache_key] = (now, proc)
            return copy.deepcopy(proc)
        return None

    @staticmethod
    def get_domains() -> list[dict]:
        """Danh sách lĩnh vực kèm số thủ tục thực tế (có in-memory cache & fallback)."""
        cache_key = "proc_domains"
        now = time.time()
        if cache_key in _proc_cache:
            c_time, c_data = _proc_cache[cache_key]
            if now - c_time < CACHE_TTL:
                return copy.deepcopy(c_data)

        domains_result = None
        if not is_postgres_circuit_open():
            try:
                with db_connection() as conn:
                    domains = _rows(conn, "SELECT code, name, description, icon, order_index FROM public.procedure_domains "
                                          "WHERE is_active IS TRUE ORDER BY order_index")
                    counts = {r["domain"]: r["count"] for r in _rows(conn, "SELECT domain, count(*) AS count FROM public.procedures GROUP BY domain")}
                domains_result = [
                    {
                        "code": d["code"],
                        "name": d["name"],
                        "description": d.get("description") or "",
                        "icon": d.get("icon") or ("landscape" if d["code"] == "land" else "storefront"),
                        "count": counts.get(d["code"], 0),
                    }
                    for d in domains
                ]
            except Exception as e:
                logger.warning(f"Lỗi SQL get_domains: {e}. Thử fallback...")
                mark_postgres_failed(60.0)
                domains_result = None

        if domains_result is None:
            domains_result = [
                {"code": "land", "name": "Đất đai & Bất động sản", "description": "Thủ tục hành chính đất đai", "icon": "landscape", "count": 2},
                {"code": "business", "name": "Doanh nghiệp & Hộ kinh doanh", "description": "Đăng ký thành lập, thay đổi HKD", "icon": "storefront", "count": 2},
            ]

        _proc_cache[cache_key] = (now, domains_result)
        return copy.deepcopy(domains_result)

    @staticmethod
    def get_procedures_by_domain(domain: str, limit: int = 100, include_details: bool = True) -> list[dict]:
        """Danh sách thủ tục theo lĩnh vực ('all' = mọi lĩnh vực), có In-Memory cache và PostgREST fallback."""
        clean_domain = _normalize_domain(domain) or "all"
        cache_key = f"proc_domain:{clean_domain}:{limit}:{include_details}"
        now = time.time()
        if cache_key in _proc_cache:
            c_time, c_data = _proc_cache[cache_key]
            if now - c_time < CACHE_TTL:
                return copy.deepcopy(c_data)

        items = None
        if not is_postgres_circuit_open():
            try:
                params: dict = {"limit": limit}
                where_sql = ""
                if clean_domain != "all":
                    where_sql = "WHERE domain = :domain"
                    params["domain"] = clean_domain
                with db_connection() as conn:
                    items = _rows(conn, f"SELECT * FROM public.procedures {where_sql} ORDER BY code LIMIT :limit", params)
                    for proc in items:
                        proc["fee_formatted"] = _fee_formatted(proc.get("fee"))
                        if not include_details:
                            continue
                        p = {"p_id": proc["id"]}
                        proc["requirements"] = _rows(conn, "SELECT id, name, is_required, copies, document_form, form_code, note "
                                                           "FROM public.procedure_requirements WHERE procedure_id = :p_id ORDER BY order_index", p)
                        proc["conditions"] = _rows(conn, "SELECT id, condition_code, title, description, is_mandatory "
                                                         "FROM public.procedure_conditions WHERE procedure_id = :p_id ORDER BY order_index", p)
                        proc["legal_bases"] = _rows(conn, "SELECT id, doc_number, title, article_ref, source_url "
                                                          "FROM public.procedure_legal_bases WHERE procedure_id = :p_id ORDER BY order_index", p)
                        proc["requirements_count"] = len(proc["requirements"])
                        proc["conditions_count"] = len(proc["conditions"])
                        proc["legal_bases_count"] = len(proc["legal_bases"])
            except Exception as e:
                logger.warning(f"Lỗi SQL get_procedures_by_domain: {e}. Thử fallback PostgREST...")
                mark_postgres_failed(60.0)
                items = None

        if items is None:
            try:
                supabase_url, _ = get_supabase_config()
                if supabase_url:
                    url = f"{supabase_url}/rest/v1/procedures?select=*&order=code.asc&limit={limit}"
                    if clean_domain != "all":
                        url += f"&domain=eq.{clean_domain}"
                    res = _proc_session.get(url, headers=_get_supabase_headers(), timeout=4)
                    if res.status_code == 200:
                        items = res.json()
                        for proc in items:
                            proc["fee_formatted"] = _fee_formatted(proc.get("fee"))
                            if "id" in proc:
                                proc["id"] = str(proc["id"])
            except Exception as e:
                logger.warning(f"Lỗi fallback PostgREST get_procedures_by_domain: {e}")
                items = None

        if items is None:
            matched = [copy.deepcopy(p) for p in MOCK_PROCEDURES if clean_domain in ("all", p.get("domain"))][:limit]
            for proc in matched:
                proc["fee_formatted"] = _fee_formatted(proc.get("fee"))
            items = matched

        _proc_cache[cache_key] = (now, items)
        return copy.deepcopy(items)

    @staticmethod
    def get_procedure_names_by_domain(domain: str) -> list[dict]:
        """Tên và mã thủ tục gọn nhẹ theo lĩnh vực (cho dropdown)."""
        clean_domain = _normalize_domain(domain) or "all"
        params: dict = {}
        where_sql = ""
        if clean_domain != "all":
            where_sql = "WHERE domain = :domain"
            params["domain"] = clean_domain
        with db_connection() as conn:
            items = _rows(conn, f"SELECT id, code, name, domain, fee, processing_days FROM public.procedures {where_sql} ORDER BY code", params)
        for item in items:
            item["fee_formatted"] = _fee_formatted(item.get("fee"))
        return items

    @staticmethod
    def create_or_ensure_procedure(
        code: str,
        name: str,
        domain: str,
        description: str = "",
        processing_days: int = 3,
        fee: int = 0,
        submission_channels: list[str] | None = None,
    ) -> dict:
        """Tạo thủ tục hành chính mới nếu chưa có, hoặc trả về thủ tục hiện tại."""
        import uuid
        clean_code = code.strip().upper()
        norm_domain = "land" if domain in ("land", "dat_dai") else "business"
        channels = submission_channels or ["online", "in_person"]

        with db_connection(write=True) as conn:
            existing = _rows(conn, "SELECT * FROM public.procedures WHERE UPPER(code) = :code LIMIT 1", {"code": clean_code})
            if existing:
                return existing[0]

            new_id = str(uuid.uuid4())
            conn.execute(
                text("""
                    INSERT INTO public.procedures (
                        id, code, name, domain, description, processing_days,
                        submission_channels, receiving_agency_level, fee, status,
                        created_at, updated_at
                    ) VALUES (
                        :id, :code, :name, :domain, :description, :processing_days,
                        :channels, 'district', :fee, 'active',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                """),
                {
                    "id": new_id,
                    "code": clean_code,
                    "name": name.strip(),
                    "domain": norm_domain,
                    "description": description or f"Thủ tục {name.strip()}",
                    "processing_days": processing_days,
                    "channels": channels,
                    "fee": fee,
                }
            )
            created = _rows(conn, "SELECT * FROM public.procedures WHERE id = :id", {"id": new_id})
            _proc_cache.clear()
            return created[0] if created else {}

