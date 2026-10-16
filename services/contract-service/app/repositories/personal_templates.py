"""C8 owner-only, immutable sources, request-key serialization and mapping CAS."""
import hashlib
import json

from psycopg.types.json import Jsonb

from app.core.errors import ContractError
from app.repositories.catalog import CatalogRepository


def request_hash(body):
    return hashlib.sha256(json.dumps({"document_id":str(body.document_id),"name":body.name},sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()


def missing():
    return ContractError("CONTRACT_TEMPLATE_NOT_FOUND","Không tìm thấy mẫu riêng.",404)


def locked():
    return ContractError("REVISION_CONFLICT","Mapping đã thay đổi hoặc xác nhận; tải lại mẫu.",409)


class PersonalRepository(CatalogRepository):
    def key_lock(self,conn,owner,key):
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(str(owner)+":"+str(key),))

    def prior_row(self,conn,owner,key,digest):
        row=conn.execute("SELECT a.id,a.request_hash,t.deleted_at FROM public.contract_analysis_jobs a JOIN public.contract_templates t ON t.id=a.template_id WHERE a.owner_user_id=%s AND a.idempotency_key=%s",(owner,key)).fetchone()
        if row:
            if row["request_hash"]!=digest:
                raise ContractError("IDEMPOTENCY_CONFLICT","Key đã dùng cho yêu cầu khác.",409)
            if row["deleted_at"] is not None:
                raise ContractError("CONTRACT_TEMPLATE_GONE","Mẫu đã xóa; dùng key mới để nhập lại.",410)
            return self.analysis_row(conn,row["id"],owner)

    def prior(self,owner,key,body):
        with self.transaction(read=True) as conn:
            return self.prior_row(conn,owner,key,request_hash(body))

    def source_row(self,conn,template_id,owner,lock=False):
        row=conn.execute("SELECT t.id,t.name,v.id AS template_version_id,v.status AS version_status,v.source_format,v.mapping_revision,v.warnings,v.snapshot_storage_key,v.source_sha256,v.source_size_bytes,v.extraction_snapshot "
            "FROM public.contract_templates t JOIN public.contract_template_versions v ON v.template_id=t.id AND v.version=1 "
            "WHERE t.id=%s AND t.kind='personal' AND t.owner_user_id=%s AND t.deleted_at IS NULL"+(" FOR UPDATE OF t,v" if lock else ""),(template_id,owner)).fetchone()
        if row is None:
            raise missing()
        row["fields"]=conn.execute("SELECT name,label,type,required,identity_key,confidence,placements FROM public.contract_template_fields WHERE version_id=%s ORDER BY ordinal",(row["template_version_id"],)).fetchall()
        return row

    def public(self,row):
        return {k:row[k] for k in ("id","template_version_id","name","source_format","version_status","mapping_revision","fields","warnings")}

    def get(self,template_id,owner):
        with self.transaction(read=True) as conn:
            return self.public(self.source_row(conn,template_id,owner))

    def source(self,template_id,owner):
        with self.transaction(read=True) as conn:
            return self.source_row(conn,template_id,owner)

    def analysis_row(self,conn,analysis_id,owner):
        row=conn.execute("SELECT a.id,a.template_id,a.template_version_id,a.status,a.error_code FROM public.contract_analysis_jobs a JOIN public.contract_templates t ON t.id=a.template_id WHERE a.id=%s AND a.owner_user_id=%s AND t.owner_user_id=%s AND t.deleted_at IS NULL",(analysis_id,owner,owner)).fetchone()
        if row is None:
            raise missing()
        row["template"]=self.public(self.source_row(conn,row["template_id"],owner))
        return row

    def analysis(self,analysis_id,owner):
        with self.transaction(read=True) as conn:
            return self.analysis_row(conn,analysis_id,owner)

    def list(self,owner,page,size):
        with self.transaction(read=True) as conn:
            total=conn.execute("SELECT count(*) AS n FROM public.contract_templates WHERE kind='personal' AND owner_user_id=%s AND deleted_at IS NULL",(owner,)).fetchone()["n"]
            ids=conn.execute("SELECT id FROM public.contract_templates WHERE kind='personal' AND owner_user_id=%s AND deleted_at IS NULL ORDER BY created_at DESC,id LIMIT %s OFFSET %s",(owner,size,(page-1)*size)).fetchall()
            return {"items":[self.public(self.source_row(conn,r["id"],owner)) for r in ids],"total":total,"page":page,"page_size":size}

    def put_fields(self,conn,version,fields):
        for index,f in enumerate(fields):
            conn.execute("INSERT INTO public.contract_template_fields(version_id,name,label,type,required,identity_key,ordinal,confidence,placements) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (version,f["name"],f["label"],f["type"],f["required"],f.get("identity_key"),index,f.get("confidence"),Jsonb(f["placements"])))

    def create(self,owner,key,body,source,source_key,preview,manifest):
        with self.transaction() as conn:
            self.key_lock(conn,owner,key)
            old=self.prior_row(conn,owner,key,request_hash(body))
            if old:
                return old
            template=conn.execute("INSERT INTO public.contract_templates(kind,owner_user_id,name,last_version) VALUES('personal',%s,%s,1) RETURNING id",(owner,body.name)).fetchone()["id"]
            extraction={"document_extraction":source.extraction,"contract_import":{"schema_version":1,"preview":preview,"manifest":manifest}}
            version=conn.execute("INSERT INTO public.contract_template_versions(template_id,version,content,source_format,snapshot_storage_key,source_sha256,source_size_bytes,source_document_id,source_extraction_id,extraction_snapshot,warnings) VALUES(%s,1,'Personal source snapshot',%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (template,source.format,source_key,source.sha256,len(source.content),source.document_id,source.job_id,Jsonb(extraction),Jsonb(manifest["warnings"]))).fetchone()["id"]
            self.put_fields(conn,version,manifest["fields"])
            analysis=conn.execute("INSERT INTO public.contract_analysis_jobs(template_id,template_version_id,owner_user_id,idempotency_key,request_hash) VALUES(%s,%s,%s,%s,%s) RETURNING id",(template,version,owner,key,request_hash(body))).fetchone()["id"]
            # Preparation happened synchronously before persistence; all transitions remain legal/atomic.
            conn.execute("UPDATE public.contract_analysis_jobs SET status='processing' WHERE id=%s",(analysis,))
            conn.execute("UPDATE public.contract_analysis_jobs SET status='needs_review' WHERE id=%s",(analysis,))
            return self.analysis_row(conn,analysis,owner)

    def can_discard(self,owner,key,object_key):
        with self.transaction() as conn:
            self.key_lock(conn,owner,key)
            return conn.execute("SELECT 1 FROM public.contract_template_versions WHERE snapshot_storage_key=%s OR extraction_snapshot->'contract_import'->'preview'->>'key'=%s",(object_key,object_key)).fetchone() is None

    def writable(self,conn,template_id,owner,revision):
        row=self.source_row(conn,template_id,owner,True)
        analysis=conn.execute("SELECT id,status FROM public.contract_analysis_jobs WHERE template_version_id=%s AND owner_user_id=%s FOR UPDATE",(row["template_version_id"],owner)).fetchone()
        if row["version_status"]!="draft" or row["mapping_revision"]!=revision or not analysis or analysis["status"]!="needs_review":
            raise locked()
        return row,analysis

    def mapping(self,template_id,owner,body):
        with self.transaction() as conn:
            row,_=self.writable(conn,template_id,owner,body.expected_revision)
            conn.execute("DELETE FROM public.contract_template_fields WHERE version_id=%s",(row["template_version_id"],))
            self.put_fields(conn,row["template_version_id"],[f.model_dump() for f in body.fields])
            conn.execute("UPDATE public.contract_template_versions SET mapping_revision=mapping_revision+1 WHERE id=%s",(row["template_version_id"],))
            return self.public(self.source_row(conn,template_id,owner))

    def confirm(self,template_id,owner,revision):
        with self.transaction() as conn:
            row,analysis=self.writable(conn,template_id,owner,revision)
            if not row["fields"]:
                raise ContractError("INVALID_MAPPING","Cần chọn ít nhất một trường điền.",422)
            conn.execute("UPDATE public.contract_template_versions SET status='published',published_at=now() WHERE id=%s",(row["template_version_id"],))
            conn.execute("UPDATE public.contract_analysis_jobs SET status='confirmed' WHERE id=%s",(analysis["id"],))
            return self.public(self.source_row(conn,template_id,owner))

    def delete(self,template_id,owner):
        with self.transaction() as conn:
            row=self.source_row(conn,template_id,owner,True)
            if conn.execute("SELECT 1 FROM public.contract_analysis_jobs WHERE template_version_id=%s AND status IN ('queued','processing')",(row["template_version_id"],)).fetchone() or conn.execute("SELECT 1 FROM public.contract_generation_jobs WHERE template_version_id=%s AND status IN ('queued','processing')",(row["template_version_id"],)).fetchone():
                raise locked()
            conn.execute("UPDATE public.contract_templates SET deleted_at=now() WHERE id=%s AND owner_user_id=%s",(template_id,owner))
