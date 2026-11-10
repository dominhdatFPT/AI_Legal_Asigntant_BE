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
import time
import unicodedata
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool
from app.core.config import DatabaseSettings
from app.core.database import pooled_connection
from app.core.errors import ContractError, success
from app.core.security import catalog_admin
from app.repositories.reference_uploads import create_upload, get_uploaded_asset, list_uploads
from app.services.reference_pdf import MAX_PDF_BYTES, prepare_reference_pdf

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/v1/contracts', tags=['reference-catalog'])
ROOT = Path(__file__).resolve().parents[2] / 'templates/reference-catalog'

_CACHE = {"data": None, "timestamp": 0.0, "search_index": {}}
_CACHE_TTL = 300.0  # 5 minutes; invalidated on upload / new procedure creation


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


def load_from_database():
    """Tải dữ liệu danh mục biểu mẫu và phân loại thủ tục trực tiếp từ Database PostgreSQL (Supabase)"""
    global _CACHE
    now = time.time()
    if _CACHE["data"] is not None and (now - _CACHE["timestamp"]) < _CACHE_TTL:
        return _CACHE["data"]

    try:
        db_url = os.getenv("CONTRACT_DATABASE_URL", "")
        if not db_url:
            try:
                settings = DatabaseSettings.from_env()
                db_url = settings.database_url
            except Exception:
                pass
        if not db_url:
            return None

        if "sslmode" not in db_url and "localhost" not in db_url and "127.0.0.1" not in db_url:
            db_url = db_url + ("&" if "?" in db_url else "?") + "sslmode=require"
        db_url = db_url.replace("postgresql+psycopg://", "postgresql://", 1)

        with pooled_connection(db_url, connect_timeout=5, use_dict_row=True) as conn:
            # 1. Lấy danh sách phân loại thủ tục từ database
            classes = conn.execute("""
                SELECT key, code, name, domain, kind, procedure_id::text
                FROM public.reference_classifications
                ORDER BY code, name
            """).fetchall()

            # 2. Lấy toàn bộ biểu mẫu tham khảo từ database
            rows = conn.execute("""
                SELECT 
                    id::text, number, name, description, domain, procedure_key as procedure,
                    procedure_code, procedure_name, procedures, page_count, format,
                    pdf_url as pdf, pages, page_dimensions, text_content as text,
                    content_status, review_notes, source_id, source_filename, source_url,
                    source_sha256, pdf_sha256, view_only, can_fill
                FROM public.contract_reference_catalog
                ORDER BY number ASC
            """).fetchall()

            uploads = list_uploads(conn)
            rows = uploads + rows

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
                _CACHE["data"] = result
                _CACHE["timestamp"] = now
                _CACHE["search_index"] = {
                    str(r.get("id", "")): _build_item_search_text(r) for r in rows
                }
                return result
    except Exception as e:
        logger.warning(f"Database catalog lookup failed: {e}")
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
        raise RuntimeError("PROCEDURE_DATABASE_URL environment variable is required and not set")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    elif url.startswith("postgresql+psycopg://"):
        url = "postgresql://" + url[len("postgresql+psycopg://"):]
    return url


def get_next_procedure_code(domain: str) -> str:
    prefix = "DD-" if domain == "land" else "HKD-"
    like_pattern = f"{prefix}%"
    max_num = 0
    proc_url = get_procedure_db_url()
    try:
        with pooled_connection(proc_url, connect_timeout=4, use_dict_row=False) as conn:
            rows = conn.execute(
                "SELECT code FROM public.procedures WHERE status = 'active' AND code ILIKE %s",
                (like_pattern,),
            ).fetchall()
            for (p_code,) in rows:
                if p_code and p_code.upper().startswith(prefix):
                    m = re.search(r"(\d+)", p_code[len(prefix):])
                    if m:
                        max_num = max(max_num, int(m.group(1)))
    except Exception as e:
        logger.warning("Error querying procedures for next code: %s", e)

    try:
        from app.repositories.reference_uploads import connection
        with connection() as conn:
            rows = conn.execute(
                "SELECT code FROM public.reference_classifications WHERE domain = %s AND code ILIKE %s",
                (domain, like_pattern),
            ).fetchall()
            for (c_code,) in rows:
                if c_code and c_code.upper().startswith(prefix):
                    m = re.search(r"(\d+)", c_code[len(prefix):])
                    if m:
                        max_num = max(max_num, int(m.group(1)))
    except Exception as e:
        logger.warning("Error querying classifications for next code: %s", e)

    return f"{prefix}{max_num + 1:03d}"


