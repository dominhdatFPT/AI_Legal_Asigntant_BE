"""Persist a two-image OCR session. Provider work never holds a SQL transaction.

Reserve before any external write/call; replay never repeats provider charges.
On crash/ambiguous commit, leave a processing session for manual reconciliation.
Objects remain private and referenced, including failed sessions (no broad cleanup).
"""
import logging
import hashlib
import time
from uuid import uuid4

from psycopg.types.json import Jsonb

from app.core.errors import DocumentError
from app.services.identity_parser import merge_fields, parse_fields, parse_additional_fields, residence_components, field_diagnostics, FIELDS, ADDITIONAL_FIELDS
from app.services.identity_card_match import ensure_card_pair

logger = logging.getLogger("document-service")
PUBLIC = "id AS session_id, status, fields, warnings, verification_status, error_code, created_at, updated_at"


class IdentityOcr:
    def __init__(self, database, storage, provider):
        self.database, self.storage, self.provider = database, storage, provider

    def detail(self, owner, session_id):
        with self.database.connection(read_only=True) as conn:
            row = conn.execute(f"SELECT {PUBLIC},raw_front,raw_back FROM public.document_identity_ocr_sessions WHERE id=%s AND owner_user_id=%s",
                               (session_id, owner)).fetchone()
        if row is None:
            raise DocumentError("NOT_FOUND", "Không tìm thấy phiên đọc CCCD.", 404)
        raw_front = row.pop("raw_front", None) or {}
        raw_back = row.pop("raw_back", None) or {}
        front = parse_additional_fields(raw_front)
        back = parse_additional_fields(raw_back)
        row["card_match_status"] = (raw_back.get("card_pair_check", {}).get("status", "not_checked")
            if row["status"] == "completed" else "not_checked")
        row["card_pair_status"] = row["card_match_status"]
        row["fields"] = {key: (row.get("fields") or {}).get(key) for key in FIELDS}
        row["additional_fields"] = {key: None if front[key] and back[key] and front[key] != back[key]
            else front[key] or back[key] for key in ADDITIONAL_FIELDS}
        components = residence_components(raw_back, row["additional_fields"].get("residence_address"))
        if row["status"] == "completed" and components:
            row["additional_fields"]["residence_address_components"] = components
        if row["card_match_status"] == "matched":
            correction = raw_back.get("card_pair_check", {}).get("corrections", {}).get("expiry_date")
            if correction:
                row["additional_fields"]["expiry_date"] = correction
        observed = row["fields"]
        if row["status"] == "failed" and raw_front and raw_back:
            observed, _ = merge_fields(parse_fields(raw_front), parse_fields(raw_back))
        row["field_diagnostics"] = field_diagnostics(raw_front, raw_back,
            {**observed, **row["additional_fields"]})
        if row["status"] == "failed" and row.get("error_code") == "IDENTITY_PAIR_UNVERIFIABLE":
            row["card_pair_status"] = "unverifiable"
        return row

    def _save_raw(self, owner, session_id, side, raw):
        assert side in {"front", "back"}
        with self.database.connection() as conn:
            conn.execute(f"UPDATE public.document_identity_ocr_sessions SET raw_{side}=%s,updated_at=now() "
                         "WHERE id=%s AND owner_user_id=%s AND status='processing'", (Jsonb(raw), session_id, owner))

    def create(self, owner, key, pair):
        front, back, fingerprint = pair
        namespace = getattr(self.provider, "request_namespace", None)
        if namespace:
            fingerprint = hashlib.sha256((namespace + ":" + fingerprint).encode("ascii")).hexdigest()
        session_id = uuid4()
        prefix = f"quarantine/{owner}/identity/{session_id}/"
        with self.database.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"identity:{owner}:{key}",))
            prior = conn.execute("SELECT id,request_hash,status,error_code,error_status FROM public.document_identity_ocr_sessions "
                                 "WHERE owner_user_id=%s AND idempotency_key=%s", (owner, key)).fetchone()
            if prior:
                if prior["request_hash"] != fingerprint:
                    raise DocumentError("IDEMPOTENCY_CONFLICT", "Idempotency-Key đã dùng cho ảnh khác.", 409)
                if prior["status"] == "processing":
                    raise DocumentError("EKYC_IN_PROGRESS", "Phiên đang xử lý hoặc cần đối soát; không chạy OCR lại.", 409,
                                        {"session_id": str(prior["id"])})
                if prior["status"] == "failed":
                    raise DocumentError(prior["error_code"], "Phiên trước thất bại; không tự chạy OCR lại.", prior["error_status"],
                                        {"session_id": str(prior["id"])})
                session_id = prior["id"]
            else:
                conn.execute("INSERT INTO public.document_identity_ocr_sessions "
                             "(id,owner_user_id,idempotency_key,request_hash,front_storage_key,back_storage_key,"
                             "front_sha256,back_sha256,front_size_bytes,back_size_bytes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                             (session_id, owner, key, fingerprint, prefix+"front.jpg", prefix+"back.jpg",
                              front.sha256, back.sha256, len(front.content), len(back.content)))
        if prior:
            return self.detail(owner, session_id)
        try:
            self.storage.put_quarantine(prefix+"front.jpg", front.content, front.media_type)
            self.storage.put_quarantine(prefix+"back.jpg", back.content, back.media_type)
            deadline = time.monotonic() + 55
            raw_images = {}
            def save_raw(side, raw):
                raw_images[side] = raw
                self._save_raw(owner, session_id, side, raw)
            f = self.provider.read_image(front, deadline=deadline,
                                         on_response=lambda raw: save_raw("front", raw))
            b = self.provider.read_image(back, deadline=deadline,
                                         on_response=lambda raw: save_raw("back", raw))
            fields, warnings = merge_fields(f, b)
            if not f["identity_number"]:
                raise DocumentError("OCR_UNREADABLE", "Chưa đọc được số CCCD và họ tên; chụp rõ hơn và kiểm tra thủ công.", 422)
            pair_check = ensure_card_pair(f, raw_images.get("front", {}), raw_images.get("back", {}))
            raw_images["back"]["card_pair_check"] = pair_check
            self._save_raw(owner, session_id, "back", raw_images["back"])
            warnings.append("CARD_PAIR_MATCHED_NOT_AUTHENTICATED")
            if pair_check.get("corrections"):
                if "expiry_date" in pair_check["corrections"]:
                    warnings.append("OCR_EXPIRY_RECONCILED_WITH_MRZ")
                if "date_of_birth" in pair_check["corrections"]:
                    fields["date_of_birth"] = pair_check["corrections"]["date_of_birth"]
                    warnings.append("OCR_BIRTH_RECONCILED_WITH_MRZ")
                if "full_name" in pair_check["corrections"]:
                    fields["full_name"] = pair_check["corrections"]["full_name"]
                    warnings.append("OCR_NAME_RECONCILED_WITH_MRZ")
            if not f["identity_number"] or not (f["full_name"] or pair_check.get("corrections", {}).get("full_name")):
                raise DocumentError("OCR_UNREADABLE", "Chưa đọc được số CCCD và họ tên; kiểm tra ảnh hoặc nhập tay.", 422)
            warnings.append("OCR_REQUIRES_USER_REVIEW")
            with self.database.connection() as conn:
                conn.execute("UPDATE public.document_identity_ocr_sessions SET status='completed',fields=%s,warnings=%s,updated_at=now() "
                             "WHERE id=%s AND owner_user_id=%s AND status='processing'",
                             (Jsonb(fields), Jsonb(warnings), session_id, owner))
        except Exception as exc:
            error = exc if isinstance(exc, DocumentError) else DocumentError("OCR_UNAVAILABLE", "Không thể hoàn tất phiên đọc CCCD.")
            error.details = {**(error.details or {}), "session_id": str(session_id)}
            # Commit may have succeeded despite a disconnected client. Do not overwrite completed.
            try:
                with self.database.connection() as conn:
                    conn.execute("UPDATE public.document_identity_ocr_sessions SET status='failed',error_code=%s,error_status=%s,"
                                 "updated_at=now() WHERE id=%s AND owner_user_id=%s AND status='processing'",
                                 (error.code, error.status, session_id, owner))
            except DocumentError:
                logger.warning("Identity OCR reconciliation required session_id=%s", session_id)
            raise error from None
        return self.detail(owner, session_id)
