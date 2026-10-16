"""Parameterized C4 PostgreSQL transactions. Never query another service database."""
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from app.core.config import DatabaseSettings
from app.core.errors import ContractError
from app.schemas.catalog import VersionInput

TEMPLATE = "id,name,description,is_visible,created_at"
VERSION = "id,template_id,version,status,content,created_at"


def missing():
    return ContractError("CONTRACT_TEMPLATE_NOT_FOUND", "Không tìm thấy mẫu hoặc phiên bản.", 404)


def conflict(message="Mẫu hoặc phiên bản đang được sử dụng hoặc không thể thay đổi."):
    return ContractError("CONTRACT_TEMPLATE_CONFLICT", message, 409)


def pagination(items, total, page, size):
    return {"items": items, "pagination": {"page": page, "page_size": size,
        "total": total, "total_pages": (total + size - 1) // size}}


class CatalogRepository:
    def __init__(self, connect=None):
        self._connect = connect

    @contextmanager
    def transaction(self, *, read=False):
        try:
            if self._connect:
                connection = self._connect()
            else:
                uri = DatabaseSettings.from_env().database_url.replace("postgresql+psycopg://", "postgresql://", 1)
                connection = psycopg.connect(uri, connect_timeout=5, prepare_threshold=None,
                    row_factory=dict_row, options="-c statement_timeout=5000 -c lock_timeout=2000")
            with connection as conn:
                if read:
                    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                yield conn
        except (psycopg.IntegrityError, psycopg.errors.DeadlockDetected):
            raise conflict() from None
        except psycopg.Error:
            raise ContractError("DATABASE_UNAVAILABLE", "Không thể truy cập database Contract.") from None

    def template(self, conn, template_id, *, lock=False):
        row = conn.execute("SELECT " + TEMPLATE + " FROM public.contract_templates WHERE id=%s "
            "AND kind='system' AND deleted_at IS NULL" + (" FOR UPDATE" if lock else ""), (template_id,)).fetchone()
        if row is None:
            raise missing()
        return row

    def fields(self, conn, version_id):
        return conn.execute("SELECT name,label,type,required,identity_key FROM public.contract_template_fields "
            "WHERE version_id=%s ORDER BY ordinal", (version_id,)).fetchall()

    def version(self, conn, version_id, *, lock=False):
        row = conn.execute("SELECT " + VERSION + ",extraction_snapshot FROM public.contract_template_versions WHERE id=%s" +
            (" FOR UPDATE" if lock else ""), (version_id,)).fetchone()
        if row is None:
            raise missing()
        row["fields"] = self.fields(conn, version_id)
        self.form_options(row)
        return row

    def form_options(self,row):
        extraction=row.pop("extraction_snapshot",None) or {}
        manifest=extraction.get("catalog_import") if isinstance(extraction,dict) else None
        choices=manifest.get("choices",{}) if isinstance(manifest,dict) else {}
        if not isinstance(choices,dict):
            choices={}
        names={field["name"] for field in row["fields"]}
        # Only short enumerated form choices leave the repository, never snapshot keys.
        row["form_options"]={key:values for key,values in choices.items() if key in names and isinstance(values,list)
            and 1<=len(values)<=20 and all(isinstance(v,str) and 1<=len(v)<=200 for v in values)}

    def locked_version(self, conn, version_id):
        # Consistent lock order: parent template, then version (including publication).
        parent = conn.execute("SELECT template_id FROM public.contract_template_versions WHERE id=%s", (version_id,)).fetchone()
        if not parent:
            raise missing()
        self.template(conn, parent["template_id"], lock=True)
        return self.version(conn, version_id, lock=True)

    def list_templates(self, admin, hidden, page, size):
        condition = "kind='system' AND deleted_at IS NULL"
        if not (admin and hidden):
            condition += " AND is_visible AND EXISTS (SELECT 1 FROM public.contract_template_versions v WHERE v.template_id=contract_templates.id AND v.status='published')"
        with self.transaction(read=True) as conn:
            total = conn.execute("SELECT count(*) AS total FROM public.contract_templates WHERE " + condition).fetchone()["total"]
            rows = conn.execute("SELECT " + TEMPLATE + " FROM public.contract_templates WHERE " + condition +
                " ORDER BY created_at DESC,id LIMIT %s OFFSET %s", (size, (page-1)*size)).fetchall()
            return pagination(rows, total, page, size)

    def get_template(self, template_id, admin):
        with self.transaction(read=True) as conn:
            row = self.template(conn, template_id)
            if not admin and (not row["is_visible"] or not conn.execute(
                "SELECT 1 FROM public.contract_template_versions WHERE template_id=%s AND status='published' LIMIT 1", (template_id,)).fetchone()):
                raise missing()
            return row

    def create_template(self, body):
        with self.transaction() as conn:
            return conn.execute("INSERT INTO public.contract_templates(kind,name,description) VALUES('system',%s,%s) RETURNING " + TEMPLATE,
                (body.name, body.description)).fetchone()

    def update_template(self, template_id, body):
        changes = body.model_dump(exclude_unset=True)
        with self.transaction() as conn:
            self.template(conn, template_id, lock=True)
            if changes.get("is_visible") and not conn.execute(
                "SELECT 1 FROM public.contract_template_versions WHERE template_id=%s AND status='published' LIMIT 1", (template_id,)).fetchone():
                raise conflict("Cần xuất bản một phiên bản trước khi hiện mẫu.")
            # Keys come exclusively from the validated model, never from raw JSON.
            assignments = ",".join(key + "=%s" for key in changes)
            return conn.execute("UPDATE public.contract_templates SET " + assignments + " WHERE id=%s RETURNING " + TEMPLATE,
                (*changes.values(), template_id)).fetchone()

    def delete_template(self, template_id):
        with self.transaction() as conn:
            self.template(conn, template_id, lock=True)
            if conn.execute("SELECT 1 FROM public.contract_drafts d JOIN public.contract_template_versions v ON v.id=d.template_version_id WHERE v.template_id=%s LIMIT 1", (template_id,)).fetchone():
                raise conflict("Mẫu đã có nháp tham chiếu; hãy ẩn mẫu thay vì xóa.")
            conn.execute("UPDATE public.contract_templates SET deleted_at=now(),is_visible=false WHERE id=%s", (template_id,))

    def list_versions(self, template_id, admin, page, size):
        with self.transaction(read=True) as conn:
            template = self.template(conn, template_id)
            if not admin and not template["is_visible"]:
                raise missing()
            condition = "template_id=%s" + ("" if admin else " AND status='published'")
            total = conn.execute("SELECT count(*) AS total FROM public.contract_template_versions WHERE " + condition, (template_id,)).fetchone()["total"]
            if not admin and total == 0:
                raise missing()
            rows = conn.execute("SELECT " + VERSION + ",extraction_snapshot FROM public.contract_template_versions WHERE " + condition +
                " ORDER BY version DESC LIMIT %s OFFSET %s", (template_id, size, (page-1)*size)).fetchall()
            for row in rows:
                row["fields"] = self.fields(conn, row["id"])
                self.form_options(row)
            return pagination(rows, total, page, size)

    def get_version(self, version_id, admin, owner):
        with self.transaction(read=True) as conn:
            row = conn.execute("SELECT v.id FROM public.contract_template_versions v JOIN public.contract_templates t ON t.id=v.template_id "
                "WHERE v.id=%s AND t.kind='system' AND ((%s AND t.deleted_at IS NULL) OR (v.status='published' AND "
                "((t.is_visible AND t.deleted_at IS NULL) OR EXISTS (SELECT 1 FROM public.contract_drafts d WHERE d.template_version_id=v.id AND d.owner_user_id=%s AND d.deleted_at IS NULL))))",
                (version_id, admin, owner)).fetchone()
            if not row:
                raise missing()
            return self.version(conn, version_id)

    def put_fields(self, conn, version_id, fields):
        for index, field in enumerate(fields):
            conn.execute("INSERT INTO public.contract_template_fields(version_id,name,label,type,required,identity_key,ordinal) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                (version_id, field.name, field.label, field.type, field.required, field.identity_key, index))

    def create_version(self, template_id, body):
        with self.transaction() as conn:
            self.template(conn, template_id, lock=True)
            number = conn.execute("UPDATE public.contract_templates SET last_version=greatest(last_version,coalesce((SELECT max(version) FROM public.contract_template_versions WHERE template_id=%s),0))+1 WHERE id=%s RETURNING last_version", (template_id, template_id)).fetchone()["last_version"]
            row = conn.execute("INSERT INTO public.contract_template_versions(template_id,version,content) VALUES(%s,%s,%s) RETURNING " + VERSION, (template_id, number, body.content)).fetchone()
            self.put_fields(conn, row["id"], body.fields)
            return self.version(conn, row["id"])

    def update_version(self, version_id, body):
        with self.transaction() as conn:
            row = self.locked_version(conn, version_id)
            if row["status"] != "draft":
                raise conflict("Bản đã xuất bản không được sửa; hãy tạo phiên bản mới.")
            if conn.execute("SELECT snapshot_storage_key FROM public.contract_template_versions WHERE id=%s",(version_id,)).fetchone()["snapshot_storage_key"] is not None:
                raise conflict("Mẫu có snapshot phải chuẩn bị lại nguồn/mapping; không sửa bằng API văn bản.")
            conn.execute("UPDATE public.contract_template_versions SET content=%s WHERE id=%s", (body.content, version_id))
            conn.execute("DELETE FROM public.contract_template_fields WHERE version_id=%s", (version_id,))
            self.put_fields(conn, version_id, body.fields)
            return self.version(conn, version_id)

    def publish_version(self, version_id):
        from pydantic import ValidationError
        with self.transaction() as conn:
            row = self.locked_version(conn, version_id)
            source=conn.execute("SELECT v.*,t.kind FROM public.contract_template_versions v JOIN public.contract_templates t ON t.id=v.template_id WHERE v.id=%s",(version_id,)).fetchone()
            if source["snapshot_storage_key"] is not None:
                from app.services.catalog_source import validate_catalog
                validate_catalog(source,{},final=False)
            if row["status"] == "published":
                return row
            try:
                VersionInput(content=row["content"], fields=row["fields"])
            except ValidationError:
                raise ContractError("VALIDATION_ERROR", "Nội dung hoặc trường điền không hợp lệ.", 422) from None
            conn.execute("UPDATE public.contract_template_versions SET status='published',published_at=now() WHERE id=%s", (version_id,))
            return self.version(conn, version_id)

    def delete_version(self, version_id):
        with self.transaction() as conn:
            row = self.locked_version(conn, version_id)
            if row["status"] != "draft":
                raise conflict("Bản đã xuất bản không được xóa.")
            if conn.execute("SELECT 1 FROM public.contract_drafts WHERE template_version_id=%s LIMIT 1", (version_id,)).fetchone():
                raise conflict()
            conn.execute("DELETE FROM public.contract_template_fields WHERE version_id=%s", (version_id,))
            conn.execute("DELETE FROM public.contract_template_versions WHERE id=%s", (version_id,))
