"""Public blank source documents, loaded directly from the PostgreSQL database (Supabase).

All template items, classifications and procedures are stored and queried from
public.contract_reference_catalog and public.reference_classifications.
"""
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Literal, Optional
import threading
import time
import unicodedata
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
from common_auth.context import sanitize_request_id

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool
from app.core.config import DatabaseSettings
from app.core.database import pooled_connection
from app.core.errors import ContractError, success
from app.core.security import catalog_admin
from app.repositories.reference_uploads import create_upload, get_uploaded_asset, list_uploads
from app.services import procedure_events, procedure_registry_client
from app.services.reference_pdf import MAX_PDF_BYTES, prepare_reference_pdf

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/v1/contracts', tags=['reference-catalog'])
ROOT = Path(__file__).resolve().parents[2] / 'templates/reference-catalog'

_CACHE = {"data": None, "timestamp": 0.0, "search_index": {}, "generation": 0}
_CACHE_TTL = 300.0  # 5 minutes; invalidated on upload / new procedure creation
_CACHE_LOCK = threading.Lock()
_CACHE_STATE_LOCK = threading.Lock()
# Not `immutable`: scripts/fix_uploaded_template_font.py rewrites uploaded assets in place.
_ASSET_CACHE_CONTROL = 'public, max-age=3600, stale-while-revalidate=86400'


def _invalidate_cache() -> None:
    with _CACHE_STATE_LOCK:
        _CACHE["generation"] = _CACHE.get("generation", 0) + 1
        _CACHE["data"] = None
        _CACHE["timestamp"] = 0.0
        _CACHE["search_index"] = {}


def _build_item_search_text(item: dict) -> str:
    return normalized(
        ' '.join([
            item.get('name') or '',
            item.get('description') or '',
            item.get('source_filename') or '',
            *[(p.get('name') or '') + ' ' + (p.get('code') or '') for p in (item.get('procedures') or [])],
        ])
    )
_ASSET_HASHES = None


def get_asset_hashes():
    """Lấy danh sách mã băm SHA256 trực tiếp từ tệp tài nguyên, không đọc qua file JSON."""
    global _ASSET_HASHES
    if _ASSET_HASHES is None:
        try:
            _ASSET_HASHES = {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in ROOT.iterdir()
                if p.is_file() and p.suffix.lower() in ('.pdf', '.png')
            }
        except Exception:
            _ASSET_HASHES = {}
    return _ASSET_HASHES


from concurrent.futures import ThreadPoolExecutor

from app.core.database import open_fresh_connection


def _resolve_contract_db_url() -> str:
    db_url = os.getenv("CONTRACT_DATABASE_URL", "")
    if not db_url:
        try:
            settings = DatabaseSettings.from_env()
            db_url = settings.database_url
        except Exception:
            pass
    if not db_url:
        return ""
    if "sslmode" not in db_url and "localhost" not in db_url and "127.0.0.1" not in db_url:
        db_url = db_url + ("&" if "?" in db_url else "?") + "sslmode=require"
    return db_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _load_classifications(db_url: str) -> list[dict]:
    with open_fresh_connection(db_url, connect_timeout=5, use_dict_row=True, autocommit=True) as conn:
        return conn.execute("""
            SELECT key, code, name, domain, kind, procedure_id::text
            FROM public.reference_classifications
            ORDER BY code, name
        """).fetchall()


def _load_catalog_rows_chunked(db_url: str, batch_size: int = 30) -> list[dict]:
    """Tải metadata danh mục contract_reference_catalog theo lô nhỏ (lược text_content lớn để tránh nghẽn TCP)."""
    rows: list[dict] = []
    offset = 0
    sql = """
        SELECT 
            id::text, number, name, description, domain, procedure_key as procedure,
            procedure_code, procedure_name, procedures, page_count, format,
            pdf_url as pdf, pages, page_dimensions, ''::text as text,
            content_status, review_notes, source_id, source_filename, source_url,
            source_sha256, pdf_sha256, view_only, can_fill
        FROM public.contract_reference_catalog
        ORDER BY number ASC, id ASC
        LIMIT %s OFFSET %s
    """
    while True:
        with open_fresh_connection(db_url, connect_timeout=5, use_dict_row=True, autocommit=True) as conn:
            batch = conn.execute(sql, (batch_size, offset)).fetchall()
        rows.extend(batch)
        if len(batch) < batch_size:
            break
        offset += batch_size
    return rows


