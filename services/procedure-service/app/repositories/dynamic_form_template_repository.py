import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import text
from app.repositories.procedure_repository import db_connection
from app.core.exceptions import (
    TemplateNotFoundError,
    TemplateNotDraftError,
    TemplateInvalidStatusError,
)

logger = logging.getLogger(__name__)


def _extract_domain(val: Any) -> str:
    if hasattr(val, "value"):
        return str(val.value)
    return str(val or "dat_dai")


def _row_to_dict(row) -> Dict[str, Any]:
    if not row:
        return {}
    m = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
    content = m.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except Exception:
            pass
    return {
        "id": str(m["id"]) if m.get("id") else "",
        "template_group_id": str(m["template_group_id"]) if m.get("template_group_id") else "",
        "version": int(m.get("version", 1)),
        "status": str(m.get("status", "draft")),
        "name": str(m.get("name", "")),
        "code": m.get("code"),
        "domain": str(m.get("domain", "")),
        "procedure_id": m.get("procedure_id"),
        "content": content if isinstance(content, dict) else {},
        "created_by": str(m["created_by"]) if m.get("created_by") else None,
        "updated_by": str(m["updated_by"]) if m.get("updated_by") else None,
        "created_at": m["created_at"].isoformat() if hasattr(m.get("created_at"), "isoformat") else str(m.get("created_at") or ""),
        "updated_at": m["updated_at"].isoformat() if hasattr(m.get("updated_at"), "isoformat") else str(m.get("updated_at") or ""),
        "deleted_at": m["deleted_at"].isoformat() if hasattr(m.get("deleted_at"), "isoformat") else (str(m["deleted_at"]) if m.get("deleted_at") else None),
    }


