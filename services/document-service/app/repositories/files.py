import math
import logging
from uuid import UUID, uuid4
from datetime import datetime, timezone, timedelta

from fastapi.encoders import jsonable_encoder

from app.core.errors import DocumentError

# Latest attempt, never expose storage paths/hashes of the uploaded quarantine object.
JOIN = """ FROM public.user_files f JOIN LATERAL (
    SELECT id, status FROM public.document_processing_jobs WHERE document_id=f.id
    ORDER BY attempt DESC LIMIT 1
) j ON true """
PUBLIC = """SELECT f.id AS document_id, f.owner_user_id, f.purpose, f.document_type,
    f.original_filename, f.media_type, f.uploaded_size_bytes, f.size_bytes, f.sha256,
    f.file_status, f.error_code, j.id AS processing_job_id, j.status AS processing_status,
    f.created_at, f.updated_at """


class Files:
    def __init__(self, database, storage):
        self.database, self.storage = database, storage

    def detail(self, owner, document_id):
        with self.database.connection(read_only=True) as conn:
            row = conn.execute(PUBLIC + JOIN + "WHERE f.owner_user_id=%s AND f.id=%s AND f.deleted_at IS NULL",
                               (owner, document_id)).fetchone()
        if row is None:
            raise DocumentError("DOCUMENT_NOT_FOUND", "Không tìm thấy tài liệu.", 404)
        return jsonable_encoder(row)

    @staticmethod
    def _available(conn, owner, document_id):
        # Shared lock serializes authorization/link issuance against soft-delete.
        row = conn.execute("SELECT * FROM public.user_files WHERE owner_user_id=%s AND id=%s "
                           "AND deleted_at IS NULL FOR SHARE", (owner, document_id)).fetchone()
        if row is None:
            raise DocumentError("DOCUMENT_NOT_FOUND", "Không tìm thấy tài liệu.", 404)
        if row["file_status"] == "rejected":
            raise DocumentError("FILE_REJECTED", "File không đạt kiểm tra an toàn.", 409)
        if row["file_status"] != "available":
            raise DocumentError("FILE_NOT_READY", "File chưa kiểm tra xong, vui lòng thử lại sau.", 409)
        prefix = f"{row['owner_user_id']}/{row['id']}/"
        if not isinstance(row["storage_key"], str) or not row["storage_key"].startswith(("quarantine/" + prefix, "safe/" + prefix)):
            raise DocumentError("STORAGE_CONFIGURATION_ERROR", "Nguồn file không khớp metadata.")
        return row

    def content(self, owner, document_id):
        with self.database.connection() as conn:
            row = self._available(conn, owner, document_id)
            content = self.storage.read_verified(row["storage_key"], row["size_bytes"], row["sha256"], row["media_type"])
            return content, row["media_type"], row["original_filename"], row["sha256"]

    def download(self, owner, document_id):
        with self.database.connection() as conn:
            row = self._available(conn, owner, document_id)
            # Conservative expiry from before the signing call, not after network latency.
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=300)
            url = self.storage.signed_download(row["storage_key"], row["original_filename"], ttl=300)
            return jsonable_encoder({"document_id": row["id"], "download_url": url, "expires_at": expires_at,
                                     "size_bytes": row["size_bytes"], "sha256": row["sha256"]})

    def delete(self, owner, document_id):
        with self.database.connection() as conn:
            row = conn.execute("SELECT id, deleted_at FROM public.user_files WHERE owner_user_id=%s AND id=%s FOR UPDATE",
                               (owner, document_id)).fetchone()
            if row is None:
                raise DocumentError("DOCUMENT_NOT_FOUND", "Không tìm thấy tài liệu.", 404)
            if row["deleted_at"] is not None:
                return
            # All job/profile writers lock the file first, including the SQL guard triggers.
            if conn.execute("SELECT 1 FROM public.document_processing_jobs WHERE document_id=%s "
                            "AND status IN ('queued','processing') LIMIT 1", (document_id,)).fetchone():
                raise DocumentError("DOCUMENT_BUSY", "File đang xử lý, chưa thể xóa.", 409)
            if conn.execute("SELECT 1 FROM public.user_identity_profiles WHERE source_document_id=%s "
                            "AND status='active' LIMIT 1", (document_id,)).fetchone():
                raise DocumentError("PROFILE_SOURCE_IN_USE", "Hãy thay hoặc xóa liên kết hồ sơ CCCD trước khi xóa file.", 409)
            conn.execute("UPDATE public.user_files SET deleted_at=now() WHERE id=%s", (document_id,))

    def list(self, owner, page, page_size, purpose=None, document_type=None):
        where = " WHERE f.owner_user_id=%s AND f.deleted_at IS NULL"
        args = [owner]
        for column, value in [("purpose", purpose), ("document_type", document_type)]:
            if value is not None:
                where += " AND f." + column + "=%s"  # Column identifiers are constants, not request data.
                args.append(value)
        with self.database.connection(read_only=True) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            total = conn.execute("SELECT count(*) AS total FROM public.user_files f" + where, args).fetchone()["total"]
            offset = (page - 1) * page_size
            items = [] if offset >= total else conn.execute(
                PUBLIC + JOIN + where + " ORDER BY f.created_at DESC, f.id DESC LIMIT %s OFFSET %s",
                args + [page_size, offset]).fetchall()
        return {"items": jsonable_encoder(items), "pagination": {"page": page, "page_size": page_size,
                "total": total, "total_pages": math.ceil(total / page_size)}}

    @staticmethod
    def replay(row, fingerprint):
        if row["deleted_at"] is not None:
            raise DocumentError("DOCUMENT_DELETED", "File của thao tác upload này đã bị xóa. Hãy dùng key mới.", 410)
        if row["request_hash"] != fingerprint:
            raise DocumentError("IDEMPOTENCY_CONFLICT", "Key upload đã dùng cho một request khác.", 409)

    def upload(self, owner, key, admission):
        document_id, job_id = uuid4(), uuid4()
        object_key = f"quarantine/{owner}/{document_id}/source"
        attempted_storage = False
        try:
            with self.database.connection() as conn:
                # Serialize per owner/key BEFORE touching Storage, including concurrent retries.
                lock = str(UUID(owner)) + ":" + str(key)
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock,))
                row = conn.execute("SELECT id, request_hash, deleted_at FROM public.user_files "
                                   "WHERE owner_user_id=%s AND idempotency_key=%s", (owner, key)).fetchone()
                if row:
                    self.replay(row, admission.request_hash)
                    document_id = row["id"]
                else:
                    attempted_storage = True
                    self.storage.put_quarantine(object_key, admission.content, admission.media_type)
                    conn.execute("""INSERT INTO public.user_files
                        (id, owner_user_id, purpose, document_type, original_filename, media_type,
                         uploaded_size_bytes, uploaded_sha256, storage_key, idempotency_key, request_hash)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (document_id, owner, admission.purpose, admission.document_type, admission.filename,
                         admission.media_type, len(admission.content), admission.uploaded_sha256, object_key,
                         key, admission.request_hash))
                    conn.execute("INSERT INTO public.document_processing_jobs (id, document_id) VALUES (%s,%s)",
                                 (job_id, document_id))
        except Exception:
            if attempted_storage:
                # Commit can succeed despite connection failure. Only delete after authoritative
                # recheck proves no committed metadata; unavailable DB => preserve private object.
                try:
                    with self.database.connection(read_only=True) as check:
                        committed = check.execute("SELECT id FROM public.user_files WHERE id=%s", (document_id,)).fetchone()
                    if committed is None:
                        self.storage.remove_quarantine(object_key)
                except DocumentError:
                    logging.getLogger("document-service").warning("Private quarantine cleanup needs maintenance; document_id=%s", document_id)
            raise
        return self.detail(owner, document_id)