def _hydrate_catalog_item_text(item: dict) -> dict:
    if item.get("text"):
        return item
    db_url = _resolve_contract_db_url()
    if not db_url:
        return item
    try:
        with open_fresh_connection(db_url, connect_timeout=5, use_dict_row=True, autocommit=True) as conn:
            row = conn.execute(
                "SELECT text_content AS text FROM public.contract_reference_catalog WHERE id = %s::uuid",
                (str(item["id"]),),
            ).fetchone()
        if row and row.get("text") is not None:
            return {**item, "text": row["text"]}
    except Exception as exc:
        logger.warning(f"Failed to hydrate catalog item text: {exc}")
    return item


def _cache_fresh():
    with _CACHE_STATE_LOCK:
        data = _CACHE["data"]
        ts = _CACHE["timestamp"]
    if data is not None and (time.time() - ts) < _CACHE_TTL:
        return data
    return None


def load_from_database():
    """Tải dữ liệu danh mục biểu mẫu và phân loại thủ tục trực tiếp từ Database PostgreSQL (Supabase)"""
    fresh = _cache_fresh()
    if fresh is not None:
        return fresh
    with _CACHE_STATE_LOCK:
        stale = _CACHE["data"]
    # Only one thread refreshes; others serve the captured stale copy, or wait only when nothing is cached yet.
    if not _CACHE_LOCK.acquire(blocking=stale is None):
        return stale
    try:
        fresh = _cache_fresh()
        if fresh is not None:
            return fresh
        return _refresh_from_database()
    finally:
        _CACHE_LOCK.release()


def _refresh_from_database():
    now = time.time()
    with _CACHE_STATE_LOCK:
        gen = _CACHE.get("generation", 0)
    try:
        db_url = _resolve_contract_db_url()
        if not db_url:
            return None

        with ThreadPoolExecutor(max_workers=3) as executor:
            fut_classes = executor.submit(_load_classifications, db_url)
            fut_rows = executor.submit(_load_catalog_rows_chunked, db_url)
            fut_uploads = executor.submit(list_uploads, db_url=db_url)
            classes = fut_classes.result()
            catalog_rows = fut_rows.result()
            uploads = fut_uploads.result()

        rows = uploads + catalog_rows

        if rows or classes:
            asset_hashes = get_asset_hashes()

            domains = [
                {'code': 'land', 'name': 'Đất đai & Bất động sản'},
                {'code': 'business', 'name': 'Doanh nghiệp & Hộ kinh doanh'},
                {'code': 'cooperative', 'name': 'Hợp tác xã'},
                {'code': 'social_insurance', 'name': 'Bảo hiểm xã hội'}
            ]
            result = {
                'read_only': True,
                'domains': domains,
                'classifications': classes,
                'items': rows,
                'counts': {'templates': len(rows), 'pages': sum(r.get('page_count', 1) for r in rows)},
                'revision': 'db-postgres',
                'asset_hashes': asset_hashes,
                'data_source': 'database'
            }
            search_index = {
                str(r.get("id", "")): _build_item_search_text(r) for r in rows
            }
            with _CACHE_STATE_LOCK:
                if _CACHE.get("generation", 0) == gen:
                    _CACHE["data"] = result
                    _CACHE["timestamp"] = now
                    _CACHE["search_index"] = search_index
            return result
    except Exception as e:
        logger.warning(f"Database catalog lookup failed: {e}")
        with _CACHE_STATE_LOCK:
            if _CACHE["data"] is not None:
                return _CACHE["data"]
    return None


