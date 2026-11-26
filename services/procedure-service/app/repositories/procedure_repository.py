import json
import logging
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DataError, SQLAlchemyError

from app.database import get_engine, is_postgres_circuit_open

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
def db_connection(write: bool = False, fresh: bool = False):
    if is_postgres_circuit_open():
        raise ProcedureDbUnavailable("Kết nối cơ sở dữ liệu thủ tục đang tạm ngừng.")
    try:
        engine = get_engine()
    except SQLAlchemyError as exc:  # URL sai định dạng
        raise ProcedureDbUnavailable(type(exc).__name__) from exc
    if engine is None:
        raise ProcedureDbUnavailable("PROCEDURE_DATABASE_URL chưa cấu hình")
    is_pg = getattr(getattr(engine, "dialect", None), "name", "") == "postgresql"
    try:
        cm = (
            engine.begin()
            if write
            else (
                engine.connect().execution_options(isolation_level="AUTOCOMMIT")
                if is_pg
                else engine.connect()
            )
        )
        with cm as conn:
            try:
                yield conn
            except DataError:
                raise
            except SQLAlchemyError:
                if is_pg:
                    try:
                        conn.invalidate()
                    except Exception:
                        pass
                raise
            finally:
                if fresh and is_pg:
                    try:
                        conn.invalidate()
                    except Exception:
                        pass
    except DataError as exc:
        raise ProcedureInvalidData(type(exc).__name__) from exc
    except SQLAlchemyError as exc:
        logger.warning("Lỗi truy vấn cơ sở dữ liệu thủ tục (%s).", type(exc).__name__)
        raise ProcedureDbUnavailable(type(exc).__name__) from exc


def _normalize_domain(domain: str | None) -> str | None:
    """Chuẩn hóa mã lĩnh vực từ form động và API về domain nội bộ."""
    if not domain:
        return domain
    mapping = {
        "dat_dai": "land",
        "ho_kinh_doanh": "business",
        "doanh_nghiep": "business",
    }
    return mapping.get(domain.strip().lower(), domain.strip().lower())


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
        if item.get("form_template_id") is not None:
            item["form_template_id"] = str(item["form_template_id"])
    return items


SUMMARY_COLUMNS = "id, code, name, domain, description, fee, processing_days, form_template_id"

_DETAIL_COLUMNS = {
    "requirements": "id, procedure_id, name, is_required, copies, document_form, form_code, note, order_index",
    "conditions": "id, procedure_id, condition_code, title, description, is_mandatory, order_index",
    "legal_bases": "id, procedure_id, doc_number, title, article_ref, source_url, order_index",
    "fees": "id, procedure_id, name, amount",
}
_DOMAIN_COLUMNS = {
    "requirements": "id, name, is_required, copies, document_form, form_code, note",
    "conditions": "id, condition_code, title, description, is_mandatory",
    "legal_bases": "id, doc_number, title, article_ref, source_url",
}


def _detail_subquery(conn, field: str, columns: str | None, ordered: bool = True) -> str:
    """Aggregate each child relation independently, using the native FK type."""
    is_pg = conn.dialect.name == "postgresql"
    selected = columns or ("*" if is_pg else _DETAIL_COLUMNS[field])
    order = " ORDER BY order_index" if ordered else ""
    rows = f"SELECT {selected} FROM public.procedure_{field} WHERE procedure_id = p.id{order}"
    if is_pg:
        return f"(SELECT COALESCE(json_agg(row_to_json(child)), '[]'::json) FROM ({rows}) child)"
    keys = (columns or _DETAIL_COLUMNS[field]).split(", ")
    pairs = ", ".join(f"'{key}', child.{key}" for key in keys)
    return f"(SELECT COALESCE(json_group_array(json_object({pairs})), '[]') FROM ({rows}) child)"


def _unpack_details(item: dict, fields: tuple[str, ...], counts: bool = False, postgres: bool = False) -> None:
    for field in fields:
        value = item[field]
        item[field] = json.loads(value) if isinstance(value, str) else value
        if postgres and not counts:
            for detail in item[field]:
                if detail.get("procedure_id") is not None:
                    detail["procedure_id"] = UUID(detail["procedure_id"])
                if detail.get("created_at") is not None:
                    detail["created_at"] = datetime.fromisoformat(detail["created_at"])
        if counts:
            item[f"{field}_count"] = len(item[field])