def create_new_procedure_record(name: str, domain: str, code: str, form_template_id: str | None = None, pdf_data: bytes | None = None, pdf_filename: str | None = None) -> tuple[dict, str]:
    from uuid import uuid4
    from app.repositories.reference_uploads import connection
    proc_id = str(uuid4())
    proc_url = get_procedure_db_url()

    # 1. Insert into public.procedures (Procedure DB)
    try:
        with pooled_connection(proc_url, connect_timeout=5, use_dict_row=False) as conn:
            conn.execute("""
                INSERT INTO public.procedures (id, code, name, domain, description, status, form_template_id, processing_days, fee)
                VALUES (%s, %s, %s, %s, %s, 'active', %s, 3, 0)
            """, (UUID(proc_id), code, name, domain, name, UUID(form_template_id) if form_template_id else None))

            conn.execute("""
                INSERT INTO public.reference_classifications (key, code, name, domain, kind, procedure_id, created_at, updated_at)
                VALUES (%s, %s, %s, %s, 'project_procedure', %s, now(), now())
                ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, code = EXCLUDED.code, procedure_id = EXCLUDED.procedure_id, updated_at = now()
            """, (code, code, name, domain, UUID(proc_id)))

            if form_template_id:
                try:
                    if pdf_data:
                        conn.execute("""
                            UPDATE public.custom_form_templates
                            SET procedure_id = %s, reference_pdf = %s, reference_pdf_filename = %s, updated_at = now()
                            WHERE id = %s
                        """, (proc_id, pdf_data, pdf_filename, UUID(form_template_id)))
                    else:
                        conn.execute("""
                            UPDATE public.custom_form_templates
                            SET procedure_id = %s, updated_at = now()
                            WHERE id = %s
                        """, (proc_id, UUID(form_template_id)))
                except Exception as ex:
                    logger.warning("Could not link custom_form_templates: %s", ex)
    except Exception as e:
        logger.error("Failed creating procedure in procedure DB: %s", e)

    # 2. Insert into public.reference_classifications (Contract DB)
    try:
        with connection() as conn:
            conn.execute("""
                INSERT INTO public.reference_classifications (key, code, name, domain, kind, procedure_id, created_at, updated_at)
                VALUES (%s, %s, %s, %s, 'project_procedure', %s, now(), now())
                ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, code = EXCLUDED.code, procedure_id = EXCLUDED.procedure_id, updated_at = now()
            """, (code, code, name, domain, UUID(proc_id)))
    except Exception as e:
        logger.warning("Failed creating classification in contract DB: %s", e)

    classification = {
        'key': code,
        'code': code,
        'name': name,
        'domain': domain,
        'kind': 'project_procedure',
        'procedure_id': proc_id
    }
    return classification, proc_id


@router.get('/reference-catalog/next-code')
def next_code_endpoint(request: Request, domain: Literal['land', 'business'] = Query('land')):
    code = get_next_procedure_code(domain)
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

    data = await file.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise ContractError('PDF_TOO_LARGE', 'Tệp PDF không được lớn hơn 10 MB.', 422)

    if classification is None:
        # Create as new procedure with auto-incremented 3-digit code
        proc_code = (code.strip().upper() if code and code.strip() else None) or get_next_procedure_code(domain)
        m = re.match(r'^([A-Z]+-)(\d+)$', proc_code)
        if m:
            proc_code = f"{m.group(1)}{int(m.group(2)):03d}"
        classification, _ = await run_in_threadpool(create_new_procedure_record, name, domain, proc_code, form_template_id, data, file.filename)
    item, images = await run_in_threadpool(prepare_reference_pdf, data, file.filename, name, domain, classification)
    await run_in_threadpool(create_upload, item, data, images, user.id)
    _CACHE.update(data=None, timestamp=0.0, search_index={})
    return success(request, item)


@router.get('/reference-catalog/{template_id}')
def get_reference(request: Request, template_id: UUID):
    item = next((i for i in snapshot()['items'] if str(i['id']) == str(template_id)), None)
    if item is None:
        raise ContractError('REFERENCE_NOT_FOUND', 'Không tìm thấy mẫu.', 404)
    return success(request, item)


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
                                 'X-Content-Type-Options': 'nosniff'})
    target = ROOT / asset_name
    if not target.is_file():
        raise ContractError('REFERENCE_ASSET_UNAVAILABLE', 'Trang tài liệu chưa sẵn sàng.')
    return FileResponse(target, media_type='application/pdf' if target.suffix == '.pdf' else 'image/png',
                        headers={'Content-Disposition': f'inline; filename="{asset_name}"', 'X-Content-Type-Options': 'nosniff'})