def snapshot():
    """Tải snapshot biểu mẫu 100% từ Database PostgreSQL (Supabase), không đọc qua file JSON."""
    db_result = load_from_database()
    if db_result:
        return db_result
    raise ContractError('REFERENCE_CATALOG_UNAVAILABLE', 'Kho mẫu từ cơ sở dữ liệu chưa sẵn sàng.')


def normalized(value):
    value = unicodedata.normalize('NFD', str(value or '')).replace('đ', 'd').replace('Đ', 'D')
    return ''.join(c for c in value if not unicodedata.combining(c)).lower()


@router.get('/reference-catalog')
def list_references(request: Request, domain: str | None = Query(None, max_length=50),
                    procedure: str | None = Query(None, max_length=100), q: str = Query('', max_length=200),
                    status: Literal['source', 'specific_case', 'needs_review'] | None = None,
                    page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=100)):
    data = snapshot()
    words = normalized(q).split()
    search_index = _CACHE.get("search_index") or {}
    items = []
    for item in data['items']:
        if domain and item['domain'] != domain or status and item['content_status'] != status:
            continue
        if procedure and not any(procedure in (p['key'], p.get('code')) for p in item['procedures']):
            continue
        if words:
            item_id = str(item.get('id', ''))
            content = search_index.get(item_id) or _build_item_search_text(item)
            if not all(word in content for word in words):
                continue
        items.append(item)
    total = len(items)
    return success(request, {'items': items[(page - 1) * page_size:page * page_size],
        'pagination': {'page': page, 'page_size': page_size, 'total': total, 'total_pages': (total + page_size - 1) // page_size},
        'domains': data['domains'], 'classifications': data['classifications'], 'counts': data['counts'],
        'revision': data['revision'], 'read_only': True, 'data_source': data.get('data_source', 'database'),
        'asset_base_path': '/api/v1/contracts/reference-assets/'})



def get_procedure_db_url() -> str:
    url = os.getenv("PROCEDURE_DATABASE_URL", "")
    if not url:
        raise ContractError('PROCEDURE_SERVICE_UNAVAILABLE', 'Chưa cấu hình kết nối dịch vụ thủ tục.', 503)
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    elif url.startswith("postgresql+psycopg://"):
        url = "postgresql://" + url[len("postgresql+psycopg://"):]
    return url


_PROC_CODE_LOCK_NS = 7202026
_MAX_PROC_CODE_ATTEMPTS = 5


def _domain_prefix(domain: str) -> str:
    return "DD-" if domain == "land" else "HKD-"


def _domain_lock_key(domain: str) -> int:
    if domain == "land":
        return 1
    if domain == "business":
        return 2
    return int.from_bytes(hashlib.sha256(str(domain).encode("utf-8")).digest()[:4], "big", signed=True)


def _extract_code_number(code: str | None, prefix: str) -> int:
    if code and code.upper().startswith(prefix):
        m = re.search(r"(\d+)", code[len(prefix):])
        if m:
            return int(m.group(1))
    return 0


def _max_procedure_code_num(proc_conn, domain: str) -> int:
    prefix = _domain_prefix(domain)
    like_pattern = f"{prefix}%"
    max_num = 0
    # Đọc toàn bộ mã trong public.procedures (kể cả đã vô hiệu hóa) để không trùng ràng buộc UNIQUE(code).
    rows = proc_conn.execute(
        "SELECT code FROM public.procedures WHERE code ILIKE %s",
        (like_pattern,),
    ).fetchall()
    for (p_code,) in rows:
        max_num = max(max_num, _extract_code_number(p_code, prefix))

    return max(max_num, _contract_floor(domain))


def _contract_floor(domain: str) -> int:
    prefix = _domain_prefix(domain)
    like_pattern = f"{prefix}%"
    max_num = 0
    try:
        from app.repositories.reference_uploads import connection
        with connection() as conn:
            c_rows = conn.execute(
                "SELECT code FROM public.reference_classifications WHERE domain = %s AND code ILIKE %s",
                (domain, like_pattern),
            ).fetchall()
            for (c_code,) in c_rows:
                max_num = max(max_num, _extract_code_number(c_code, prefix))
    except Exception as e:
        logger.warning("Error querying classifications for next code: %s", e)
        return 0
    return max_num


def _procedure_write_mode() -> str:
    mode = os.getenv("PROCEDURE_WRITE_MODE", "auto")
    if mode in ("api", "db"):
        return mode
    return "api" if os.getenv("PROCEDURE_SERVICE_URL") and os.getenv("INTERNAL_TOKEN") else "db"


def get_next_procedure_code(domain: str, *, request_id: str | None = None) -> str:
    if _procedure_write_mode() == "api":
        try:
            return procedure_registry_client.next_code(domain, floor=_contract_floor(domain), request_id=request_id)
        except procedure_registry_client.EndpointMissing:
            pass  # Compatibility with older deployments. The DB path requires its own URL.
    return _get_next_procedure_code_db(domain)


def _get_next_procedure_code_db(domain: str) -> str:
    prefix = _domain_prefix(domain)
    max_num = 0
    proc_url = get_procedure_db_url()
    try:
        with pooled_connection(proc_url, connect_timeout=4, use_dict_row=False) as conn:
            max_num = _max_procedure_code_num(conn, domain)
    except Exception as e:
        logger.warning("Error querying procedures for next code: %s", e)
        try:
            from app.repositories.reference_uploads import connection
            with connection() as conn:
                rows = conn.execute(
                    "SELECT code FROM public.reference_classifications WHERE domain = %s AND code ILIKE %s",
                    (domain, f"{prefix}%"),
                ).fetchall()
                for (c_code,) in rows:
                    max_num = max(max_num, _extract_code_number(c_code, prefix))
        except Exception as e2:
            logger.warning("Error querying classifications for next code: %s", e2)

    return f"{prefix}{max_num + 1:03d}"


def create_new_procedure_record(
    name: str,
    domain: str,
    code: str | None,
    form_template_id: str | None = None,
    pdf_data: bytes | None = None,
    pdf_filename: str | None = None,
    *,
    auto_code: bool = False,
    request_id: str | None = None,
    procedure_id: str | None = None,
    sync_contract: bool = True,
) -> tuple[dict, str]:
    if _procedure_write_mode() == "api":
        proc_id = procedure_id or str(uuid4())
        floor = _contract_floor(domain) if auto_code or not (code or "").strip() else 0
        try:
            classification = procedure_registry_client.register(
                procedure_id=proc_id, name=name, domain=domain, code=None if auto_code else code, floor=floor,
                form_template_id=form_template_id, pdf_data=pdf_data, pdf_filename=pdf_filename,
                request_id=request_id,
            )
        except procedure_registry_client.EndpointMissing:
            pass  # Compatibility with older deployments. The DB path requires its own URL.
        else:
            if sync_contract:
                _upsert_contract_classification(classification["code"], classification["name"], domain, proc_id)
            return classification, proc_id
    return _create_procedure_record_db(
        name, domain, code, form_template_id, pdf_data, pdf_filename,
        auto_code=auto_code, procedure_id=procedure_id, sync_contract=sync_contract,
    )


def _create_procedure_record_db(
    name: str,
    domain: str,
    code: str | None,
    form_template_id: str | None = None,
    pdf_data: bytes | None = None,
    pdf_filename: str | None = None,
    *,
    auto_code: bool = False,
    procedure_id: str | None = None,
    sync_contract: bool = True,
) -> tuple[dict, str]:
    proc_id = procedure_id or str(uuid4())
    proc_url = get_procedure_db_url()
    prefix = _domain_prefix(domain)
    lock_key = _domain_lock_key(domain)

    # 1. Cấp mã và chèn nguyên tử vào public.procedures dưới khóa pg_advisory_xact_lock theo domain.
    min_num = 0
    allocated_code = (code or "").strip().upper()
    if not allocated_code:
        auto_code = True

    for attempt in range(_MAX_PROC_CODE_ATTEMPTS):
        current_code = allocated_code
        inserting_procedure = False
        try:
            with pooled_connection(proc_url, connect_timeout=5, use_dict_row=False) as conn:
                conn.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)",
                    (_PROC_CODE_LOCK_NS, lock_key),
                )
                if procedure_id:
                    existing = conn.execute(
                        "SELECT code, name, domain FROM public.procedures WHERE id = %s", (UUID(proc_id),),
                    ).fetchone()
                    if existing is not None:
                        allocated_code, name, domain = existing
                        break
                if auto_code:
                    next_num = max(_max_procedure_code_num(conn, domain), min_num) + 1
                    current_code = f"{prefix}{next_num:03d}"
                else:
                    next_num = _extract_code_number(current_code, prefix)

                if form_template_id:
                    pdf_columns = ", reference_pdf = %s, reference_pdf_filename = %s" if pdf_data is not None else ""
                    pdf_params = (pdf_data, pdf_filename) if pdf_data is not None else ()
                    linked = conn.execute(f"""
                        UPDATE public.custom_form_templates
                        SET procedure_id = %s, updated_at = now(){pdf_columns}
                        WHERE id = %s AND deleted_at IS NULL AND COALESCE(status, 'draft') <> 'archived'
                          AND (NULLIF(procedure_id, '') IS NULL OR procedure_id = %s)
                    """, (proc_id, *pdf_params, UUID(form_template_id), proc_id))
                    if linked.rowcount != 1:
                        raise ContractError('FORM_TEMPLATE_UNAVAILABLE', 'Không thể gắn biểu mẫu đã chọn.', 422)

                inserting_procedure = True
                conn.execute("""
                    INSERT INTO public.procedures (id, code, name, domain, description, status, form_template_id, processing_days, fee)
                    VALUES (%s, %s, %s, %s, %s, 'active', %s, 3, 0)
                """, (UUID(proc_id), current_code, name, domain, name, UUID(form_template_id) if form_template_id else None))
                inserting_procedure = False
                conn.execute("""
                    INSERT INTO public.reference_classifications (key, code, name, domain, kind, procedure_id, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, 'project_procedure', %s, now(), now())
                    ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, code = EXCLUDED.code, procedure_id = EXCLUDED.procedure_id, updated_at = now()
                """, (current_code, current_code, name, domain, UUID(proc_id)))
            allocated_code = current_code
            break
        except psycopg.errors.UniqueViolation as exc:
            if not inserting_procedure:
                raise ContractError('PROCEDURE_SERVICE_UNAVAILABLE', 'Không thể lưu thủ tục lúc này.', 503) from exc
            if not auto_code:
                raise ContractError(
                    'PROCEDURE_CODE_EXISTS',
                    f'Mã thủ tục {current_code} đã tồn tại.',
                    409,
                ) from None
            min_num = max(min_num, next_num)
            logger.warning(
                "Trùng mã thủ tục tự động %s (lần %d), tự động thử mã kế tiếp",
                current_code,
                attempt + 1,
            )
            if attempt == _MAX_PROC_CODE_ATTEMPTS - 1:
                raise ContractError(
                    'PROCEDURE_CODE_CONFLICT',
                    'Không thể cấp phát mã thủ tục mới lúc này, vui lòng thử lại.',
                    409,
                ) from None
        except ContractError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise ContractError('PROCEDURE_SERVICE_UNAVAILABLE', 'Không thể lưu thủ tục lúc này.', 503) from exc

    # 2. Đồng bộ sang public.reference_classifications (Contract DB) bằng đúng mã đã chốt
    if sync_contract:
        _upsert_contract_classification(allocated_code, name, domain, proc_id)

    classification = {
        'key': allocated_code,
        'code': allocated_code,
        'name': name,
        'domain': domain,
        'kind': 'project_procedure',
        'procedure_id': proc_id
    }
    return classification, proc_id


