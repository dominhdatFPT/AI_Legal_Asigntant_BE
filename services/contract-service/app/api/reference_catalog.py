"""Public blank source documents, isolated from authenticated/private contracts.

Only the packaged 30-document manifest and its immutable asset whitelist can be
read here. These routes never access DB, Storage, user documents or generators.
"""
import json
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from app.core.errors import ContractError, success

router = APIRouter(prefix='/api/v1/contracts', tags=['reference-catalog'])
ROOT = Path(__file__).resolve().parents[2] / 'templates/reference-catalog'


@lru_cache(maxsize=1)
def snapshot():
    try:
        result = json.loads((ROOT / 'catalog.json').read_text(encoding='utf-8'))
        if result.get('read_only') is not True or any(i.get('view_only') is not True or i.get('can_fill') is not False for i in result['items']):
            raise ValueError('Not a read-only manifest')
        return result
    except (OSError, ValueError, KeyError, TypeError):
        raise ContractError('REFERENCE_CATALOG_UNAVAILABLE', 'Kho mẫu chưa sẵn sàng.') from None


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
        content = normalized(' '.join([item['name'], item['description'], item['source_filename'],
                                      *[p['name'] + ' ' + (p['code'] or '') for p in item['procedures']]]))
        if all(word in content for word in words):
            items.append(item)
    total = len(items)
    return success(request, {'items': items[(page - 1) * page_size:page * page_size],
        'pagination': {'page': page, 'page_size': page_size, 'total': total, 'total_pages': (total + page_size - 1) // page_size},
        'domains': data['domains'], 'classifications': data['classifications'], 'counts': data['counts'],
        'revision': data['revision'], 'read_only': True, 'asset_base_path': '/api/v1/contracts/reference-assets/'})


@router.get('/reference-catalog/{template_id}')
def get_reference(request: Request, template_id: UUID):
    item = next((i for i in snapshot()['items'] if i['id'] == str(template_id)), None)
    if item is None:
        raise ContractError('REFERENCE_NOT_FOUND', 'Không tìm thấy mẫu.', 404)
    return success(request, item)


@router.get('/reference-assets/{asset_name}')
def get_asset(asset_name: str):
    # Manifest membership is required before any path is used. No user-supplied
    # Storage keys, path traversal, arbitrary filenames or private results.
    if asset_name not in snapshot()['asset_hashes'] or Path(asset_name).name != asset_name:
        raise ContractError('REFERENCE_NOT_FOUND', 'Không tìm thấy tài liệu.', 404)
    target = ROOT / asset_name
    if not target.is_file():
        raise ContractError('REFERENCE_ASSET_UNAVAILABLE', 'Trang tài liệu chưa sẵn sàng.')
    return FileResponse(target, media_type='application/pdf' if target.suffix == '.pdf' else 'image/png',
                        headers={'Content-Disposition': f'inline; filename="{asset_name}"', 'X-Content-Type-Options': 'nosniff'})
