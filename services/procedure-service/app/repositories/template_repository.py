import logging
import os
import unicodedata
from typing import Any, Dict, List, Optional
import requests
from requests.adapters import HTTPAdapter
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from app.database import get_engine, get_supabase_config, is_postgres_circuit_open, mark_postgres_failed
from app.repositories.procedure_repository import ProcedureDbUnavailable, db_connection, remove_accents

logger = logging.getLogger(__name__)

# Connection pool session
_session = requests.Session()
_adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=1)
_session.mount("http://", _adapter)
_session.mount("https://", _adapter)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BIZ_DIR = os.path.join(BASE_DIR, "Template các thủ tục kinh doanh")
LAND_DIR = os.path.join(BASE_DIR, "Template các thủ tục đất đai")

DOMAIN_LABELS = {
    "land": "Đất đai & Bất động sản",
    "business": "Doanh nghiệp & Hộ kinh doanh",
}


def _supabase_headers() -> dict:
    _, anon_key = get_supabase_config()
    return {
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _domain_label(domain: str) -> str:
    return DOMAIN_LABELS.get(domain, "Khác")


def _is_viewable(format_str: str) -> bool:
    # PDF và ảnh có thể xem trước trực tiếp trên trình duyệt
    return format_str.upper() in ("PDF", "PNG", "JPG", "JPEG")


def _local_disk_fallback_list(
    domain: Optional[str] = None,
    procedure_code: Optional[str] = None,
    q: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fallback quét trực tiếp từ đĩa khi không thể kết nối tới Database."""
    folders = []
    if domain in (None, "", "all", "land"):
        folders.append(("land", LAND_DIR))
    if domain in (None, "", "all", "business"):
        folders.append(("business", BIZ_DIR))

    results = []
    for d_name, folder_path in folders:
        if not os.path.exists(folder_path):
            continue
        for fname in sorted(os.listdir(folder_path)):
            if fname.startswith("~$") or fname.startswith("."):
                continue
            ext = os.path.splitext(fname)[1].lower().replace(".", "").upper()
            full_path = os.path.join(folder_path, fname)
            fsize = os.path.getsize(full_path)
            
            # Extract basic code hint if any
            p_code = procedure_code or ("DD-001" if d_name == "land" else "HKD-001")
            
            item = {
                "id": f"local-{d_name}-{fname}",
                "procedure_code": p_code,
                "procedure_name": os.path.splitext(fname)[0],
                "name": os.path.splitext(fname)[0],
                "file_name": fname,
                "domain": d_name,
                "domain_label": _domain_label(d_name),
                "format": ext,
                "size": fsize,
                "viewable": _is_viewable(ext),
                "order_index": 99,
            }
            results.append(item)

    if q:
        words = remove_accents(q.strip().casefold()).split()
        results = [
            r for r in results
            if all(w in remove_accents(f"{r['name']} {r['procedure_name']} {r['procedure_code']}").casefold() for w in words)
        ]
    return results


_METADATA_BY_ID: Dict[str, Dict[str, Any]] = {}
_FILE_CACHE: Dict[str, Dict[str, Any]] = {}


class TemplateRepository:
    """Repository chuyên trách quản lý và phục vụ biểu mẫu (Kho mẫu đơn tham khảo)."""

    @staticmethod
    def list_templates(
        domain: Optional[str] = None,
        procedure_code: Optional[str] = None,
        q: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Lấy danh sách biểu mẫu có lọc theo lĩnh vực, mã thủ tục và từ khóa."""
        items = TemplateRepository._do_list_templates(domain=domain, procedure_code=procedure_code, q=q)
        for it in items:
            _METADATA_BY_ID[it["id"]] = it
        return items

    @staticmethod
    def _do_list_templates(
        domain: Optional[str] = None,
        procedure_code: Optional[str] = None,
        q: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        clean_domain = domain.strip().lower() if domain else None
        if clean_domain == "all":
            clean_domain = None
        clean_code = procedure_code.strip().upper() if procedure_code else None
        if clean_code == "ALL":
            clean_code = None

        # 1. Thử qua SQLAlchemy Engine trực tiếp
        if not is_postgres_circuit_open():
            engine = get_engine()
            if engine:
                try:
                    where_clauses = ["is_active = true"]
                    params: Dict[str, Any] = {}
                    if clean_domain:
                        where_clauses.append("domain = :domain")
                        params["domain"] = clean_domain
                    if clean_code:
                        where_clauses.append("UPPER(procedure_code) = :code")
                        params["code"] = clean_code

                    where_sql = " AND ".join(where_clauses)
                    sql = (
                        f"SELECT id, procedure_code, procedure_name, domain, name, file_name, "
                        f"format, media_type, file_size, order_index, is_active "
                        f"FROM public.form_templates WHERE {where_sql} ORDER BY order_index, name"
                    )
                    with db_connection() as conn:
                        rows = conn.execute(text(sql), params).mappings().all()
                        items = []
                        for r in rows:
                            d = dict(r)
                            d["id"] = str(d["id"])
                            d["domain_label"] = _domain_label(d["domain"])
                            d["size"] = d.get("file_size", 0)
                            d["viewable"] = _is_viewable(d.get("format", ""))
                            items.append(d)

                        if q:
                            words = remove_accents(q.strip().casefold()).split()
                            items = [
                                it for it in items
                                if all(w in remove_accents(f"{it['name']} {it.get('procedure_name','')} {it.get('procedure_code','')}").casefold() for w in words)
                            ]
                        return items
                except (OperationalError, DBAPIError, ProcedureDbUnavailable) as exc:
                    logger.warning(f"Lỗi truy vấn SQL form_templates: {exc}. Chuyển hướng PostgREST.")
                    mark_postgres_failed(60.0)
                except Exception as exc:
                    logger.warning(f"Lỗi không xác định khi query form_templates: {exc}")

        # 2. Thử qua Supabase PostgREST Fallback
        supabase_url, _ = get_supabase_config()
        if supabase_url:
            try:
                query_parts = ["select=id,procedure_code,procedure_name,domain,name,file_name,format,media_type,file_size,order_index,is_active", "is_active=eq.true"]
                if clean_domain:
                    query_parts.append(f"domain=eq.{clean_domain}")
                if clean_code:
                    query_parts.append(f"procedure_code=eq.{clean_code}")
                query_parts.append("order=order_index.asc,name.asc")

                rest_url = f"{supabase_url}/rest/v1/form_templates?{'&'.join(query_parts)}"
                res = _session.get(rest_url, headers=_supabase_headers(), timeout=5)
                if res.status_code == 200:
                    items = res.json()
                    for it in items:
                        it["domain_label"] = _domain_label(it["domain"])
                        it["size"] = it.get("file_size", 0)
                        it["viewable"] = _is_viewable(it.get("format", ""))

                    if q:
                        words = remove_accents(q.strip().casefold()).split()
                        items = [
                            it for it in items
                            if all(w in remove_accents(f"{it['name']} {it.get('procedure_name','')} {it.get('procedure_code','')}").casefold() for w in words)
                        ]
                    return items
            except Exception as e:
                logger.warning(f"Lỗi gọi Supabase PostgREST form_templates: {e}")

        # 3. Fallback đọc từ đĩa (chống sập hệ thống)
        logger.info("Sử dụng fallback danh sách biểu mẫu từ thư mục cục bộ.")
        return _local_disk_fallback_list(clean_domain, clean_code, q)

    @staticmethod
    def get_template_file(template_id: str) -> Optional[Dict[str, Any]]:
        """Lấy nội dung binary của biểu mẫu theo ID (ưu tiên RAM Cache & đĩa cục bộ, sau đó DB)."""
        clean_id = template_id.strip()

        # 0. Kiểm tra RAM cache
        if clean_id in _FILE_CACHE:
            return _FILE_CACHE[clean_id]

        # 0.1 Fast-path: đọc từ đĩa cục bộ nếu đã biết metadata
        meta = _METADATA_BY_ID.get(clean_id)
        if meta and meta.get("file_name"):
            fname = meta["file_name"]
            for folder in (LAND_DIR, BIZ_DIR):
                if not os.path.exists(folder):
                    continue
                p = os.path.join(folder, fname)
                if os.path.exists(p):
                    try:
                        with open(p, "rb") as f:
                            b = f.read()
                        res = {
                            "id": clean_id,
                            "file_name": fname,
                            "media_type": meta.get("media_type") or ("application/pdf" if meta.get("format") == "PDF" else "application/octet-stream"),
                            "format": meta.get("format") or "PDF",
                            "file_bytes": b,
                        }
                        _FILE_CACHE[clean_id] = res
                        return res
                    except Exception as err:
                        logger.warning(f"Lỗi đọc file nhanh từ đĩa {p}: {err}")

        # 1. Thử qua SQLAlchemy
        if not is_postgres_circuit_open():
            engine = get_engine()
            if engine:
                try:
                    with db_connection() as conn:
                        row = conn.execute(
                            text("SELECT id, name, file_name, format, media_type, file_data, domain FROM public.form_templates WHERE CAST(id AS TEXT) = :id LIMIT 1"),
                            {"id": clean_id},
                        ).mappings().fetchone()
                        if row:
                            raw_data = row["file_data"]
                            if isinstance(raw_data, memoryview):
                                raw_bytes = raw_data.tobytes()
                            elif isinstance(raw_data, str) and raw_data.startswith("\\x"):
                                raw_bytes = bytes.fromhex(raw_data[2:])
                            elif isinstance(raw_data, bytes):
                                raw_bytes = raw_data
                            else:
                                raw_bytes = b""

                            res = {
                                "id": str(row["id"]),
                                "file_name": row["file_name"],
                                "media_type": row["media_type"],
                                "format": row["format"],
                                "file_bytes": raw_bytes,
                            }
                            _FILE_CACHE[clean_id] = res
                            return res
                except (OperationalError, DBAPIError, ProcedureDbUnavailable) as exc:
                    logger.warning(f"Lỗi lấy file_data từ DB: {exc}. Chuyển sang PostgREST.")
                    mark_postgres_failed(60.0)
                except Exception as exc:
                    logger.warning(f"Lỗi không xác định khi query get_template_file: {exc}")

        # 2. Thử qua PostgREST
        supabase_url, _ = get_supabase_config()
        if supabase_url:
            try:
                rest_url = f"{supabase_url}/rest/v1/form_templates?id=eq.{clean_id}&select=id,name,file_name,format,media_type,file_data,domain&limit=1"
                res = _session.get(rest_url, headers=_supabase_headers(), timeout=10)
                if res.status_code == 200 and res.json():
                    row = res.json()[0]
                    raw_data = row.get("file_data") or ""
                    if raw_data.startswith("\\x"):
                        raw_bytes = bytes.fromhex(raw_data[2:])
                    else:
                        raw_bytes = raw_data.encode("latin1") if isinstance(raw_data, str) else b""

                    res = {
                        "id": str(row["id"]),
                        "file_name": row["file_name"],
                        "media_type": row["media_type"],
                        "format": row["format"],
                        "file_bytes": raw_bytes,
                    }
                    _FILE_CACHE[clean_id] = res
                    return res
            except Exception as e:
                logger.warning(f"Lỗi lấy file qua PostgREST: {e}")

        # 3. Fallback tìm file trên đĩa
        target_token = clean_id
        for prefix in ("local-land-", "local-business-", "local-"):
            if target_token.startswith(prefix):
                target_token = target_token[len(prefix):]
                break

        for folder in (BIZ_DIR, LAND_DIR):
            if not os.path.exists(folder):
                continue
            for fname in os.listdir(folder):
                if fname.startswith("~$") or fname.startswith("."):
                    continue
                if target_token == fname or target_token in fname or fname.startswith(target_token):
                    full_p = os.path.join(folder, fname)
                    with open(full_p, "rb") as f:
                        b = f.read()
                    ext = os.path.splitext(fname)[1].lower().replace(".", "").upper()
                    if ext == "PDF":
                        mtype = "application/pdf"
                    elif ext == "DOCX":
                        mtype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    elif ext == "DOC":
                        mtype = "application/msword"
                    elif ext in ("PNG", "JPG", "JPEG"):
                        mtype = f"image/{ext.lower() if ext != 'JPG' else 'jpeg'}"
                    else:
                        mtype = "application/octet-stream"

                    return {
                        "id": clean_id,
                        "file_name": fname,
                        "media_type": mtype,
                        "format": ext,
                        "file_bytes": b,
                    }
        return None

    @staticmethod
    def get_template_by_procedure_code(procedure_code: str, preferred_format: str = "DOCX") -> Optional[Dict[str, Any]]:
        """Lấy biểu mẫu Word hoặc PDF chuẩn theo mã thủ tục (ví dụ: DD-001, DN-003)."""
        clean_code = procedure_code.strip().upper()
        templates = TemplateRepository.list_templates(procedure_code=clean_code)
        if not templates:
            return None

        pref = preferred_format.upper()
        if pref == "PDF":
            pdf_match = next((t for t in templates if t.get("format") == "PDF"), None)
            if pdf_match:
                return TemplateRepository.get_template_file(pdf_match["id"])

        # Ưu tiên lấy file Word (DOCX trước, sau đó DOC)
        docx_match = next((t for t in templates if t.get("format") == "DOCX"), None)
        if docx_match:
            return TemplateRepository.get_template_file(docx_match["id"])

        doc_match = next((t for t in templates if t.get("format") == "DOC"), None)
        if doc_match:
            return TemplateRepository.get_template_file(doc_match["id"])

        # Lấy file đầu tiên nếu không có Word
        return TemplateRepository.get_template_file(templates[0]["id"])