def _upsert_contract_classification(
    code: str, name: str, domain: str, proc_id: str, *, conn=None, key: str | None = None,
) -> None:
    from app.repositories.reference_uploads import connection

    if conn is None:
        try:
            with connection() as contract_conn:
                _upsert_contract_classification(code, name, domain, proc_id, conn=contract_conn, key=key)
        except ContractError as exc:
            if exc.code != "CONFIGURATION_ERROR" or os.getenv("CONTRACT_DATABASE_URL"):
                raise
        return
    conn.execute("""
        INSERT INTO public.reference_classifications (key, code, name, domain, kind, procedure_id, created_at, updated_at)
        VALUES (%s, %s, %s, %s, 'project_procedure', %s, now(), now())
        ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, code = EXCLUDED.code,
            domain = EXCLUDED.domain, kind = EXCLUDED.kind, procedure_id = EXCLUDED.procedure_id, updated_at = now()
    """, (key or code, code, name, domain, UUID(proc_id)))


def _ensure_procedure_exists(proc_id: str, request_id: str) -> None:
    if _procedure_write_mode() == "api":
        try:
            procedure_registry_client.ensure_registered(proc_id, request_id=request_id)
            return
        except procedure_registry_client.EndpointMissing:
            pass
    try:
        with pooled_connection(get_procedure_db_url(), connect_timeout=5, use_dict_row=False) as conn:
            exists = conn.execute("SELECT 1 FROM public.procedures WHERE id = %s", (UUID(proc_id),)).fetchone()
    except (psycopg.Error, OSError) as exc:
        raise ContractError('PROCEDURE_SERVICE_UNAVAILABLE', 'Không thể kiểm tra thủ tục lúc này.', 503) from exc
    if exists is None:
        raise ContractError('PROCEDURE_NOT_FOUND', 'Không tìm thấy thủ tục.', 404)


