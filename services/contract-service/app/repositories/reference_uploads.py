"""Keep public uploaded PDFs and their page previews in the Contract database."""
from contextlib import contextmanager
import re
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from app.core.config import DatabaseSettings
from app.core.errors import ContractError

ASSET_NAME = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:-page-([1-9][0-9]{0,2})\.png|\.pdf)")


@contextmanager
def connection():
    url = DatabaseSettings.from_env(default_tls=True).database_url.replace('postgresql+psycopg://', 'postgresql://', 1)
    try:
        with psycopg.connect(url, connect_timeout=5, prepare_threshold=None,
                             options='-c statement_timeout=15000 -c lock_timeout=3000') as conn:
            yield conn
    except psycopg.errors.UndefinedTable:
        raise ContractError('REFERENCE_UPLOAD_SCHEMA_NOT_READY', 'Chưa cập nhật database cho chức năng tải PDF.') from None
    except psycopg.Error:
        raise ContractError('REFERENCE_UPLOAD_UNAVAILABLE', 'Không thể lưu hoặc đọc mẫu PDF, vui lòng thử lại.') from None


def list_uploads(conn):
    # Older installations can still read the packaged catalog before migration.
    if conn.execute("SELECT to_regclass('public.contract_reference_uploads')").fetchone()['to_regclass'] is None:
        return []
    return [row['metadata'] for row in conn.execute(
        'SELECT metadata FROM public.contract_reference_uploads ORDER BY created_at DESC, id'
    ).fetchall()]


def create_upload(item, data, images, owner):
    with connection() as conn:
        saved = conn.execute('''
            INSERT INTO public.contract_reference_uploads
                (id, domain, procedure_key, source_sha256, metadata, file_data, page_images, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (domain, procedure_key, source_sha256) DO NOTHING RETURNING id
        ''', (UUID(item['id']), item['domain'], item['procedure'], item['source_sha256'],
              Jsonb(item), data, images, UUID(owner))).fetchone()
        if saved is None:
            raise ContractError('REFERENCE_ALREADY_EXISTS', 'PDF này đã có trong thủ tục được chọn.', 409)
    return item


def get_uploaded_asset(name):
    match = ASSET_NAME.fullmatch(name)
    if match is None:
        return None
    template_id, page = match.groups()
    with connection() as conn:
        if page:
            row = conn.execute('SELECT page_images[%s] FROM public.contract_reference_uploads WHERE id = %s',
                               (int(page), UUID(template_id))).fetchone()
        else:
            row = conn.execute('SELECT file_data FROM public.contract_reference_uploads WHERE id = %s',
                               (UUID(template_id),)).fetchone()
    if not row or row[0] is None:
        raise ContractError('REFERENCE_NOT_FOUND', 'Không tìm thấy tài liệu.', 404)
    return bytes(row[0]), 'image/png' if page else 'application/pdf'
