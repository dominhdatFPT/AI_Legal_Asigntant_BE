"""Keep public uploaded PDFs and their page previews in the Contract database."""
from contextlib import contextmanager
import re
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from app.core.config import DatabaseSettings
from app.core.database import pooled_connection
from app.core.errors import ContractError

ASSET_NAME = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:-page-([1-9][0-9]{0,2})\.png|\.pdf)")


from app.core.database import open_fresh_connection

_BYTEA_CHUNK_SIZE = 49152


def _resolved_url(url: str = '') -> str:
    if url:
        return url.replace('postgresql+psycopg://', 'postgresql://', 1)
    return DatabaseSettings.from_env(default_tls=True).database_url.replace('postgresql+psycopg://', 'postgresql://', 1)


@contextmanager
def connection(url: str = '', *, fresh: bool = False, use_dict_row: bool = False, autocommit: bool = False):
    resolved = _resolved_url(url)
    try:
        cm = (
            open_fresh_connection(resolved, connect_timeout=5, use_dict_row=use_dict_row, autocommit=autocommit)
            if fresh
            else pooled_connection(resolved, connect_timeout=5, use_dict_row=use_dict_row)
        )
        with cm as conn:
            yield conn
    except psycopg.errors.UndefinedTable:
        raise ContractError('REFERENCE_UPLOAD_SCHEMA_NOT_READY', 'Chưa cập nhật database cho chức năng tải PDF.') from None
    except psycopg.Error:
        raise ContractError('REFERENCE_UPLOAD_UNAVAILABLE', 'Không thể lưu hoặc đọc mẫu PDF, vui lòng thử lại.') from None


def list_uploads(conn=None, *, db_url: str | None = None, batch_size: int = 3):
    # Older installations can still read the packaged catalog before migration.
    if conn is not None and not db_url:
        if conn.execute("SELECT to_regclass('public.contract_reference_uploads')").fetchone()['to_regclass'] is None:
            return []
        return [row['metadata'] for row in conn.execute(
            'SELECT metadata FROM public.contract_reference_uploads ORDER BY created_at DESC, id'
        ).fetchall()]

    target_url = _resolved_url(db_url or '')
    uploads = []
    offset = 0
    while True:
        try:
            with connection(target_url, fresh=True, use_dict_row=True, autocommit=True) as batch_conn:
                batch = batch_conn.execute(
                    'SELECT metadata FROM public.contract_reference_uploads ORDER BY created_at DESC, id LIMIT %s OFFSET %s',
                    (batch_size, offset),
                ).fetchall()
        except ContractError as exc:
            if offset == 0 and exc.code == 'REFERENCE_UPLOAD_SCHEMA_NOT_READY':
                return []
            raise
        uploads.extend(row['metadata'] for row in batch)
        if len(batch) < batch_size:
            break
        offset += batch_size
    return uploads


def create_upload(item, data, images, owner, *, conn=None):
    if conn is None:
        with connection() as upload_conn:
            return create_upload(item, data, images, owner, conn=upload_conn)
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
    col_subquery = (
        'SELECT page_images[%s] AS val FROM public.contract_reference_uploads WHERE id = %s'
        if page
        else 'SELECT file_data AS val FROM public.contract_reference_uploads WHERE id = %s'
    )
    base_args = (int(page), UUID(template_id)) if page else (UUID(template_id),)

    # Still 48 KB per query (small TCP bursts), but one connection per asset instead of one per chunk.
    with connection(fresh=True, autocommit=True) as conn:
        row = conn.execute(
            f'SELECT octet_length(val), substring(val from 1 for %s) FROM ({col_subquery}) AS _asset',
            (_BYTEA_CHUNK_SIZE, *base_args),
        ).fetchone()
        if not row or row[0] is None:
            raise ContractError('REFERENCE_NOT_FOUND', 'Không tìm thấy tài liệu.', 404)

        total_len = int(row[0])
        chunks = [bytes(row[1] or b'')]
        offset = 1 + len(chunks[0])
        while offset <= total_len:
            chunk_row = conn.execute(
                f'SELECT substring(val from %s for %s) FROM ({col_subquery}) AS _asset',
                (offset, _BYTEA_CHUNK_SIZE, *base_args),
            ).fetchone()
            chunk = bytes(chunk_row[0]) if chunk_row and chunk_row[0] is not None else b''
            if not chunk:
                break
            chunks.append(chunk)
            offset += len(chunk)

    return b''.join(chunks), 'image/png' if page else 'application/pdf'
