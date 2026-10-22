"""Opt-in worker. Session advisory lock + started_at fencing; no long SQL TX over IO.

Requires Direct or Session pooler (NOT transaction pooler). No User/Contract writes.
"""
import argparse
import hashlib
import json
import logging
import os
import time

from common_auth.events import publish_event_sync
from psycopg.types.json import Jsonb
from pydantic import ValidationError
from app.core.config import Settings, require_session_database
from app.core.errors import DocumentError
from app.repositories.database import Database
from app.schemas.extraction import ExtractionResult
from app.services.document_processor import DocumentProcessor
from app.services.malware import MalwareScanner
from app.services.storage import Storage

RETRYABLE = {"SCAN_UNAVAILABLE", "STORAGE_UNAVAILABLE", "STORAGE_TIMEOUT", "STORAGE_BAD_RESPONSE", "STORAGE_CONFIGURATION_ERROR"}
REJECT = {"MALWARE_DETECTED", "UNSAFE_FILE", "INVALID_FILE", "ENCRYPTED_PDF", "SIGNED_PDF", "UNSUPPORTED_FILE_TYPE", "TOO_MANY_PAGES"}


def notify(event_type, row, error_code=None):
    # Chỉ id, không gửi nội dung tài liệu. Lỗi broker không được làm hỏng job đã commit.
    # shortcut: phát sau commit, worker chết đúng lúc đó thì mất thông báo; cần chắc chắn thì phát từ document_notification_outbox.
    try:
        publish_event_sync(event_type,{"document_id":str(row["id"]),"user_id":str(row["owner_user_id"]),"error_code":error_code})
    except Exception:
        logging.getLogger("document-worker").warning("Event %s not published.", event_type)


