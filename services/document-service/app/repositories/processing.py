from fastapi.encoders import jsonable_encoder
from app.core.errors import DocumentError


class Processing:
    def __init__(self, database):
        self.database = database

    def _latest(self, conn, owner, document):
        row = conn.execute("SELECT f.file_status,f.sha256,j.* FROM user_files f JOIN LATERAL "
            "(SELECT * FROM document_processing_jobs WHERE document_id=f.id ORDER BY attempt DESC LIMIT 1) j ON true "
            "WHERE f.id=%s AND f.owner_user_id=%s AND f.deleted_at IS NULL", (document, owner)).fetchone()
        if row is None:
            raise DocumentError("DOCUMENT_NOT_FOUND", "Không tìm thấy tài liệu.", 404)
        return row

    def status(self, owner, document):
        with self.database.connection(read_only=True) as conn:
            row = self._latest(conn, owner, document)
            return jsonable_encoder({"document_id": document, "job_id": row["id"], **{
                key: row[key] for key in ("attempt", "status", "file_status", "error_code", "created_at",
                                          "started_at", "completed_at", "updated_at")}})

    def extraction(self, owner, document):
        with self.database.connection(read_only=True) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            job = self._latest(conn, owner, document)
            if job["file_status"] == "rejected":
                raise DocumentError("FILE_REJECTED", "File không đạt kiểm tra an toàn.", 409)
            if job["status"] == "failed":
                raise DocumentError("EXTRACTION_FAILED", "Không thể trích nội dung; xem trạng thái xử lý.", 409)
            if job["file_status"] != "available" or job["status"] != "completed":
                raise DocumentError("EXTRACTION_NOT_READY", "Tài liệu chưa xử lý xong.", 409)
            row = conn.execute("SELECT job_id,schema_version,source_sha256,source_kind,text,pages,extracted_fields,"
                "warnings,engine_version,created_at FROM document_extraction_results WHERE job_id=%s", (job["id"],)).fetchone()
            if not row or row["source_sha256"] != job["sha256"]:
                raise DocumentError("EXTRACTION_BAD_RESPONSE", "Kết quả không khớp nguồn tài liệu.", 502)
            return jsonable_encoder({"document_id": document, **row})
