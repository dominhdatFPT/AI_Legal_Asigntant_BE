import logging
import mimetypes
import os
import re
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import text

from app.repositories.procedure_repository import ProcedureDbUnavailable, db_connection, remove_accents

logger = logging.getLogger(__name__)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BIZ_DIR = os.path.join(BASE_DIR, "Template các thủ tục kinh doanh")
LAND_DIR = os.path.join(BASE_DIR, "Template các thủ tục đất đai")
PREVIEW_DIR = os.path.join(BASE_DIR, "database", "form_templates_preview")
PREVIEW_LAND_DIR = os.path.join(PREVIEW_DIR, "Template các thủ tục đất đai")
PREVIEW_BIZ_DIR = os.path.join(PREVIEW_DIR, "Template các thủ tục kinh doanh")

DOMAIN_LABELS = {
    "land": "Đất đai & Bất động sản",
    "business": "Doanh nghiệp & Hộ kinh doanh",
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
    """Liệt kê các mẫu đi kèm bản cài đặt cục bộ."""
    if procedure_code and not re.fullmatch(r"(?:DD|HKD|DN|HTX|BHXH)-[0-9]+", procedure_code):
        return []
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


_FILE_CACHE_MAX = 64
_FILE_CACHE: OrderedDict[str, tuple[str, dict]] = OrderedDict()
_FILE_CACHE_LOCK = Lock()


def _allow_local_assets() -> bool:
    return os.getenv("ENVIRONMENT", "").strip().lower() != "production"


def _read_local_file(template_id: str) -> Optional[Dict[str, Any]]:
    if not _allow_local_assets():
        return None
    parts = template_id.split("-", 2)
    if len(parts) != 3 or parts[0] != "local":
        return None
    _, domain, filename = parts
    if not filename or filename.startswith((".", "~$")) or any(char in filename for char in "/\\:"):
        return None

    folders = {
        "land": (PREVIEW_LAND_DIR, LAND_DIR),
        "business": (PREVIEW_BIZ_DIR, BIZ_DIR),
    }
    for folder in folders.get(domain, ()):
        path = Path(folder) / filename
        if path.is_file():
            return {
                "id": template_id,
                "file_name": filename,
                "media_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
                "format": path.suffix.lstrip(".").upper(),
                "file_bytes": path.read_bytes(),
                "preview_pdf": None,
            }
    return None


class TemplateRepository:
    """Đọc danh mục và tệp của kho mẫu đơn tham khảo."""

    @staticmethod
    def list_templates(
        domain: Optional[str] = None,
        procedure_code: Optional[str] = None,
        q: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        clean_domain = domain.strip().lower() if domain else None
        clean_code = procedure_code.strip().upper() if procedure_code else None
        if clean_domain == "all":
            clean_domain = None
        if clean_code == "ALL":
            clean_code = None

        clauses = ["t.is_active = true"]
        params = {}
        if clean_domain:
            clauses.append("t.domain = :domain")
            params["domain"] = clean_domain
        if clean_code:
            clauses.append("UPPER(t.procedure_code) = :code")
            params["code"] = clean_code
        where_sql = " AND ".join(clauses)
        try:
            with db_connection() as conn:
                rows = conn.execute(
                    text(f"""
                        SELECT t.id, t.procedure_code, t.procedure_name, t.domain, t.name,
                               t.file_name, t.format, t.media_type, t.file_size, t.order_index,
                               t.is_active, d.name AS domain_label,
                               t.preview_pdf IS NOT NULL AS has_preview
                        FROM public.form_templates t
                        LEFT JOIN public.procedure_domains d ON d.code = t.domain
                        WHERE {where_sql}
                        ORDER BY d.order_index, t.order_index, t.name
                    """),
                    params,
                ).mappings().all()
        except ProcedureDbUnavailable:
            if not _allow_local_assets():
                raise
            logger.warning("Không đọc được kho mẫu từ cơ sở dữ liệu. Đang dùng mẫu cục bộ.")
            return _local_disk_fallback_list(clean_domain, clean_code, q)

        items = []
        words = remove_accents(q.strip().casefold()).split() if q else []
        for row in rows:
            item = dict(row)
            searchable = remove_accents(
                f"{item['name']} {item['procedure_name'] or ''} {item['procedure_code'] or ''}"
            ).casefold()
            if not all(word in searchable for word in words):
                continue
            item["id"] = str(item["id"])
            item["domain_label"] = item["domain_label"] or _domain_label(item["domain"])
            item["size"] = item["file_size"]
            has_preview = item.pop("has_preview")
            item["viewable"] = bool(has_preview) or _is_viewable(item["format"])
            items.append(item)
        return items

    @staticmethod
    def get_file(template_id: str, preview: bool = True) -> Optional[Dict[str, Any]]:
        file_info = TemplateRepository.get_template_file(template_id)
        if file_info is None:
            return None
        if preview and file_info.get("preview_pdf"):
            return {
                "content": file_info["preview_pdf"],
                "file_name": f"{Path(file_info['file_name']).stem}.pdf",
                "media_type": "application/pdf",
            }
        return {
            "content": file_info["file_bytes"],
            "file_name": file_info["file_name"],
            "media_type": file_info["media_type"],
        }

    @staticmethod
    def get_template_file(template_id: str) -> Optional[Dict[str, Any]]:
        clean_id = str(template_id).strip()
        try:
            clean_id = str(UUID(clean_id))
        except ValueError:
            return _read_local_file(clean_id)

        with _FILE_CACHE_LOCK:
            cached = _FILE_CACHE.get(clean_id)

        with db_connection() as conn:
            version_sql = "CAST(xmin AS text)" if conn.dialect.name == "postgresql" else "CAST(NULL AS text)"
            row = conn.execute(
                text(f"""
                    SELECT file_name, media_type, format, {version_sql} AS version,
                           CASE WHEN {version_sql} = :cached_version THEN NULL ELSE file_data END AS file_data,
                           CASE WHEN {version_sql} = :cached_version THEN NULL ELSE preview_pdf END AS preview_pdf
                    FROM public.form_templates
                    WHERE id = :id AND is_active = true
                """),
                {"id": clean_id, "cached_version": cached[0] if cached else None},
            ).mappings().first()

        if row is None:
            with _FILE_CACHE_LOCK:
                _FILE_CACHE.pop(clean_id, None)
            return None

        version = row["version"]
        if cached is not None and version == cached[0]:
            file_info = cached[1]
        else:
            if not row["file_data"]:
                return None
            file_info = {
                "id": clean_id,
                "file_name": row["file_name"],
                "media_type": row["media_type"],
                "format": row["format"],
                "file_bytes": bytes(row["file_data"]),
                "preview_pdf": bytes(row["preview_pdf"]) if row["preview_pdf"] else None,
            }

        if version is not None:
            with _FILE_CACHE_LOCK:
                _FILE_CACHE[clean_id] = (version, file_info)
                _FILE_CACHE.move_to_end(clean_id)
                if len(_FILE_CACHE) > _FILE_CACHE_MAX:
                    _FILE_CACHE.popitem(last=False)
        return dict(file_info)

    @staticmethod
    def get_template_by_procedure_code(procedure_code: str, preferred_format: str = "DOCX") -> Optional[Dict[str, Any]]:
        clean_code = procedure_code.strip().upper()
        with db_connection() as conn:
            row = conn.execute(text("""
                SELECT id, file_name, media_type, format, file_data, preview_pdf
                FROM public.form_templates
                WHERE upper(procedure_code) = :code AND is_active = true
                ORDER BY CASE
                    WHEN :prefer_pdf = 1 AND upper(format) = 'PDF' THEN 0
                    WHEN upper(format) = 'DOCX' THEN 1
                    WHEN upper(format) = 'DOC' THEN 2 ELSE 3 END,
                    order_index, id
                LIMIT 1
            """), {"code": clean_code, "prefer_pdf": int(preferred_format.upper() == "PDF")}).mappings().first()
        if row is None or not row["file_data"]:
            return None
        return {"id": str(row["id"]), "file_name": row["file_name"],
                "media_type": row["media_type"], "format": row["format"],
                "file_bytes": bytes(row["file_data"]),
                "preview_pdf": bytes(row["preview_pdf"]) if row["preview_pdf"] else None}