class Worker:
    def __init__(self, database, storage, scanner, processor, max_retries=3):
        if not 0 <= max_retries <= 5: raise ValueError("Invalid retry budget")
        self.database,self.storage,self.scanner,self.processor=database,storage,scanner,processor
        self.max_retries=max_retries

    def _claim(self, conn, document_id=None):
        target=" AND f.id=%s" if document_id is not None else ""
        candidates=conn.execute("SELECT f.id FROM user_files f JOIN document_processing_jobs j ON j.document_id=f.id "
            "WHERE f.deleted_at IS NULL AND (j.status='processing' OR (j.status='queued' AND (j.retry_count=0 OR "
            "j.updated_at < now()-make_interval(secs=>least(300,5*power(2,j.retry_count)::int))))) "
            + target + " ORDER BY j.created_at,j.id LIMIT 100", (document_id,) if document_id is not None else ()).fetchall()
        conn.commit()
        for candidate in candidates:
            row=conn.execute("SELECT * FROM user_files WHERE id=%s AND deleted_at IS NULL FOR UPDATE SKIP LOCKED",
                             (candidate["id"],)).fetchone()
            if row is None:
                conn.rollback(); continue
            job=conn.execute("SELECT * FROM document_processing_jobs WHERE document_id=%s ORDER BY attempt DESC LIMIT 1 FOR UPDATE",
                             (row["id"],)).fetchone()
            if job["status"] not in {"queued","processing"}:
                conn.rollback(); continue
            lock=f"document-worker:{row['id']}"
            if not conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS locked",(lock,)).fetchone()["locked"]:
                conn.rollback(); continue
            try:
                recovered=job["status"]=="processing"
                if recovered and job["retry_count"]>=self.max_retries:
                    conn.execute("UPDATE document_processing_jobs SET status='failed',error_code='WORKER_RECOVERY_EXHAUSTED',"
                                 "completed_at=now() WHERE id=%s",(job["id"],))
                    conn.commit(); conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))",(lock,));conn.commit()
                    continue
                started=conn.execute("UPDATE document_processing_jobs SET status='processing',started_at=clock_timestamp(),"
                    "retry_count=retry_count+%s WHERE id=%s RETURNING started_at",(int(recovered),job["id"])).fetchone()["started_at"]
                conn.commit()
                return row,{**job,"started_at":started,"retry_count":job["retry_count"]+int(recovered)},lock
            except Exception:
                conn.rollback();conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))",(lock,));conn.commit();raise
        return None

    @staticmethod
    def _fence(conn, row, job):
        current=conn.execute("SELECT * FROM user_files WHERE id=%s AND deleted_at IS NULL FOR UPDATE",(row["id"],)).fetchone()
        active=conn.execute("SELECT 1 FROM document_processing_jobs WHERE id=%s AND status='processing' AND started_at=%s FOR UPDATE",
                            (job["id"],job["started_at"])).fetchone()
        if not current or not active:
            raise DocumentError("WORKER_STALE", "Job không còn thuộc worker này.",409)
        return current

    def _process(self, conn, row, job):
        prefix=f"{row['owner_user_id']}/{row['id']}/"
        if not row["storage_key"].startswith(("quarantine/"+prefix,"safe/"+prefix)):
            raise DocumentError("STORAGE_CONFIGURATION_ERROR","Nguồn không khớp owner/document.")
        if row["file_status"]=="rejected":
            raise DocumentError("UNSAFE_FILE","Nguồn đã bị từ chối.",422)
        if row["file_status"]=="available":
            content=self.storage.read_verified(row["storage_key"],row["size_bytes"],row["sha256"],row["media_type"])
        else:
            content=self.storage.read_verified(row["storage_key"],row["uploaded_size_bytes"],row["uploaded_sha256"],row["media_type"])
            self.scanner.scan(content)
            content=self.processor.run("sanitize",content,row)
            self.scanner.scan(content)
            digest=hashlib.sha256(content).hexdigest()
            key=f"safe/{prefix}{job['id']}/{digest}/source"
            self.storage.put_safe(key,content,row["media_type"])
            self._fence(conn,row,job)
            conn.execute("UPDATE user_files SET file_status='available',size_bytes=%s,sha256=%s,storage_key=%s,error_code=NULL WHERE id=%s",
                         (len(content),digest,key,row["id"]))
            conn.commit()
            row={**row,"file_status":"available","size_bytes":len(content),"sha256":digest,"storage_key":key}
        raw=self.processor.run("extract",content,row)
        try:
            result=ExtractionResult.model_validate(raw).model_dump()
        except ValidationError:
            raise DocumentError("EXTRACTION_FAILED","Kết quả trích xuất không hợp lệ.",422) from None
        if len(json.dumps(result,ensure_ascii=False).encode())>16*1024*1024-8192:
            raise DocumentError("EXTRACTION_TOO_LARGE","Kết quả vượt giới hạn.",422)
        self._fence(conn,row,job)
        conn.execute("INSERT INTO document_extraction_results (job_id,source_sha256,schema_version,source_kind,text,pages,"
            "extracted_fields,warnings,engine_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (job["id"],row["sha256"],result["schema_version"],result["source_kind"],result["text"],Jsonb(result["pages"]),
             Jsonb(result["extracted_fields"]),Jsonb(result["warnings"]),result["engine_version"]))
        conn.execute("UPDATE document_processing_jobs SET status='completed',completed_at=now() WHERE id=%s",(job["id"],))
        conn.commit()

    def _failure(self, conn, row, job, code):
        conn.rollback()
        current=self._fence(conn,row,job)
        if code in RETRYABLE and job["retry_count"]<self.max_retries:
            conn.execute("UPDATE document_processing_jobs SET status='queued',retry_count=retry_count+1 WHERE id=%s",(job["id"],))
        else:
            if code in REJECT and current["file_status"]=="pending_check":
                conn.execute("UPDATE user_files SET file_status='rejected',error_code=%s WHERE id=%s",(code,row["id"]))
            conn.execute("UPDATE document_processing_jobs SET status='failed',completed_at=now(),error_code=%s WHERE id=%s",(code,job["id"]))
            conn.commit()
            notify("document.processing.failed",row,code)
            return
        conn.commit()

    def run_once(self, document_id=None):
        with self.database.connection() as conn:
            claim=self._claim(conn, document_id)
            if claim is None: return False
            row,job,lock=claim
            try:
                try:
                    self._process(conn,row,job)
                    notify("document.ocr.completed",row)
                except DocumentError as exc:
                    if exc.code!="WORKER_STALE": self._failure(conn,row,job,exc.code)
                except Exception:
                    # Never log exception strings, SQL parameters or document text.
                    self._failure(conn,row,job,"PROCESSING_FAILED")
            finally:
                conn.rollback()
                conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))",(lock,));conn.commit()
        return True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once",action="store_true")
    args=parser.parse_args()
    try:
        require_session_database(Settings.from_env())
        worker=Worker(Database(Settings.from_env()),Storage(Settings.from_env()),
            MalwareScanner(os.environ.get("DOCUMENT_CLAMAV_HOST","document-antivirus"),int(os.environ.get("DOCUMENT_CLAMAV_PORT","3310"))),
            DocumentProcessor(os.environ.get("DOCUMENT_PADDLE_MODEL_DIR","/opt/document-ocr"),int(os.environ.get("DOCUMENT_PROCESSING_TIMEOUT_SECONDS","240"))))
    except Exception:
        print("Worker config unavailable; no credentials printed."); return 1
    while True:
        try: worked=worker.run_once()
        except Exception:
            logging.getLogger("document-worker").warning("Worker cycle failed; content/credentials omitted."); worked=False
        if args.once: return 0 if worked else 2
        if not worked: time.sleep(2)


if __name__=="__main__": raise SystemExit(main())