def _save_reference_upload(item, data, images, owner, classification, *, new_procedure, request_id):
    from app.repositories.reference_uploads import connection

    if not os.getenv("CONTRACT_DATABASE_URL"):
        return create_upload(item, data, images, owner)

    with connection() as conn:
        if proc_id := item.get('procedure_id'):
            # A purge acquires the same Contract lock after the Procedure delete commits.
            procedure_events.lock_procedure_keys(conn, [item['procedure'], item.get('procedure_code')])
            _ensure_procedure_exists(proc_id, request_id)
            if new_procedure:
                _upsert_contract_classification(
                    classification['code'], classification['name'], classification['domain'], proc_id,
                    conn=conn, key=classification['key'],
                )
        return create_upload(item, data, images, owner, conn=conn)


@router.get('/reference-catalog/next-code')
def next_code_endpoint(request: Request, domain: Literal['land', 'business'] = Query('land')):
    code = get_next_procedure_code(domain, request_id=request.state.request_id)
    return success(request, {'code': code, 'domain': domain})


@router.post('/reference-catalog', status_code=201)
async def upload_reference(request: Request, file: UploadFile = File(...),
                           name: str = Form(..., min_length=5, max_length=255),
                           domain: Literal['land', 'business'] = Form(...),
                           procedure: Optional[str] = Form(None),
                           code: Optional[str] = Form(None),
                           form_template_id: Optional[str] = Form(None),
                           user=Depends(catalog_admin)):
    name = name.strip()
    if len(name) < 5 or '\x00' in name:
        raise ContractError('INVALID_NAME', 'Tên thủ tục phải có từ 5 đến 255 ký tự.', 422)

    catalog = await run_in_threadpool(snapshot)
    classification = None
    if procedure:
        classification = next((entry for entry in catalog['classifications']
                               if entry['key'] == procedure and entry['domain'] == domain), None)
        if classification is None:
            raise ContractError('INVALID_PROCEDURE', 'Thủ tục không thuộc lĩnh vực đã chọn hoặc không tồn tại.', 422)

    data = await file.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise ContractError('PDF_TOO_LARGE', 'Tệp PDF không được lớn hơn 10 MB.', 422)

    if classification is None:
        # Validate và render PDF trước khi mở khóa DB (tránh giữ advisory lock trong lúc render PDF).
        explicit_code = code.strip().upper() if code and code.strip() else None
        if explicit_code:
            m = re.match(r'^([A-Z]+-)(\d+)$', explicit_code)
            if m:
                explicit_code = f"{m.group(1)}{int(m.group(2)):03d}"
        candidate = {'key': explicit_code or 'AUTO', 'code': explicit_code or 'AUTO', 'name': name}
        item, images = await run_in_threadpool(
            prepare_reference_pdf, data, file.filename, name, domain, candidate
        )
        retry_key = sanitize_request_id(request.headers.get("Idempotency-Key") or request.headers.get("X-Request-ID"))
        proc_id = None
        if retry_key:
            fingerprint = json.dumps((retry_key, user.id, domain, name, explicit_code, form_template_id,
                                      item['source_sha256']), default=str)
            proc_id = str(uuid5(NAMESPACE_URL, f"contract-reference-procedure:{fingerprint}"))
        classification, proc_id = await run_in_threadpool(
            create_new_procedure_record, name, domain, explicit_code,
            form_template_id, data, file.filename, auto_code=(explicit_code is None),
            request_id=request.state.request_id,
            procedure_id=proc_id, sync_contract=not os.getenv("CONTRACT_DATABASE_URL"),
        )
        # Đồng bộ lại mã thực tế đã được cấp nguyên tử vào metadata của item trước khi lưu upload.
        item['procedure'] = classification['key']
        item['procedure_code'] = classification['code']
        item['procedures'] = [{k: classification.get(k) for k in ('key', 'code', 'name')}]
        new_procedure = True
    else:
        item, images = await run_in_threadpool(
            prepare_reference_pdf, data, file.filename, name, domain, classification
        )
        proc_id = classification.get('procedure_id')
        new_procedure = False
    if proc_id:
        item['procedure_id'] = str(proc_id)
    await run_in_threadpool(
        _save_reference_upload, item, data, images, user.id, classification,
        new_procedure=new_procedure, request_id=request.state.request_id,
    )
    _invalidate_cache()
    return success(request, item)