class DynamicFormTemplateRepository:

    @staticmethod
    def create(content_dict: Dict[str, Any], user_id: Optional[str] = None) -> Dict[str, Any]:
        # Tự động tạo thủ tục hành chính mới nếu có yêu cầu
        if content_dict.get("new_procedure") and isinstance(content_dict["new_procedure"], dict):
            np = content_dict["new_procedure"]
            np_code = (np.get("code") or "").strip().upper()
            np_name = (np.get("name") or "").strip()
            if np_code and np_name:
                from app.repositories.procedure_repository import ProcedureRepository
                existing_proc = ProcedureRepository.get_procedure_by_id_or_code(np_code)
                if existing_proc:
                    from fastapi import HTTPException
                    raise HTTPException(
                        status_code=422,
                        detail=f"Mã thủ tục '{np_code}' đã tồn tại trên hệ thống ('{existing_proc.get('name')}'). Vui lòng nhập mã khác hoặc chuyển sang chế độ 'Chọn thủ tục đã có'."
                    )
                ProcedureRepository.create_or_ensure_procedure(
                    code=np_code,
                    name=np_name,
                    domain=content_dict.get("domain", "dat_dai")
                )
                content_dict["procedure_id"] = np_code

        new_id = str(uuid.uuid4())
        new_group_id = str(uuid.uuid4())
        domain_str = _extract_domain(content_dict.get("domain"))
        with db_connection(write=True) as conn:
            stmt = text("""
                INSERT INTO public.dynamic_form_templates (
                    id, template_group_id, version, status, name, code, domain, procedure_id, content, created_by, updated_by
                ) VALUES (
                    :id, :template_group_id, 1, 'draft', :name, :code, :domain, :procedure_id, :content, :user_id, :user_id
                ) RETURNING *;
            """)
            row = conn.execute(
                stmt,
                {
                    "id": new_id,
                    "template_group_id": new_group_id,
                    "name": content_dict.get("name", ""),
                    "code": content_dict.get("code"),
                    "domain": domain_str,
                    "procedure_id": content_dict.get("procedure_id"),
                    "content": json.dumps(content_dict, ensure_ascii=False),
                    "user_id": user_id,
                },
            ).fetchone()
            return _row_to_dict(row)

    @staticmethod
    def get_by_id(template_id: str, include_deleted: bool = False) -> Dict[str, Any]:
        with db_connection(write=False) as conn:
            query = "SELECT * FROM public.dynamic_form_templates WHERE id = :id"
            if not include_deleted:
                query += " AND deleted_at IS NULL"
            row = conn.execute(text(query), {"id": template_id}).fetchone()
            if not row:
                raise TemplateNotFoundError("Không tìm thấy mẫu đơn.")
            return _row_to_dict(row)

    @staticmethod
    def update(template_id: str, content_dict: Dict[str, Any], user_id: Optional[str] = None) -> Dict[str, Any]:
        if content_dict.get("new_procedure") and isinstance(content_dict["new_procedure"], dict):
            np = content_dict["new_procedure"]
            np_code = (np.get("code") or "").strip().upper()
            np_name = (np.get("name") or "").strip()
            if np_code and np_name:
                from app.repositories.procedure_repository import ProcedureRepository
                existing_proc = ProcedureRepository.get_procedure_by_id_or_code(np_code)
                if existing_proc:
                    from fastapi import HTTPException
                    raise HTTPException(
                        status_code=422,
                        detail=f"Mã thủ tục '{np_code}' đã tồn tại trên hệ thống ('{existing_proc.get('name')}'). Vui lòng nhập mã khác hoặc chuyển sang chế độ 'Chọn thủ tục đã có'."
                    )
                ProcedureRepository.create_or_ensure_procedure(
                    code=np_code,
                    name=np_name,
                    domain=content_dict.get("domain", "dat_dai")
                )
                content_dict["procedure_id"] = np_code

        domain_str = _extract_domain(content_dict.get("domain"))
        with db_connection(write=True) as conn:
            check_row = conn.execute(
                text("SELECT * FROM public.dynamic_form_templates WHERE id = :id AND deleted_at IS NULL"),
                {"id": template_id},
            ).fetchone()
            if not check_row:
                raise TemplateNotFoundError("Không tìm thấy mẫu đơn.")
            status_val = check_row._mapping["status"] if hasattr(check_row, "_mapping") else check_row["status"]
            if status_val != "draft":
                raise TemplateNotDraftError("Chỉ sửa được bản nháp. Hãy tạo phiên bản mới.")

            stmt = text("""
                UPDATE public.dynamic_form_templates
                SET content = :content,
                    name = :name,
                    code = :code,
                    domain = :domain,
                    procedure_id = :procedure_id,
                    updated_by = :user_id,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
                RETURNING *;
            """)
            mapping = check_row._mapping if hasattr(check_row, "_mapping") else check_row
            row = conn.execute(
                stmt,
                {
                    "id": template_id,
                    "content": json.dumps(content_dict, ensure_ascii=False),
                    "name": content_dict.get("name", mapping["name"]),
                    "code": content_dict.get("code", mapping["code"]),
                    "domain": domain_str if content_dict.get("domain") else mapping["domain"],
                    "procedure_id": content_dict.get("procedure_id", mapping["procedure_id"]),
                    "user_id": user_id,
                },
            ).fetchone()
            return _row_to_dict(row)

    @staticmethod
    def list_admin(
        domain: Optional[str] = None,
        status: Optional[str] = None,
        include_deleted: bool = False,
    ) -> Tuple[List[Dict[str, Any]], int]:
        with db_connection(write=False) as conn:
            where_clauses = []
            params: Dict[str, Any] = {}

            if not include_deleted:
                where_clauses.append("deleted_at IS NULL")
            if domain:
                where_clauses.append("domain = :domain")
                params["domain"] = domain
            if status:
                where_clauses.append("status = :status")
                params["status"] = status

            where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            sql = f"""
                SELECT * FROM public.dynamic_form_templates
                {where_str}
                ORDER BY updated_at DESC, version DESC
            """
            rows = conn.execute(text(sql), params).fetchall()
            items = [_row_to_dict(r) for r in rows]
            return items, len(items)

    @staticmethod
    def list_published(
        domain: Optional[str] = None,
        procedure_id: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        with db_connection(write=False) as conn:
            where_clauses = ["status = 'published'", "deleted_at IS NULL"]
            params: Dict[str, Any] = {}

            if domain:
                where_clauses.append("domain = :domain")
                params["domain"] = domain
            if procedure_id:
                where_clauses.append("procedure_id = :procedure_id")
                params["procedure_id"] = procedure_id

            where_str = f"WHERE {' AND '.join(where_clauses)}"
            sql = f"""
                SELECT *
                FROM public.dynamic_form_templates
                {where_str}
                ORDER BY version DESC, updated_at DESC
            """
            rows = conn.execute(text(sql), params).fetchall()
            items = [_row_to_dict(r) for r in rows]

            # Giữ bản mới nhất cho mỗi nhóm (tương thích cả Postgres và SQLite)
            seen_groups = set()
            unique_items = []
            for it in items:
                gid = it["template_group_id"]
                if gid not in seen_groups:
                    seen_groups.add(gid)
                    unique_items.append(it)

            return unique_items, len(unique_items)

    @staticmethod
    def publish(template_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        with db_connection(write=True) as conn:
            row = conn.execute(
                text("SELECT * FROM public.dynamic_form_templates WHERE id = :id AND deleted_at IS NULL"),
                {"id": template_id},
            ).fetchone()
            if not row:
                raise TemplateNotFoundError("Không tìm thấy mẫu đơn.")

            m = row._mapping if hasattr(row, "_mapping") else row
            current_status = m["status"]
            if current_status not in ("draft", "hidden"):
                raise TemplateInvalidStatusError("Chỉ xuất bản được bản nháp hoặc mẫu đang ẩn.")

            group_id = m["template_group_id"]
            # Chuyển các bản published/hidden cũ cùng nhóm sang archived
            conn.execute(
                text("""
                    UPDATE public.dynamic_form_templates
                    SET status = 'archived', updated_at = CURRENT_TIMESTAMP
                    WHERE template_group_id = :gid
                      AND status IN ('published', 'hidden')
                      AND deleted_at IS NULL
                      AND id != :id
                """),
                {"gid": group_id, "id": template_id},
            )

            # Xuất bản bản hiện tại
            pub_row = conn.execute(
                text("""
                    UPDATE public.dynamic_form_templates
                    SET status = 'published',
                        updated_by = :user_id,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id
                    RETURNING *;
                """),
                {"id": template_id, "user_id": user_id},
            ).fetchone()
            return _row_to_dict(pub_row)

    @staticmethod
    def hide(template_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        with db_connection(write=True) as conn:
            row = conn.execute(
                text("SELECT * FROM public.dynamic_form_templates WHERE id = :id AND deleted_at IS NULL"),
                {"id": template_id},
            ).fetchone()
            if not row:
                raise TemplateNotFoundError("Không tìm thấy mẫu đơn.")

            m = row._mapping if hasattr(row, "_mapping") else row
            if m["status"] != "published":
                raise TemplateInvalidStatusError("Chỉ ẩn được mẫu đang xuất bản.")

            updated = conn.execute(
                text("""
                    UPDATE public.dynamic_form_templates
                    SET status = 'hidden',
                        updated_by = :user_id,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id
                    RETURNING *;
                """),
                {"id": template_id, "user_id": user_id},
            ).fetchone()
            return _row_to_dict(updated)

    @staticmethod
    def new_version(template_id: str, user_id: Optional[str] = None) -> Tuple[Dict[str, Any], bool]:
        """Tạo bản draft mới cùng nhóm.
        Nếu nhóm đã có bản nháp chưa xóa, trả lại bản nháp đó (already_existed=True).
        """
        with db_connection(write=True) as conn:
            row = conn.execute(
                text("SELECT * FROM public.dynamic_form_templates WHERE id = :id AND deleted_at IS NULL"),
                {"id": template_id},
            ).fetchone()
            if not row:
                raise TemplateNotFoundError("Không tìm thấy mẫu đơn.")

            m = row._mapping if hasattr(row, "_mapping") else row
            group_id = m["template_group_id"]

            # Xóa sạch mọi bản nháp cũ đã bị xóa mềm của nhóm này để tránh rác và xung đột constraint
            conn.execute(
                text("DELETE FROM public.dynamic_form_templates WHERE template_group_id = :gid AND status = 'draft' AND deleted_at IS NOT NULL"),
                {"gid": group_id},
            )

            existing_draft = conn.execute(
                text("""
                    SELECT * FROM public.dynamic_form_templates
                    WHERE template_group_id = :gid
                      AND status = 'draft'
                      AND deleted_at IS NULL
                    ORDER BY version DESC
                    LIMIT 1
                """),
                {"gid": group_id},
            ).fetchone()

            if existing_draft:
                return _row_to_dict(existing_draft), True

            max_ver_row = conn.execute(
                text("SELECT COALESCE(MAX(version), 0) AS max_v FROM public.dynamic_form_templates WHERE template_group_id = :gid AND deleted_at IS NULL"),
                {"gid": group_id},
            ).fetchone()
            max_m = max_ver_row._mapping if hasattr(max_ver_row, "_mapping") else max_ver_row
            next_ver = (max_m["max_v"] or 0) + 1

            # Đảm bảo không bị trùng unique constraint uq_dft_group_version với các bản đã xóa
            conn.execute(
                text("DELETE FROM public.dynamic_form_templates WHERE template_group_id = :gid AND version >= :next_ver AND deleted_at IS NOT NULL"),
                {"gid": group_id, "next_ver": next_ver},
            )

            new_id = str(uuid.uuid4())
            new_row = conn.execute(
                text("""
                    INSERT INTO public.dynamic_form_templates (
                        id, template_group_id, version, status, name, code, domain, procedure_id, content, created_by, updated_by
                    ) VALUES (
                        :id, :gid, :ver, 'draft', :name, :code, :domain, :procedure_id, :content, :user_id, :user_id
                    ) RETURNING *;
                """),
                {
                    "id": new_id,
                    "gid": group_id,
                    "ver": next_ver,
                    "name": m["name"],
                    "code": m["code"],
                    "domain": m["domain"],
                    "procedure_id": m["procedure_id"],
                    "content": json.dumps(m["content"], ensure_ascii=False) if isinstance(m["content"], dict) else m["content"],
                    "user_id": user_id,
                },
            ).fetchone()
            return _row_to_dict(new_row), False

    @staticmethod
    def soft_delete(template_id: str) -> None:
        with db_connection(write=True) as conn:
            row = conn.execute(
                text("SELECT id, status, deleted_at FROM public.dynamic_form_templates WHERE id = :id"),
                {"id": template_id},
            ).fetchone()
            if not row:
                raise TemplateNotFoundError("Không tìm thấy mẫu đơn.")
            m = row._mapping if hasattr(row, "_mapping") else row

            # Nếu là bản nháp (draft) hoặc đã bị xóa mềm trước đó: XÓA CỨNG VĨNH VIỄN KHỎI DATABASE!
            if m["status"] == "draft" or m["deleted_at"] is not None:
                conn.execute(
                    text("DELETE FROM public.dynamic_form_templates WHERE id = :id"),
                    {"id": template_id},
                )
            else:
                conn.execute(
                    text("UPDATE public.dynamic_form_templates SET deleted_at = CURRENT_TIMESTAMP WHERE id = :id"),
                    {"id": template_id},
                )

    @staticmethod
    def restore(template_id: str) -> Dict[str, Any]:
        with db_connection(write=True) as conn:
            row = conn.execute(
                text("SELECT * FROM public.dynamic_form_templates WHERE id = :id"),
                {"id": template_id},
            ).fetchone()
            if not row:
                raise TemplateNotFoundError("Không tìm thấy mẫu đơn.")

            m = row._mapping if hasattr(row, "_mapping") else row
            if m["deleted_at"] is None:
                return _row_to_dict(row)

            group_id = m["template_group_id"]
            current_status = m["status"]

            # An toàn: Kiểm tra tránh vi phạm partial unique index
            if current_status == "draft":
                conflicting = conn.execute(
                    text("SELECT id FROM public.dynamic_form_templates WHERE template_group_id = :gid AND status = 'draft' AND deleted_at IS NULL"),
                    {"gid": group_id},
                ).fetchone()
                if conflicting:
                    raise TemplateInvalidStatusError("Nhóm đã có bản nháp khác đang hoạt động, không thể khôi phục bản nháp này.")

            if current_status in ("published", "hidden"):
                conflicting = conn.execute(
                    text("SELECT id FROM public.dynamic_form_templates WHERE template_group_id = :gid AND status IN ('published', 'hidden') AND deleted_at IS NULL"),
                    {"gid": group_id},
                ).fetchone()
                if conflicting:
                    raise TemplateInvalidStatusError("Nhóm đã có bản đang xuất bản/ẩn khác, không thể khôi phục bản này.")

            restored = conn.execute(
                text("UPDATE public.dynamic_form_templates SET deleted_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = :id RETURNING *"),
                {"id": template_id},
            ).fetchone()
            return _row_to_dict(restored)
