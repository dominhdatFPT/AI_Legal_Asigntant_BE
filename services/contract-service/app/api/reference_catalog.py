"""Public blank source documents, loaded directly from the PostgreSQL database (Supabase).

All template items, classifications and procedures are stored and queried from
public.contract_reference_catalog and public.reference_classifications.
"""
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Literal
import time
import unicodedata
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool
from app.core.config import DatabaseSettings
from app.core.errors import ContractError, success
from app.core.security import catalog_admin
from app.repositories.reference_uploads import create_upload, get_uploaded_asset, list_uploads
from app.services.reference_pdf import MAX_PDF_BYTES, prepare_reference_pdf

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/v1/contracts', tags=['reference-catalog'])
ROOT = Path(__file__).resolve().parents[2] / 'templates/reference-catalog'

_CACHE = {"data": None, "timestamp": 0.0}
_CACHE_TTL = 30.0  # seconds
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

        with psycopg.connect(db_url, connect_timeout=5, prepare_threshold=None,
                             row_factory=dict_row, options="-c statement_timeout=5000 -c lock_timeout=2000") as conn:
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
                return result
    except Exception as e:
        logger.warning(f"Database catalog lookup failed: {e}")
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
    items = []
    for item in data['items']:
        if domain and item['domain'] != domain or status and item['content_status'] != status:
            continue
        if procedure and not any(procedure in (p['key'], p.get('code')) for p in item['procedures']):
            continue
        content = normalized(' '.join([item['name'], item.get('description') or '', item.get('source_filename') or '',
                                      *[p['name'] + ' ' + (p.get('code') or '') for p in item['procedures']]]))
        if all(word in content for word in words):
            items.append(item)
    total = len(items)
    return success(request, {'items': items[(page - 1) * page_size:page * page_size],
        'pagination': {'page': page, 'page_size': page_size, 'total': total, 'total_pages': (total + page_size - 1) // page_size},
        'domains': data['domains'], 'classifications': data['classifications'], 'counts': data['counts'],
        'revision': data['revision'], 'read_only': True, 'data_source': data.get('data_source', 'database'),
        'asset_base_path': '/api/v1/contracts/reference-assets/'})


@router.post('/reference-catalog', status_code=201)
async def upload_reference(request: Request, file: UploadFile = File(...),
                           name: str = Form(..., min_length=5, max_length=255),
                           domain: Literal['land', 'business'] = Form(...),
                           procedure: str = Form(..., min_length=1, max_length=100),
                           user=Depends(catalog_admin)):
    name = name.strip()
    if len(name) < 5 or '\x00' in name:
        raise ContractError('INVALID_NAME', 'Tên mẫu đơn phải có từ 5 đến 255 ký tự.', 422)
    catalog = await run_in_threadpool(snapshot)
    classification = next((entry for entry in catalog['classifications']
                           if entry['key'] == procedure and entry['domain'] == domain), None)
    if classification is None:
        raise ContractError('INVALID_PROCEDURE', 'Chọn thủ tục thuộc đúng lĩnh vực của mẫu đơn.', 422)
    data = await file.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise ContractError('PDF_TOO_LARGE', 'Tệp PDF không được lớn hơn 10 MB.', 422)
    item, images = await run_in_threadpool(prepare_reference_pdf, data, file.filename, name, domain, classification)
    await run_in_threadpool(create_upload, item, data, images, user.id)
    _CACHE.update(data=None, timestamp=0.0)
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