@router.get('/reference-catalog/{template_id}')
def get_reference(request: Request, template_id: UUID):
    item = next((i for i in snapshot()['items'] if str(i['id']) == str(template_id)), None)
    if item is None:
        raise ContractError('REFERENCE_NOT_FOUND', 'Không tìm thấy mẫu.', 404)
    return success(request, _hydrate_catalog_item_text(item))


@router.get('/reference-assets/{asset_name}')
def get_asset(asset_name: str):
    # Manifest membership is required before any path is used. No user-supplied
    # Storage keys, path traversal, arbitrary filenames or private results.
    if Path(asset_name).name != asset_name:
        raise ContractError('REFERENCE_NOT_FOUND', 'Không tìm thấy tài liệu.', 404)
    if asset_name not in get_asset_hashes():
        uploaded = get_uploaded_asset(asset_name)
        if uploaded is None:
            raise ContractError('REFERENCE_NOT_FOUND', 'Không tìm thấy tài liệu.', 404)
        content, media_type = uploaded
        return Response(content, media_type=media_type,
                        headers={'Content-Disposition': f'inline; filename="{asset_name}"',
                                 'X-Content-Type-Options': 'nosniff', 'Cache-Control': _ASSET_CACHE_CONTROL})
    target = ROOT / asset_name
    if not target.is_file():
        raise ContractError('REFERENCE_ASSET_UNAVAILABLE', 'Trang tài liệu chưa sẵn sàng.')
    return FileResponse(target, media_type='application/pdf' if target.suffix == '.pdf' else 'image/png',
                        headers={'Content-Disposition': f'inline; filename="{asset_name}"', 'X-Content-Type-Options': 'nosniff',
                                 'Cache-Control': _ASSET_CACHE_CONTROL})
