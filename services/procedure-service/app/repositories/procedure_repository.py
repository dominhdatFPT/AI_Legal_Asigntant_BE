import logging
import unicodedata
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.exc import DataError, SQLAlchemyError

from app.database import get_engine

logger = logging.getLogger(__name__)


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
        logger.warning("Lỗi truy vấn database thủ tục: %s", type(exc).__name__)
        raise ProcedureDbUnavailable(type(exc).__name__) from exc


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


class ProcedureRepository:
    @staticmethod
    def get_procedures(domain: str | None = None, offset: int = 0, limit: int = 20, search: str | None = None) -> tuple[list[dict], int]:
        """Lấy danh sách thủ tục có phân trang và lọc."""
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
        # shortcut: lọc trong Python vì bảng thủ tục chỉ vài chục dòng; >1.000 dòng thì chuyển sang unaccent/pg_trgm.
        words = remove_accents(query_text.casefold()).split()
        with db_connection() as conn:
            rows = _rows(conn, f"SELECT {SUMMARY_COLUMNS} FROM public.procedures WHERE status = 'active' ORDER BY code")
        # Khớp khi mọi từ của câu hỏi đều có trong tên/mã/mô tả (bỏ dấu).
        matched = [
            r for r in rows
            if all(w in remove_accents(" ".join(filter(None, [r["name"], r["code"], r.get("description")])).casefold()) for w in words)
        ]
        return matched[:limit]

    @staticmethod
    def get_procedure_by_id_or_code(procedure_id_or_code: str) -> dict | None:
        """Lấy chi tiết thủ tục theo ID hoặc mã nghiệp vụ (HKD-001, DD-001...)."""
        target = procedure_id_or_code.strip()
        with db_connection() as conn:
            found = _rows(conn, "SELECT * FROM public.procedures WHERE CAST(id AS TEXT) = :target "
                                "OR upper(code) = :code LIMIT 1", {"target": target, "code": target.upper()})
            if not found:
                return None
            proc = found[0]
            p = {"p_id": proc["id"]}
            proc["legal_bases"] = _rows(conn, "SELECT * FROM public.procedure_legal_bases WHERE procedure_id = :p_id ORDER BY order_index", p)
            proc["requirements"] = _rows(conn, "SELECT * FROM public.procedure_requirements WHERE procedure_id = :p_id ORDER BY order_index", p)
            proc["conditions"] = _rows(conn, "SELECT * FROM public.procedure_conditions WHERE procedure_id = :p_id ORDER BY order_index", p)
            proc["fees"] = _rows(conn, "SELECT * FROM public.procedure_fees WHERE procedure_id = :p_id", p)
        return proc

    @staticmethod
    def get_domains() -> list[dict]:
        """Danh sách lĩnh vực kèm số thủ tục thực tế."""
        with db_connection() as conn:
            domains = _rows(conn, "SELECT code, name, description, icon, order_index FROM public.procedure_domains "
                                  "WHERE is_active = true ORDER BY order_index")
            counts = {r["domain"]: r["count"] for r in _rows(conn, "SELECT domain, count(*) AS count FROM public.procedures GROUP BY domain")}
        return [
            {
                "code": d["code"],
                "name": d["name"],
                "description": d.get("description") or "",
                "icon": d.get("icon") or ("landscape" if d["code"] == "land" else "storefront"),
                "count": counts.get(d["code"], 0),
            }
            for d in domains
        ]

    @staticmethod
    def get_procedures_by_domain(domain: str, limit: int = 100, include_details: bool = True) -> list[dict]:
        """Danh sách thủ tục theo lĩnh vực ('all' = mọi lĩnh vực)."""
        clean_domain = domain.strip().lower()
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
        return items

    @staticmethod
    def get_procedure_names_by_domain(domain: str) -> list[dict]:
        """Tên và mã thủ tục gọn nhẹ theo lĩnh vực (cho dropdown)."""
        clean_domain = domain.strip().lower()
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
