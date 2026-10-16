"""Ảnh thẻ luật sư + JSON OCR (bảng document_lawyer_card_ocr, migration 006).

Thứ tự: giữ chỗ (processing) trong transaction ngắn → ghi Storage safe/ → OCR (không giữ transaction) → cập nhật.
OCR lỗi vẫn giữ ảnh (status failed) để người dùng nhập tay và admin xem ảnh. Storage lỗi thì xóa chỗ đã giữ.
Không trả storage key / raw OCR ra ngoài.
"""

import logging
from uuid import uuid4

from psycopg.types.json import Jsonb

from app.core.errors import DocumentError
from app.services.lawyer_card_ocr import FAILURE_MESSAGES, read_card_safely

logger = logging.getLogger("document-service")
COLUMNS = "id, owner_user_id, status, fields, warnings, error_code, engine, storage_key, created_at"
VIEW_TTL_SECONDS = 300


def _not_found():
    return DocumentError("NOT_FOUND", "Không tìm thấy ảnh thẻ luật sư.", 404)


def _status(row):
    # processing chỉ còn khi crash giữa chừng: với bên ngoài coi như OCR lỗi, ảnh có thể chưa lưu xong.
    if row["status"] == "processing":
        return "failed", "OCR_UNAVAILABLE"
    return row["status"], row["error_code"]


def public_view(row):
    status, code = _status(row)
    return {"card_id": row["id"], "ocr_status": status, "fields": row["fields"] if status == "completed" else None,
            "warnings": row["warnings"] or [], "error_code": code,
            "error_message": FAILURE_MESSAGES.get(code) if code else None, "created_at": row["created_at"]}


class LawyerCards:
    def __init__(self, database, storage, provider):
        self.database, self.storage, self.provider = database, storage, provider

    def _row(self, card_id, owner=None):
        query = f"SELECT {COLUMNS} FROM public.document_lawyer_card_ocr WHERE id=%s"
        params = [card_id]
        if owner is not None:
            query += " AND owner_user_id=%s"
            params.append(owner)
        with self.database.connection(read_only=True) as conn:
            row = conn.execute(query, params).fetchone()
        if row is None:
            raise _not_found()
        return row

    def detail(self, owner, card_id):
        return public_view(self._row(card_id, owner))

    def internal(self, card_id):
        row = self._row(card_id)
        status, code = _status(row)
        return {"card_id": row["id"], "owner_user_id": row["owner_user_id"], "created_at": row["created_at"],
                "ocr_result": {"status": status, "fields": row["fields"] if status == "completed" else None,
                               "warnings": row["warnings"] or [], "error_code": code, "engine": row["engine"]}}

    def view_url(self, card_id):
        row = self._row(card_id)
        if row["status"] == "processing":
            raise _not_found()
        url = self.storage.signed_download(row["storage_key"], "lawyer-card.jpg", ttl=VIEW_TTL_SECONDS)
        return {"card_id": row["id"], "url": url, "expires_in": VIEW_TTL_SECONDS}

    def create(self, owner, key, image, fingerprint):
        card_id = uuid4()
        storage_key = f"safe/{owner}/lawyer-card/{card_id}.jpg"
        with self.database.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"lawyer-card:{owner}:{key}",))
            prior = conn.execute("SELECT id, request_hash, status FROM public.document_lawyer_card_ocr "
                                 "WHERE owner_user_id=%s AND idempotency_key=%s", (owner, key)).fetchone()
            if prior is None:
                conn.execute("INSERT INTO public.document_lawyer_card_ocr "
                             "(id, owner_user_id, idempotency_key, request_hash, storage_key, sha256, size_bytes, engine) "
                             "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                             (card_id, owner, key, fingerprint, storage_key, image.sha256, len(image.content),
                              self.provider.engine))
        if prior is not None:
            if prior["request_hash"] != fingerprint:
                raise DocumentError("IDEMPOTENCY_CONFLICT", "Idempotency-Key đã dùng cho ảnh khác.", 409)
            if prior["status"] == "processing":
                raise DocumentError("CONFLICT", "Ảnh thẻ đang được xử lý, vui lòng thử lại sau.", 409)
            return self.detail(owner, prior["id"])

        try:
            self.storage.put_safe(storage_key, image.content, image.media_type)
        except DocumentError:
            try:
                with self.database.connection() as conn:
                    conn.execute("DELETE FROM public.document_lawyer_card_ocr WHERE id=%s AND status='processing'",
                                 (card_id,))
            except DocumentError:
                logger.warning("Lawyer card reservation cleanup failed card_id=%s", card_id)
            raise

        outcome = read_card_safely(self.provider, image)
        with self.database.connection() as conn:
            conn.execute("UPDATE public.document_lawyer_card_ocr SET status=%s, fields=%s, warnings=%s, raw=%s, "
                         "error_code=%s, updated_at=now() WHERE id=%s AND status='processing'",
                         (outcome.status, Jsonb(outcome.fields) if outcome.fields is not None else None,
                          Jsonb(outcome.warnings), Jsonb(outcome.raw) if outcome.raw is not None else None,
                          outcome.error_code, card_id))
        return self.detail(owner, card_id)