class ProcedureRepository:
    @staticmethod
    def check_database_health() -> dict:
        """Kiểm tra cơ sở dữ liệu thủ tục bằng SELECT 1."""
        try:
            with db_connection() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            logger.exception("Kiểm tra kết nối cơ sở dữ liệu thủ tục thất bại.")
            return {
                "status": "circuit_breaker" if is_postgres_circuit_open() else "error",
                "connected": False,
                "type": "postgresql",
                "connection": "hidden",
                "message": "Không thể kết nối cơ sở dữ liệu thủ tục.",
            }
        return {
            "status": "connected",
            "connected": True,
            "type": "postgresql",
            "connection": "hidden",
            "message": "Kết nối PostgreSQL qua PROCEDURE_DATABASE_URL hoạt động tốt.",
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
            rows = _rows(conn, f"""
                SELECT page.*, totals.total AS _total
                FROM (SELECT count(*) AS total FROM public.procedures WHERE {where_sql}) totals
                LEFT JOIN (SELECT {SUMMARY_COLUMNS} FROM public.procedures WHERE {where_sql}
                           ORDER BY code LIMIT :limit OFFSET :offset) page ON 1=1
                ORDER BY page.code
            """, params)
        total = rows[0].get("_total", 0) if rows else 0
        for row in rows:
            row.pop("_total", None)
        items = [row for row in rows if row.get("id") is not None]

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
        """Lấy chi tiết thủ tục theo ID hoặc mã nghiệp vụ."""
        from app.repositories.procedure_record_repository import safe_uuid_str

        target = procedure_id_or_code.strip()
        target_uuid = safe_uuid_str(target)
        id_sql = "id = :target OR " if target_uuid else ""
        with db_connection() as conn:
            fields = ("legal_bases", "requirements", "conditions", "fees")
            details = ", ".join(
                f"{_detail_subquery(conn, field, None, field != 'fees')} AS {field}"
                for field in fields
            )
            rows = _rows(
                conn,
                f"SELECT p.*, {details} FROM (SELECT * FROM public.procedures "
                f"WHERE {id_sql}upper(code) = :code LIMIT 1) p",
                {"target": target_uuid or target, "code": target.upper()},
            )
            if not rows:
                return None
            proc = rows[0]
            _unpack_details(proc, fields, postgres=conn.dialect.name == "postgresql")
        return proc

    @staticmethod
    def get_domains() -> list[dict]:
        """Lấy lĩnh vực đang hoạt động kèm số thủ tục."""
        with db_connection() as conn:
            domains = _rows(conn, """
                SELECT d.code, d.name, d.description, d.icon, COALESCE(p.count, 0) AS count
                FROM public.procedure_domains d
                LEFT JOIN (
                    SELECT domain, count(*) AS count FROM public.procedures GROUP BY domain
                ) p ON p.domain = d.code
                WHERE d.is_active IS TRUE ORDER BY d.order_index
            """)
        for domain in domains:
            domain["description"] = domain["description"] or ""
            domain["icon"] = domain["icon"] or ("landscape" if domain["code"] == "land" else "storefront")
        return domains

    @staticmethod
    def get_procedures_by_domain(domain: str, limit: int = 100, include_details: bool = True) -> list[dict]:
        """Lấy danh sách thủ tục theo lĩnh vực, có thể kèm dữ liệu chi tiết."""
        clean_domain = _normalize_domain(domain) or "all"
        params = {"limit": limit}
        where_sql = ""
        if clean_domain != "all":
            where_sql = "WHERE domain = :domain"
            params["domain"] = clean_domain

        with db_connection() as conn:
            fields = tuple(_DOMAIN_COLUMNS)
            details = ""
            if include_details:
                details = ", " + ", ".join(
                    f"{_detail_subquery(conn, field, _DOMAIN_COLUMNS[field])} AS {field}"
                    for field in fields
                )
            items = _rows(conn, f"SELECT p.*{details} FROM (SELECT * FROM public.procedures {where_sql} "
                          "ORDER BY code LIMIT :limit) p ORDER BY p.code", params)
            for proc in items:
                proc["fee_formatted"] = _fee_formatted(proc.get("fee"))
                if include_details:
                    _unpack_details(proc, fields, counts=True)

        return items

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
        """Maak een procedure aan wanneer een dynamic template een nieuwe code gebruikt."""
        import uuid

        clean_code = code.strip().upper()
        norm_domain = _normalize_domain(domain) or "business"
        channels = submission_channels or ["online", "in_person"]

        with db_connection(write=True) as conn:
            existing = _rows(
                conn,
                "SELECT * FROM public.procedures WHERE UPPER(code) = :code LIMIT 1",
                {"code": clean_code},
            )
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
                },
            )
            created = _rows(conn, "SELECT * FROM public.procedures WHERE id = :id", {"id": new_id})
        for namespace in ("procedures", "procedure_domains", "classifications"):
            catalog_cache.invalidate(namespace)
        return created[0] if created else {}


# Catalogue entries are public data; cache copies and invalidation are centralized.
from functools import wraps
from app.core.catalog_cache import catalog_cache


def _cached_procedures(method):
    @wraps(method)
    def wrapper(*args, **kwargs):
        key = (method.__name__, args, tuple(sorted(kwargs.items())))
        return catalog_cache.get_or_load("procedures", key, 120, lambda: method(*args, **kwargs))
    return staticmethod(wrapper)


for _method_name in ("get_procedures", "get_procedure_by_id_or_code", "get_procedures_by_domain"):
    setattr(ProcedureRepository, _method_name, _cached_procedures(getattr(ProcedureRepository, _method_name)))
