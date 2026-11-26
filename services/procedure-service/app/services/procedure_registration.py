"""Allocate procedure codes and register procedures in their owning database."""

import hashlib
import re
from sqlite3 import IntegrityError as SQLiteIntegrityError
from uuid import UUID

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.repositories.procedure_repository import ProcedureDbUnavailable, db_connection

PROC_CODE_LOCK_NS = 7202026
PROC_ID_LOCK_NS = 7202027


class ProcedureCodeExists(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ProcedureCodeConflict(Exception):
    pass


class FormTemplateUnavailable(Exception):
    pass


def _prefix(domain: str) -> str:
    return "DD-" if domain == "land" else "HKD-"


def _domain_lock_key(domain: str) -> int:
    if domain == "land":
        return 1
    if domain == "business":
        return 2
    return int.from_bytes(hashlib.sha256(str(domain).encode("utf-8")).digest()[:4], "big", signed=True)


def _max_number(conn, domain: str) -> int:
    prefix = _prefix(domain)
    codes = conn.execute(
        text("SELECT code FROM public.procedures WHERE UPPER(code) LIKE :p"), {"p": f"{prefix}%"},
    ).scalars()
    highest = 0
    for code in codes:
        if code and code.upper().startswith(prefix):
            match = re.search(r"(\d+)", code[len(prefix):])
            if match:
                highest = max(highest, int(match.group(1)))
    return highest


def _allocate_code(conn, domain: str, floor: int) -> tuple[str, int]:
    number = max(_max_number(conn, domain), floor) + 1
    return f"{_prefix(domain)}{number:03d}", number


def next_code(domain: str, floor: int) -> tuple[str, int]:
    with db_connection() as conn:
        return _allocate_code(conn, domain, floor)


def _result(procedure_id: str, code: str, name: str, domain: str, *, created: bool) -> dict:
    return {
        "procedure_id": procedure_id,
        "code": code,
        "classification": {
            "key": code, "code": code, "name": name, "domain": domain,
            "kind": "project_procedure", "procedure_id": procedure_id,
        },
        "created": created,
    }


def _upsert_classification(conn, params: dict) -> None:
    inspector = inspect(conn)
    if not inspector.has_table("reference_classifications", schema="public"):
        return
    columns = {col["name"] for col in inspector.get_columns("reference_classifications", schema="public")}
    timestamps = [col for col in ("created_at", "updated_at") if col in columns]
    timestamp_columns = "".join(f", {col}" for col in timestamps)
    timestamp_values = ", CURRENT_TIMESTAMP" * len(timestamps)
    update_timestamp = ", updated_at = CURRENT_TIMESTAMP" if "updated_at" in columns else ""
    conn.execute(text(f"""
        INSERT INTO public.reference_classifications (key, code, name, domain, kind, procedure_id{timestamp_columns})
        VALUES (:classification_key, :code, :name, :domain, 'project_procedure', :id{timestamp_values})
        ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, code = EXCLUDED.code,
            procedure_id = EXCLUDED.procedure_id{update_timestamp}
    """), {**params, "classification_key": params["code"]})


def _find_procedure(conn, stored_id):
    return conn.execute(text("""
        SELECT code, name, domain FROM public.procedures WHERE id = :id
    """), {"id": stored_id}).mappings().first()


def exists(procedure_id: UUID) -> bool:
    with db_connection() as conn:
        stored_id = procedure_id if conn.dialect.name == "postgresql" else str(procedure_id)
        return _find_procedure(conn, stored_id) is not None


def _code_exists(conn, code: str) -> bool:
    return conn.execute(
        text("SELECT 1 FROM public.procedures WHERE UPPER(code) = :code"), {"code": code},
    ).first() is not None


def register(
    *, procedure_id: str, name: str, domain: str, code: str | None, floor: int,
    form_template_id: str | None, pdf_data: bytes | None, pdf_filename: str | None,
) -> dict:
    inserting_procedure = False
    stored_id = procedure_id
    try:
        with db_connection(write=True) as conn:
            postgres = conn.dialect.name == "postgresql"
            if postgres:
                conn.execute(text("SELECT pg_advisory_xact_lock(:ns, :key)"), {
                    "ns": PROC_ID_LOCK_NS, "key": int.from_bytes(UUID(procedure_id).bytes[:4], "big", signed=True),
                })
            stored_id = UUID(procedure_id) if postgres else procedure_id
            existing = _find_procedure(conn, stored_id)
            if existing is not None:
                return _result(procedure_id, **existing, created=False)

            if postgres:
                conn.execute(text("SELECT pg_advisory_xact_lock(:ns, :key)"), {
                    "ns": PROC_CODE_LOCK_NS, "key": _domain_lock_key(domain),
                })
            code = (code or "").strip().upper()
            if code:
                if match := re.fullmatch(r"([A-Z]+-)(\d+)", code):
                    code = f"{match[1]}{int(match[2]):03d}"
                if _code_exists(conn, code):
                    raise ProcedureCodeExists(code)
            else:
                code, _ = _allocate_code(conn, domain, floor)

            template_id = UUID(form_template_id) if postgres and form_template_id else form_template_id
            params = {"id": stored_id, "code": code, "name": name, "domain": domain, "template": template_id}
            if template_id:
                pdf_columns = ", reference_pdf = :pdf, reference_pdf_filename = :filename" if pdf_data is not None else ""
                linked = conn.execute(text(f"""
                    UPDATE public.custom_form_templates
                    SET procedure_id = :procedure_id, updated_at = CURRENT_TIMESTAMP{pdf_columns}
                    WHERE id = :template AND deleted_at IS NULL AND COALESCE(status, 'draft') <> 'archived'
                      AND (NULLIF(procedure_id, '') IS NULL OR procedure_id = :procedure_id)
                """), {"procedure_id": procedure_id, "template": template_id, "pdf": pdf_data, "filename": pdf_filename})
                if linked.rowcount != 1:
                    raise FormTemplateUnavailable()

            inserting_procedure = True
            conn.execute(text("""
                INSERT INTO public.procedures
                    (id, code, name, domain, description, status, form_template_id, processing_days, fee)
                VALUES (:id, :code, :name, :domain, :description, 'active', :template, 3, 0)
            """), {**params, "description": name})
            inserting_procedure = False
            _upsert_classification(conn, params)
    except (IntegrityError, ProcedureDbUnavailable) as exc:
        integrity = exc if isinstance(exc, IntegrityError) else exc.__cause__
        if not isinstance(integrity, IntegrityError):
            raise
        original = integrity.orig
        unique_violation = (
            (getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)) == "23505"
            or (isinstance(original, SQLiteIntegrityError)
                and str(original).startswith("UNIQUE constraint failed: "))
        )
        with db_connection() as conn:
            existing = _find_procedure(conn, stored_id)
            if existing is not None:
                return _result(procedure_id, **existing, created=False)
            if inserting_procedure and unique_violation and code and _code_exists(conn, code):
                raise ProcedureCodeConflict from exc
        raise

    return _result(procedure_id, code, name, domain, created=True)
