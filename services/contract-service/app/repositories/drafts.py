"""Owner-filtered C5 drafts; all checks/links/revision changes commit together."""
from contextlib import contextmanager

from psycopg.types.json import Jsonb

from app.core.errors import ContractError
from app.repositories.catalog import CatalogRepository, pagination
from app.schemas.drafts import validate_values

DRAFT = "id,owner_user_id,template_version_id,procedure_id,title,field_values,created_at,updated_at"
ADMISSIBLE = "((t.kind='system' AND t.is_visible) OR (t.kind='personal' AND t.owner_user_id=%s AND EXISTS (SELECT 1 FROM public.contract_analysis_jobs a WHERE a.template_version_id=v.id AND a.owner_user_id=t.owner_user_id AND a.status='confirmed')))"


def missing():
    return ContractError("CONTRACT_NOT_FOUND", "Không tìm thấy nháp hoặc mẫu được chọn.", 404)


def conflict():
    return ContractError("CONTRACT_DRAFT_CONFLICT", "Nháp đang sinh file hoặc có thay đổi xung đột; vui lòng thử lại.", 409)


class DraftRepository(CatalogRepository):
    @contextmanager
    def transaction(self, *, read=False):
        try:
            with super().transaction(read=read) as conn:
                yield conn
        except ContractError as exc:
            if exc.code == "CONTRACT_TEMPLATE_CONFLICT":
                raise conflict() from None
            raise

    def row(self, conn, draft_id, owner, *, lock=False):
        row = conn.execute("SELECT " + DRAFT + " FROM public.contract_drafts WHERE id=%s AND owner_user_id=%s AND deleted_at IS NULL" +
            (" FOR UPDATE" if lock else ""), (draft_id, owner)).fetchone()
        if row is None:
            raise missing()
        row["document_ids"] = [item["document_id"] for item in conn.execute(
            "SELECT document_id FROM public.contract_draft_documents WHERE draft_id=%s AND owner_user_id=%s ORDER BY document_id", (draft_id, owner)).fetchall()]
        return row

    def template_for_create(self, conn, version_id, owner, *, lock=False):
        # Read parent identity first, lock template then version as in C4.
        parent = conn.execute("SELECT v.template_id FROM public.contract_template_versions v JOIN public.contract_templates t ON t.id=v.template_id "
            "WHERE v.id=%s AND v.status='published' AND t.deleted_at IS NULL AND "
            + ADMISSIBLE, (version_id, owner)).fetchone()
        if not parent:
            raise missing()
        conn.execute("SELECT id FROM public.contract_templates WHERE id=%s" + (" FOR UPDATE" if lock else ""), (parent["template_id"],))
        version = conn.execute("SELECT v.id FROM public.contract_template_versions v JOIN public.contract_templates t ON t.id=v.template_id "
            "WHERE v.id=%s AND v.status='published' AND t.deleted_at IS NULL AND "
            + ADMISSIBLE +
            (" FOR UPDATE OF v" if lock else ""), (version_id, owner)).fetchone()
        if not version:
            raise missing()
        # Personal requires a confirmed analysis for this exact owner/version.
        return self.fields(conn, version_id)

    def admit(self, body, owner):
        with self.transaction(read=True) as conn:
            fields = self.template_for_create(conn, body.template_version_id, owner)
            validate_values(body.field_values, fields)

    def create(self, body, owner):
        with self.transaction() as conn:
            fields = self.template_for_create(conn, body.template_version_id, owner, lock=True)
            validate_values(body.field_values, fields)
            row = conn.execute("INSERT INTO public.contract_drafts(owner_user_id,template_version_id,procedure_id,title,field_values) "
                "VALUES(%s,%s,%s,%s,%s) RETURNING id", (owner, body.template_version_id, body.procedure_id, body.title, Jsonb(body.field_values))).fetchone()
            self.attach(conn, row["id"], owner, body.document_ids)
            return self.row(conn, row["id"], owner)

    def attach(self, conn, draft_id, owner, documents):
        for document in sorted(documents):
            conn.execute("INSERT INTO public.contract_draft_documents(draft_id,owner_user_id,document_id) VALUES(%s,%s,%s)", (draft_id, owner, document))

    def get(self, draft_id, owner):
        with self.transaction(read=True) as conn:
            return self.row(conn, draft_id, owner)

    def list(self, owner, page, size):
        with self.transaction(read=True) as conn:
            total = conn.execute("SELECT count(*) AS total FROM public.contract_drafts WHERE owner_user_id=%s AND deleted_at IS NULL", (owner,)).fetchone()["total"]
            ids = conn.execute("SELECT id FROM public.contract_drafts WHERE owner_user_id=%s AND deleted_at IS NULL ORDER BY updated_at DESC,id LIMIT %s OFFSET %s",
                (owner, size, (page-1)*size)).fetchall()
            return pagination([self.row(conn, row["id"], owner) for row in ids], total, page, size)

    def active_guard(self, conn, draft_id):
        if conn.execute("SELECT 1 FROM public.contract_generation_jobs WHERE draft_id=%s AND status IN ('queued','processing') LIMIT 1", (draft_id,)).fetchone():
            raise conflict()

    def admit_update(self, draft_id, body, owner):
        with self.transaction(read=True) as conn:
            row = self.row(conn, draft_id, owner)
            self.active_guard(conn, draft_id)
            if "field_values" in body.model_fields_set:
                validate_values(body.field_values, self.fields(conn, row["template_version_id"]))

    def update(self, draft_id, body, owner):
        changes = body.model_dump(exclude_unset=True)
        with self.transaction() as conn:
            row = self.row(conn, draft_id, owner, lock=True)
            self.active_guard(conn, draft_id)
            if "field_values" in changes:
                validate_values(body.field_values, self.fields(conn, row["template_version_id"]))
            assignments, values = [], []
            for key in ("title", "field_values"):
                if key in changes:
                    assignments.append(key + "=%s")
                    values.append(Jsonb(changes[key]) if key == "field_values" else changes[key])
            # Even link-only changes advance the draft revision under its row lock.
            if not assignments:
                assignments.append("updated_at=now()")
            conn.execute("UPDATE public.contract_drafts SET " + ",".join(assignments) + " WHERE id=%s AND owner_user_id=%s", (*values, draft_id, owner))
            if "document_ids" in changes:
                conn.execute("DELETE FROM public.contract_draft_documents WHERE draft_id=%s AND owner_user_id=%s", (draft_id, owner))
                self.attach(conn, draft_id, owner, body.document_ids)
            return self.row(conn, draft_id, owner)

    def delete(self, draft_id, owner):
        with self.transaction() as conn:
            self.row(conn, draft_id, owner, lock=True)
            self.active_guard(conn, draft_id)
            # Leave references intact; never delete original Document objects.
            conn.execute("UPDATE public.contract_drafts SET deleted_at=now() WHERE id=%s AND owner_user_id=%s", (draft_id, owner))

    def suggestion_context(self, draft_id, owner):
        with self.transaction(read=True) as conn:
            row = self.row(conn, draft_id, owner)
            revision = conn.execute("SELECT revision FROM public.contract_drafts WHERE id=%s AND owner_user_id=%s", (draft_id, owner)).fetchone()["revision"]
            return {"draft":row, "revision":revision, "fields":self.fields(conn, row["template_version_id"])}

    def assert_suggestion_current(self, draft_id, owner, revision):
        with self.transaction(read=True) as conn:
            current = conn.execute("SELECT revision FROM public.contract_drafts WHERE id=%s AND owner_user_id=%s AND deleted_at IS NULL", (draft_id, owner)).fetchone()
            if current is None:
                raise missing()
            if current["revision"] != revision:
                raise ContractError("DRAFT_CHANGED", "Nháp đã thay đổi; tải lại trước khi lấy gợi ý.", 409)
