"""Owner-only current profile, serialized CAS, sources locked before profile.

No Storage/engine/User/Contract writes; user fields are never inferred here.
"""
from fastapi.encoders import jsonable_encoder

from app.core.errors import DocumentError
from app.services.identity_parser import FIELDS


class IdentityProfile:
    def __init__(self, database):
        self.database = database

    @staticmethod
    def _row(conn, owner, *, lock=False):
        return conn.execute("SELECT * FROM public.user_identity_profiles WHERE owner_user_id=%s" +
                            (" FOR UPDATE" if lock else ""), (owner,)).fetchone()

    @staticmethod
    def _public(row):
        return jsonable_encoder({"owner_user_id": row["owner_user_id"], "source_session_id": row["source_session_id"],
            "source_document_id": row["source_document_id"], "revision": row["revision"],
            "confirmed_at": row["confirmed_at"], "updated_at": row["updated_at"],
            "fields": {key: row[key] for key in FIELDS}})

    @staticmethod
    def _missing(revision=0):
        return DocumentError("IDENTITY_PROFILE_NOT_FOUND", "Chưa có hồ sơ CCCD đang dùng.", 404,
                             current_revision=revision)

    @staticmethod
    def _conflict(revision):
        return DocumentError("REVISION_CONFLICT", "Hồ sơ đã thay đổi; tải lại trước khi xác nhận.", 409,
                             current_revision=revision)

    def get(self, owner):
        with self.database.connection(read_only=True) as conn:
            row = self._row(conn, owner)
            if row is None or row["status"] == "deleted":
                raise self._missing(row["revision"] if row else 0)
            return self._public(row)

    def _lock(self, conn, owner, new_source=None):
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"identity-profile:{owner}",))
        prior = self._row(conn, owner)
        sources = set()
        if prior:
            sources.add(("session" if prior["source_session_id"] else "document",
                         str(prior["source_session_id"] or prior["source_document_id"])))
        if new_source:
            sources.add((new_source[0], str(new_source[1])))
        for kind, identifier in sorted(sources):
            table = "document_identity_ocr_sessions" if kind == "session" else "user_files"
            # Table identifiers are constants, never JSON input.
            conn.execute(f"SELECT id FROM public.{table} WHERE id=%s AND owner_user_id=%s FOR UPDATE", (identifier, owner)).fetchone()
        current = self._row(conn, owner, lock=True)
        # A writer bypassing the documented owner lock must not make us change
        # sources while already holding the profile lock (lock order/deadlock).
        if (current["revision"] if current else 0) != (prior["revision"] if prior else 0):
            raise self._conflict(current["revision"] if current else 0)
        return current

    def confirm(self, owner, body):
        kind, source_id = ("session", body.source_session_id) if body.source_session_id else ("document", body.source_document_id)
        with self.database.connection() as conn:
            prior = self._lock(conn, owner, (kind, source_id))
            revision = prior["revision"] if prior else 0
            if body.expected_revision != revision:
                raise self._conflict(revision)
            if revision == 2147483647:
                raise DocumentError("REVISION_EXHAUSTED", "Hồ sơ đã đạt giới hạn revision.", 409, current_revision=revision)
            result_id = None
            if kind == "session":
                source = conn.execute("SELECT status FROM public.document_identity_ocr_sessions WHERE id=%s AND owner_user_id=%s",
                                      (source_id, owner)).fetchone()
                if source is None:
                    raise DocumentError("IDENTITY_SOURCE_NOT_FOUND", "Không tìm thấy nguồn CCCD của bạn.", 404)
                if source["status"] != "completed":
                    raise DocumentError("IDENTITY_SOURCE_NOT_READY", "Chỉ xác nhận phiên OCR đã hoàn tất.", 409)
            else:
                source = conn.execute("SELECT * FROM public.user_files WHERE id=%s AND owner_user_id=%s AND deleted_at IS NULL",
                                      (source_id, owner)).fetchone()
                if source is None:
                    raise DocumentError("IDENTITY_SOURCE_NOT_FOUND", "Không tìm thấy nguồn CCCD của bạn.", 404)
                if (source["purpose"] != "supporting_document" or source["document_type"] != "identity_card"
                        or source["media_type"] not in {"image/jpeg", "image/png", "application/pdf"}):
                    raise DocumentError("INVALID_IDENTITY_SOURCE", "Nguồn phải là giấy tờ CCCD đã khai báo.", 422)
                job = conn.execute("SELECT j.status,r.id AS result_id,r.source_sha256 FROM public.document_processing_jobs j "
                                   "LEFT JOIN public.document_extraction_results r ON r.job_id=j.id WHERE j.document_id=%s "
                                   "ORDER BY j.attempt DESC LIMIT 1", (source_id,)).fetchone()
                if (source["file_status"] != "available" or not job or job["status"] != "completed"
                        or not job["result_id"] or job["source_sha256"] != source["sha256"]):
                    raise DocumentError("IDENTITY_SOURCE_NOT_READY", "Nguồn CCCD chưa có kết quả xử lý hoàn tất.", 409)
                result_id = job["result_id"]
            values = [body.fields.model_dump()[key] for key in FIELDS]
            if prior:
                conn.execute("UPDATE public.user_identity_profiles SET source_session_id=%s,source_document_id=%s,source_result_id=%s,"
                             "full_name=%s,identity_number=%s,date_of_birth=%s,issue_date=%s,issue_place=%s,permanent_address=%s,"
                             "revision=revision+1,status='active',deleted_at=NULL,confirmed_at=now() WHERE owner_user_id=%s",
                             (body.source_session_id, body.source_document_id, result_id, *values, owner))
            else:
                conn.execute("INSERT INTO public.user_identity_profiles (source_session_id,source_document_id,source_result_id,"
                             "full_name,identity_number,date_of_birth,issue_date,issue_place,permanent_address,owner_user_id,confirmed_at) "
                             "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())",
                             (body.source_session_id, body.source_document_id, result_id, *values, owner))
            return self._public(self._row(conn, owner))

    def unlink(self, owner, expected_revision):
        with self.database.connection() as conn:
            prior = self._lock(conn, owner)
            if prior is None:
                raise self._missing()
            revision = prior["revision"]
            # Replay the last unlink or acknowledge an already-deleted revision.
            # A recreated active profile still rejects the old revision.
            if prior["status"] == "deleted" and expected_revision in {revision, revision - 1}:
                return
            if expected_revision != revision:
                raise self._conflict(revision)
            if revision == 2147483647:
                raise DocumentError("REVISION_EXHAUSTED", "Hồ sơ đã đạt giới hạn revision.", 409, current_revision=revision)
            conn.execute("UPDATE public.user_identity_profiles SET status='deleted',deleted_at=now(),revision=revision+1 "
                         "WHERE owner_user_id=%s", (owner,))
