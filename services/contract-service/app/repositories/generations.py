"""Durable jobs, bounded recovery and fencing using existing started_at/retry_count."""
import hashlib
import json

from psycopg.types.json import Jsonb

from app.core.errors import ContractError
from app.repositories.drafts import DraftRepository, missing, conflict
from app.repositories.catalog import pagination
from app.schemas.generations import validate_generation

PUBLIC = "id,draft_id,kind,format,status,error_code,warnings,created_at,completed_at"


def fingerprint(draft_id, kind, format, context):
    payload = {"draft_id":str(draft_id),"kind":kind,"format":format}
    if kind == "preview":
        payload.update(values=context["draft"]["field_values"],version=str(context["draft"]["template_version_id"]))
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


class GenerationRepository(DraftRepository):
    def context(self, conn, draft_id, owner, lock=False):
        draft = self.row(conn, draft_id, owner, lock=lock)
        revision = conn.execute("SELECT revision FROM public.contract_drafts WHERE id=%s", (draft_id,)).fetchone()["revision"]
        version = conn.execute("SELECT v.id,v.template_id,v.content,v.source_format,v.snapshot_storage_key,v.source_sha256,v.source_size_bytes,v.extraction_snapshot,t.kind FROM public.contract_template_versions v JOIN public.contract_templates t ON t.id=v.template_id WHERE v.id=%s AND v.status='published'", (draft["template_version_id"],)).fetchone()
        if not version:
            raise missing()
        return {"draft":draft,"revision":revision,"version":version,"fields":self.fields(conn,draft["template_version_id"])}

    def prior(self, conn, owner, key, kind, request_hash):
        row = conn.execute("SELECT " + PUBLIC + ",request_hash FROM public.contract_generation_jobs WHERE owner_user_id=%s AND kind=%s AND idempotency_key=%s", (owner,kind,key)).fetchone()
        if row:
            if row.pop("request_hash") != request_hash:
                raise ContractError("IDEMPOTENCY_CONFLICT", "Key đã được dùng cho yêu cầu khác.", 409)
            return row

    def admit_generation(self, draft_id, owner, key, kind, format):
        with self.transaction(read=True) as conn:
            context = self.context(conn,draft_id,owner)
            prior = self.prior(conn,owner,key,kind,fingerprint(draft_id,kind,format,context))
            if prior:
                return context, prior
            self.active_guard(conn,draft_id)
            validate_generation(context,kind,format)
            return context, None

    def enqueue(self, draft_id, owner, key, kind, format, expected_revision):
        with self.transaction() as conn:
            context = self.context(conn,draft_id,owner,lock=True)
            digest = fingerprint(draft_id,kind,format,context)
            prior = self.prior(conn,owner,key,kind,digest)
            if prior:
                return prior
            if context["revision"] != expected_revision:
                raise ContractError("DRAFT_CHANGED", "Nháp đã đổi; kiểm tra lại trước khi xuất.",409)
            self.active_guard(conn,draft_id)
            warnings = validate_generation(context,kind,format)
            documents = sorted(context["draft"]["document_ids"],key=str)
            return conn.execute("INSERT INTO public.contract_generation_jobs(draft_id,template_version_id,owner_user_id,draft_revision,input_snapshot,document_ids,kind,format,idempotency_key,request_hash,warnings) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING " + PUBLIC,
                (draft_id,context["draft"]["template_version_id"],owner,context["revision"],Jsonb(context["draft"]["field_values"]),documents,kind,format,key,digest,Jsonb(warnings))).fetchone()

    def get_job(self, conn, job_id, owner):
        row = conn.execute("SELECT " + ",".join("j."+part for part in PUBLIC.split(",")) + " FROM public.contract_generation_jobs j JOIN public.contract_drafts d ON d.id=j.draft_id AND d.owner_user_id=j.owner_user_id WHERE j.id=%s AND j.owner_user_id=%s AND d.deleted_at IS NULL", (job_id,owner)).fetchone()
        if row is None:
            raise missing()
        return row

    def get_generation(self, job_id, owner):
        with self.transaction(read=True) as conn:
            return self.get_job(conn,job_id,owner)

    def list_generations(self, draft_id, owner, page, size):
        with self.transaction(read=True) as conn:
            self.row(conn,draft_id,owner)
            total=conn.execute("SELECT count(*) AS total FROM public.contract_generation_jobs WHERE draft_id=%s AND owner_user_id=%s", (draft_id,owner)).fetchone()["total"]
            rows=conn.execute("SELECT " + PUBLIC + " FROM public.contract_generation_jobs WHERE draft_id=%s AND owner_user_id=%s ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s",(draft_id,owner,size,(page-1)*size)).fetchall()
            return pagination(rows,total,page,size)

    def result(self, job_id, owner):
        with self.transaction(read=True) as conn:
            job=self.get_job(conn,job_id,owner)
            if job["status"] != "completed":
                raise conflict()
            result=conn.execute("SELECT storage_key,sha256,size_bytes,format FROM public.contract_generation_results WHERE job_id=%s AND owner_user_id=%s",(job_id,owner)).fetchone()
            if not result:
                raise ContractError("RESULT_UNAVAILABLE","Kết quả chưa sẵn sàng.")
            return result

    def claim(self):
        with self.transaction() as conn:
            job=conn.execute("SELECT * FROM public.contract_generation_jobs WHERE status='queued' OR (status='processing' AND started_at < now()-interval '180 seconds') ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
            if not job:
                return None
            if job["retry_count"] >= 3:
                conn.execute("UPDATE public.contract_generation_jobs SET status='failed',error_code='WORKER_RETRY_EXHAUSTED',completed_at=now() WHERE id=%s",(job["id"],))
                return {"exhausted":True}
            claimed=conn.execute("UPDATE public.contract_generation_jobs SET status='processing',started_at=now(),retry_count=retry_count+1 WHERE id=%s RETURNING *",(job["id"],)).fetchone()
            version=conn.execute("SELECT v.id,v.template_id,v.content,v.source_format,v.snapshot_storage_key,v.source_sha256,v.source_size_bytes,v.extraction_snapshot,t.kind FROM public.contract_template_versions v JOIN public.contract_templates t ON t.id=v.template_id WHERE v.id=%s",(job["template_version_id"],)).fetchone()
            claimed["content"]=version["content"]
            if version["snapshot_storage_key"]:
                claimed["source"]=version
                claimed["fields"]=conn.execute("SELECT name,label,type,required,identity_key,placements FROM public.contract_template_fields WHERE version_id=%s ORDER BY ordinal",(job["template_version_id"],)).fetchall()
            return claimed

    def finish(self, job, result=None, error=None):
        with self.transaction() as conn:
            current=conn.execute("SELECT status,retry_count,started_at,started_at > now()-interval '180 seconds' AS lease_valid FROM public.contract_generation_jobs WHERE id=%s FOR UPDATE",(job["id"],)).fetchone()
            if current["status"] != "processing" or current["retry_count"] != job["retry_count"] or current["started_at"] != job["started_at"] or not current["lease_valid"]:
                return False
            if error:
                conn.execute("UPDATE public.contract_generation_jobs SET status='failed',error_code=%s,completed_at=now() WHERE id=%s",(error,job["id"]))
            else:
                conn.execute("INSERT INTO public.contract_generation_results(job_id,owner_user_id,format,storage_key,sha256,size_bytes) VALUES(%s,%s,%s,%s,%s,%s)",(job["id"],job["owner_user_id"],job["format"],result["storage_key"],result["sha256"],result["size_bytes"]))
                conn.execute("UPDATE public.contract_generation_jobs SET status='completed',completed_at=now() WHERE id=%s",(job["id"],))
                # Durable outbox only. Delivery/Notification is C9, not claimed here.
                if job["kind"] == "final":
                    conn.execute("INSERT INTO public.contract_notification_outbox(job_id) VALUES(%s)",(job["id"],))
            return True

    def can_discard(self,job_id,key):
        # Serialize with any in-flight completion before deciding to remove an object.
        with self.transaction() as conn:
            conn.execute("SELECT id FROM public.contract_generation_jobs WHERE id=%s FOR UPDATE",(job_id,)).fetchone()
            return conn.execute("SELECT 1 FROM public.contract_generation_results WHERE storage_key=%s",(key,)).fetchone() is None
