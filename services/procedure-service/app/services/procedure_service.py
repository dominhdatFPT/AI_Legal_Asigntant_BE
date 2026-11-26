"""
Unified Facade Service for Procedure Service.
Tích hợp các service chuyên biệt thành một điểm truy cập duy nhất để duy trì tính tương thích ngược 100%.

Các service thành phần:
- ProcedureCatalogService: Tra cứu danh mục thủ tục, tìm kiếm, lĩnh vực, lệ phí, điều kiện.
- ProcedureApplicationService: Quản lý vòng đời hồ sơ người dùng (bản nháp, cập nhật, nộp hồ sơ).
- procedure_form_service: Điều phối và cung cấp cấu trúc biểu mẫu (Form Schema) cho 25 thủ tục.
"""

from contextlib import contextmanager
import json
import logging
import os
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import bindparam, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.schemas.form_schema import ProcedureFormSchema
from app.services import procedure_form_service
from app.services.procedure_application_service import ProcedureApplicationService
from app.services.procedure_catalog_service import ProcedureCatalogService

logger = logging.getLogger(__name__)


_PURGE_TABLES = (
    "procedures",
    "custom_form_submissions",
    "form_template_preview_pages",
    "land_procedure_records",
    "business_procedure_records",
    "procedure_form_templates",
    "procedure_requirements",
    "procedure_legal_bases",
    "procedure_fees",
    "procedure_conditions",
    "dynamic_form_templates",
    "custom_form_templates",
    "form_templates",
    "reference_classifications",
    "contract_reference_uploads",
)


def _purge_statement(sql: str, expanding: tuple[str, ...] = ()):
    statement = text(sql)
    for parameter in expanding:
        statement = statement.bindparams(bindparam(parameter, expanding=True))
    return statement


def _purge_schema(conn) -> dict[str, set[str]]:
    inspector = inspect(conn)
    existing = set(inspector.get_table_names(schema="public"))
    return {
        table: {
            column["name"]
            for column in inspector.get_columns(table, schema="public")
        }
        for table in _PURGE_TABLES
        if table in existing
    }


def _purge_id(conn, value):
    if not value:
        return None
    if conn.dialect.name != "postgresql":
        return str(value)
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _purge_uuid_list(conn, values) -> list:
    items = sorted(str(value) for value in values if value)
    if conn.dialect.name != "postgresql":
        return items
    parsed = []
    for item in items:
        try:
            parsed.append(UUID(item))
        except (ValueError, TypeError, AttributeError):
            continue
    return parsed


def _purge_json_object(value) -> dict:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def _purge_json_text(conn, column: str, key: str) -> str:
    if conn.dialect.name == "postgresql":
        return f"({column} ->> '{key}')"
    return f"json_extract({column}, '$.{key}')"


def _prepare_purge_transaction(conn, tables) -> None:
    names = sorted(set(tables))
    if not names or conn.dialect.name != "postgresql":
        return

    conn.execute(text("SET LOCAL lock_timeout = '5s'"))
    conn.execute(text("SET LOCAL statement_timeout = '30s'"))

    trigger = conn.execute(
        _purge_statement(
            """
            SELECT c.relname, t.tgname
            FROM pg_catalog.pg_trigger AS t
            JOIN pg_catalog.pg_class AS c ON c.oid = t.tgrelid
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname IN :tables
              AND NOT t.tgisinternal
              AND t.tgenabled <> 'D'
              AND (t.tgtype & 8) <> 0
            LIMIT 1
            """,
            ("tables",),
        ),
        {"tables": names},
    ).mappings().first()
    if trigger:
        raise HTTPException(
            status_code=409,
            detail=(
                "Không thể xóa mà không chạy DELETE trigger nghiệp vụ: "
                f"{trigger['relname']}.{trigger['tgname']}."
            ),
        )


def _purge_has_procedure_template_id(conn) -> bool:
    """Inspect metadata without selecting a potentially missing column."""
    if conn.dialect.name == "postgresql":
        return bool(
            conn.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'procedures'
                          AND column_name = 'form_template_id'
                    )
                    """
                )
            ).scalar_one()
        )

    if conn.dialect.name == "sqlite":
        return any(
            row["name"] == "form_template_id"
            for row in conn.execute(
                text("PRAGMA public.table_info(procedures)")
            ).mappings()
        )

    return any(
        column["name"] == "form_template_id"
        for column in inspect(conn).get_columns("procedures", schema="public")
    )


@contextmanager
def _purge_contract_connection():
    """Open an independent transaction, exclusively after Procedure DB commit."""
    raw_url = (os.getenv("CONTRACT_DATABASE_URL") or "").strip()
    if not raw_url:
        raise RuntimeError("CONTRACT_DATABASE_URL is not configured")

    if raw_url.startswith("postgres://"):
        raw_url = "postgresql+psycopg://" + raw_url[len("postgres://"):]
    elif raw_url.startswith("postgresql://"):
        raw_url = "postgresql+psycopg://" + raw_url[len("postgresql://"):]

    url = make_url(raw_url)
    connect_args = {}
    if url.get_backend_name() == "postgresql":
        connect_args["connect_timeout"] = 5
        if url.get_driver_name() == "psycopg":
            connect_args["prepare_threshold"] = None

    engine = None
    try:
        engine = create_engine(
            url,
            poolclass=NullPool,
            connect_args=connect_args,
            hide_parameters=True,
        )
        with engine.begin() as contract_conn:
            yield contract_conn
    finally:
        if engine is not None:
            engine.dispose()


def _purge_cleanup_error(exc: Exception) -> str:
    """Expose exception type/SQLSTATE without SQL, parameters or credentials."""
    original = getattr(exc, "orig", exc)
    sqlstate = (
        getattr(original, "sqlstate", None)
        or getattr(original, "pgcode", None)
    )
    detail = type(exc).__name__
    if (
        isinstance(sqlstate, str)
        and len(sqlstate) == 5
        and sqlstate.isalnum()
    ):
        detail += f" (SQLSTATE {sqlstate})"
    return detail


def cleanup_purged_procedure_contract_uploads(result: dict, *, queued_event_id=None) -> dict:
    """Clean uploads in independent transactions after Procedure DB commit."""
    from app.repositories.procedure_repository import db_connection

    plan = result.pop("_contract_cleanup")
    sources = []
    outcomes = {}

    if plan["local_uploads"]:
        sources.append(
            ("procedure", lambda: db_connection(write=True))
        )

    if (os.getenv("CONTRACT_DATABASE_URL") or "").strip():
        sources.append(("contract", _purge_contract_connection))
    elif queued_event_id:
        outcomes["contract"] = {"status": "queued", "event_id": queued_event_id}
    else:
        outcomes["contract"] = {
            "status": "skipped",
            "reason": "CONTRACT_DATABASE_URL is not configured",
        }

    params = {
        "identifier": plan["identifier"],
        "keys": sorted(set(plan["keys"]).union(value for value in (plan["code"], plan["identifier"]) if value)),
        "proc_id_str": plan["proc_id_str"],
    }

    for namespace, connection_factory in sources:
        try:
            count = None
            with connection_factory() as upload_conn:
                inspector = inspect(upload_conn)
                exists = inspector.has_table(
                    "contract_reference_uploads",
                    schema="public",
                )
                if not exists:
                    outcome = {
                        "status": "skipped",
                        "reason": (
                            "public.contract_reference_uploads "
                            "does not exist"
                        ),
                    }
                else:
                    has_classifications = namespace == "contract" and inspector.has_table(
                        "reference_classifications", schema="public",
                    )
                    _prepare_purge_transaction(
                        upload_conn, {"contract_reference_uploads", "reference_classifications"}
                        if has_classifications else {"contract_reference_uploads"},
                    )
                    if namespace == "contract" and upload_conn.dialect.name == "postgresql":
                        for key in params["keys"]:
                            upload_conn.execute(text(
                                "SELECT pg_advisory_xact_lock(7202028, hashtext(:key))"
                            ), {"key": key})
                    metadata_code = _purge_json_text(upload_conn, "u.metadata", "procedure_code")
                    metadata_key = _purge_json_text(upload_conn, "u.metadata", "procedure")
                    metadata_id = _purge_json_text(upload_conn, "u.metadata", "procedure_id")
                    owner_guard = f"""
                        AND NOT EXISTS (SELECT 1 FROM public.reference_classifications owner
                            WHERE owner.procedure_id IS NOT NULL
                              AND CAST(owner.procedure_id AS TEXT) IS DISTINCT FROM :proc_id_str
                              AND (owner.key IN (u.procedure_key, {metadata_key}, {metadata_code})
                                   OR owner.code IN (u.procedure_key, {metadata_key}, {metadata_code})))
                    """ if has_classifications else ""
                    deleted = upload_conn.execute(
                        _purge_statement(
                            f"""
                            DELETE FROM public.contract_reference_uploads AS u
                            WHERE {metadata_id} = :proc_id_str
                               OR (NULLIF({metadata_id}, '') IS NULL
                                   AND (u.procedure_key IN :keys OR CAST(u.id AS TEXT) = :identifier
                                        OR {metadata_key} IN :keys OR {metadata_code} IN :keys)
                                   {owner_guard})
                            """,
                            ("keys",),
                        ),
                        params,
                    )
                    count = max(deleted.rowcount, 0)
                    if has_classifications:
                        upload_conn.execute(
                            _purge_statement(f"""
                                DELETE FROM public.reference_classifications AS rc
                                WHERE rc.kind = 'project_procedure'
                                  AND (CAST(rc.procedure_id AS TEXT) = :proc_id_str
                                       OR (rc.procedure_id IS NULL AND (rc.code IN :keys OR rc.key IN :keys)))
                                  AND NOT EXISTS (SELECT 1 FROM public.contract_reference_uploads u
                                      WHERE {metadata_id} = CAST(rc.procedure_id AS TEXT)
                                         OR ((rc.procedure_id IS NULL OR NULLIF({metadata_id}, '') IS NULL)
                                             AND (u.procedure_key IN (rc.key, rc.code)
                                                  OR {metadata_key} IN (rc.key, rc.code)
                                                  OR {metadata_code} IN (rc.key, rc.code))))
                            """, ("keys",)),
                            params,
                        )
                    outcome = {
                        "status": "completed",
                        "deleted": count,
                    }

            # The context manager has committed successfully.
            outcomes[namespace] = outcome
            if count is not None:
                result["deleted"][
                    f"{namespace}.contract_reference_uploads"
                ] = count

        except Exception as exc:
            outcomes[namespace] = {
                "status": "failed",
                "error": _purge_cleanup_error(exc),
            }
            # Operational warning only; no audit/event or raw exception.
            logger.warning(
                "Post-commit upload cleanup failed (%s, %s).",
                namespace,
                type(exc).__name__,
            )

    failures = [
        f"{namespace}: {outcome['error']}"
        for namespace, outcome in outcomes.items()
        if outcome["status"] == "failed"
    ]
    skipped = next((outcome for outcome in outcomes.values() if outcome["status"] == "skipped"), None)

    if failures:
        cleanup = {
            "status": "failed",
            "error": "; ".join(failures),
        }
    elif skipped:
        cleanup = dict(skipped)
    elif outcomes.get("contract", {}).get("status") == "queued":
        cleanup = dict(outcomes["contract"])
    else:
        cleanup = {"status": "completed", "mode": "direct"}

    if queued_event_id and cleanup["status"] in ("failed", "skipped"):
        cleanup["retry_event_id"] = queued_event_id
    cleanup["sources"] = outcomes
    result["contract_cleanup"] = cleanup
    return cleanup


def invalidate_procedure_caches() -> None:
    from app.api.custom_form_templates import _invalidate_template_caches
    from app.core.catalog_cache import catalog_cache

    _invalidate_template_caches()
    for namespace in ("procedures", "procedure_domains", "classifications", "templates", "dynamic_templates"):
        catalog_cache.invalidate(namespace)


def purge_procedure(conn, procedure_id_or_code: str) -> dict:
    """Purge local procedure data inside the caller-owned transaction.

    Never connect to Contract DB, commit, publish events or invalidate caches
    here. The endpoint performs post-commit cache invalidation and upload cleanup.
    """
    identifier = procedure_id_or_code.strip()
    if not identifier:
        raise HTTPException(422, "Mã hoặc ID thủ tục không được để trống.")

    local_schema = _purge_schema(conn)
    if "procedures" not in local_schema:
        raise HTTPException(503, "Chưa có bảng public.procedures.")

    _prepare_purge_transaction(conn, local_schema)

    has_template_id = _purge_has_procedure_template_id(conn)
    template_projection = (
        "form_template_id"
        if has_template_id
        else "NULL AS form_template_id"
    )
    procedure_select = (
        f"SELECT id, code, name, {template_projection} "
        "FROM public.procedures "
    )
    lock_suffix = (
        " FOR UPDATE" if conn.dialect.name == "postgresql" else ""
    )

    procedures = conn.execute(
        text(
            procedure_select
            + "WHERE id = :id OR code = :code"
            + " ORDER BY id"
            + lock_suffix
        ),
        {"id": _purge_id(conn, identifier), "code": identifier},
    ).mappings().all()
    if len(procedures) > 1:
        raise HTTPException(409, "Giá trị này khớp với nhiều thủ tục.")
    procedure = dict(procedures[0]) if procedures else None

    def classifications(lookup):
        if not lookup or "reference_classifications" not in local_schema:
            return []
        return list(
            conn.execute(
                text(
                    "SELECT key, code, name, procedure_id "
                    "FROM public.reference_classifications "
                    "WHERE code = :lookup OR key = :lookup "
                    "OR CAST(procedure_id AS TEXT) = :lookup"
                ),
                {"lookup": str(lookup)},
            ).mappings()
        )

    references = classifications(identifier)
    custom_matches = []

    if "custom_form_templates" in local_schema:
        content_code = _purge_json_text(conn, "content", "code")
        content_procedure_code = _purge_json_text(
            conn, "content", "procedure_code"
        )
        custom_matches = list(
            conn.execute(
                text(
                    "SELECT id, procedure_id, name, content "
                    "FROM public.custom_form_templates "
                    "WHERE id = :id OR procedure_id = :identifier "
                    f"OR {content_code} = :identifier "
                    f"OR {content_procedure_code} = :identifier"
                ),
                {
                    "id": _purge_id(conn, identifier),
                    "identifier": identifier,
                },
            ).mappings()
        )

    # Local metadata may resolve aliases/orphans. No Contract DB connection
    # is opened during the primary transaction.
    upload_matches = []
    if "contract_reference_uploads" in local_schema:
        metadata_code = _purge_json_text(
            conn, "metadata", "procedure_code"
        )
        metadata_id = _purge_json_text(
            conn, "metadata", "procedure_id"
        )
        upload_matches = list(
            conn.execute(
                text(
                    "SELECT procedure_key, metadata "
                    "FROM public.contract_reference_uploads "
                    "WHERE id = :id OR procedure_key = :identifier "
                    f"OR {metadata_code} = :identifier "
                    f"OR {metadata_id} = :identifier"
                ),
                {
                    "id": _purge_id(conn, identifier),
                    "identifier": identifier,
                },
            ).mappings()
        )
        for upload in upload_matches:
            metadata = _purge_json_object(upload["metadata"])
            references.append(
                {
                    "key": upload["procedure_key"],
                    "code": metadata.get("procedure_code"),
                    "name": metadata.get("procedure_name"),
                    "procedure_id": metadata.get("procedure_id"),
                }
            )
            for lookup in (
                upload["procedure_key"],
                metadata.get("procedure_code"),
                metadata.get("procedure_id"),
            ):
                references.extend(classifications(lookup))

    candidate_ids = {
        str(row["procedure_id"])
        for row in [*references, *custom_matches]
        if row.get("procedure_id")
    }
    candidate_codes = {
        str(row["code"])
        for row in references
        if row.get("code")
    }
    for row in custom_matches:
        content = _purge_json_object(row["content"])
        for field in ("code", "procedure_code"):
            if content.get(field):
                candidate_codes.add(str(content[field]))

    if procedure is None and (candidate_ids or candidate_codes):
        candidate_predicates = []
        candidate_expanding = []
        if candidate_ids:
            candidate_predicates.append("CAST(id AS TEXT) IN :ids")
            candidate_expanding.append("ids")
        if candidate_codes:
            candidate_predicates.append("code IN :codes")
            candidate_expanding.append("codes")
        candidates = conn.execute(
            _purge_statement(
                procedure_select
                + "WHERE "
                + " OR ".join(candidate_predicates)
                + " ORDER BY id"
                + lock_suffix,
                tuple(candidate_expanding),
            ),
            {
                "ids": sorted(candidate_ids),
                "codes": sorted(candidate_codes),
            },
        ).mappings().all()
        if len(candidates) > 1:
            raise HTTPException(
                409,
                "Dữ liệu liên kết khớp với nhiều thủ tục; chưa thực hiện xóa.",
            )
        if candidates:
            procedure = dict(candidates[0])

    if (
        not procedure
        and not references
        and not custom_matches
        and not upload_matches
    ):
        raise HTTPException(
            404, "Không tìm thấy thủ tục hoặc dữ liệu mồ côi."
        )

    if procedure:
        proc_id_str = str(procedure["id"])
        code = procedure["code"]
        name = procedure["name"]
    else:
        orphan_names = {
            str(row["name"])
            for row in references
            if row.get("name")
        }
        if (
            len(candidate_ids) > 1
            or len(candidate_codes) > 1
            or len(orphan_names) > 1
        ):
            raise HTTPException(
                409,
                "Dữ liệu mồ côi không xác định duy nhất một thủ tục.",
            )

        proc_id_str = next(iter(candidate_ids), None)
        code = next(iter(candidate_codes), None)
        name = next(iter(orphan_names), None)
        if not code:
            try:
                UUID(identifier)
            except ValueError:
                code = identifier

    references.extend(classifications(code))
    references.extend(classifications(proc_id_str))

    keys = {
        str(value)
        for value in (identifier, code, proc_id_str)
        if value
    }
    for reference in references:
        for field in ("key", "code"):
            if reference.get(field):
                keys.add(str(reference[field]))

    custom_ids = {str(row["id"]) for row in custom_matches}
    if procedure and procedure.get("form_template_id"):
        custom_ids.add(str(procedure["form_template_id"]))

    proc_id = _purge_id(conn, proc_id_str)

    # Include templates reachable only through the association table.
    if "procedure_form_templates" in local_schema and proc_id:
        linked_ids = conn.execute(
            text(
                "SELECT template_id FROM public.procedure_form_templates "
                "WHERE procedure_id = :proc_id"
            ),
            {"proc_id": proc_id},
        ).scalars()
        custom_ids.update(str(value) for value in linked_ids if value)

    linked_templates = []
    if "custom_form_templates" in local_schema:
        content_code = _purge_json_text(conn, "content", "code")
        content_procedure_code = _purge_json_text(
            conn, "content", "procedure_code"
        )
        initial_bound_ids = _purge_uuid_list(conn, custom_ids)
        template_predicates = ["procedure_id = :proc_id_str"]
        template_expanding = []
        if initial_bound_ids:
            template_predicates.append("id IN :ids")
            template_expanding.append("ids")
        if keys:
            template_predicates.append(f"{content_code} IN :keys")
            template_predicates.append(f"{content_procedure_code} IN :keys")
            template_expanding.append("keys")
        linked_templates = conn.execute(
            _purge_statement(
                "SELECT id, procedure_id "
                "FROM public.custom_form_templates "
                "WHERE " + " OR ".join(template_predicates),
                tuple(template_expanding),
            ),
            {
                "proc_id_str": proc_id_str,
                "ids": initial_bound_ids,
                "keys": sorted(keys),
            },
        ).mappings().all()
        custom_ids.update(str(row["id"]) for row in linked_templates)

    bound_custom_ids = _purge_uuid_list(conn, custom_ids)
    sorted_keys = sorted(keys)
    params = {
        "proc_id": proc_id,
        "proc_id_str": proc_id_str,
        "code": code,
        "name": name,
        "resolved_code": code,
        "procedure_name": name,
        "ids": bound_custom_ids,
        "keys": sorted_keys,
        "identifier": identifier,
    }

    shared_detail = (
        "Không thể xóa: Mẫu biểu mẫu đang được sử dụng chung "
        "bởi thủ tục khác."
    )

    if (
        bound_custom_ids
        and "custom_form_templates" in local_schema
        and conn.dialect.name == "postgresql"
    ):
        conn.execute(
            _purge_statement(
                "SELECT id FROM public.custom_form_templates "
                "WHERE id IN :ids ORDER BY id FOR UPDATE",
                ("ids",),
            ),
            params,
        ).all()

    # Check every reference before executing any DELETE.
    if bound_custom_ids:
        if has_template_id:
            exclude_current = (
                " AND id <> :proc_id" if proc_id else ""
            )
            shared = conn.execute(
                _purge_statement(
                    "SELECT 1 FROM public.procedures "
                    "WHERE form_template_id IN :ids"
                    + exclude_current
                    + " LIMIT 1",
                    ("ids",),
                ),
                params,
            ).first()
            if shared:
                raise HTTPException(409, shared_detail)

        if "procedure_form_templates" in local_schema:
            exclude_current_link = (
                " AND procedure_id <> :proc_id" if proc_id else ""
            )
            shared_link = conn.execute(
                _purge_statement(
                    "SELECT 1 FROM public.procedure_form_templates "
                    "WHERE template_id IN :ids"
                    + exclude_current_link
                    + " LIMIT 1",
                    ("ids",),
                ),
                params,
            ).first()
            if shared_link:
                raise HTTPException(409, shared_detail)

    if any(
        row["procedure_id"]
        and str(row["procedure_id"]) != proc_id_str
        for row in linked_templates
    ):
        raise HTTPException(409, shared_detail)

    legacy_columns = local_schema.get("form_templates", set())
    legacy_parts = []
    name_only_where = None

    if "procedure_code" in legacy_columns:
        legacy_parts.append("procedure_code = :resolved_code")
        if "procedure_name" in legacy_columns:
            legacy_parts.append(
                "(procedure_name = :procedure_name AND "
                "(procedure_code IS NULL OR procedure_code = '' "
                "OR procedure_code = :resolved_code))"
            )
            name_only_where = (
                "procedure_name = :procedure_name AND "
                "(procedure_code IS NULL OR procedure_code = '')"
            )
    elif "procedure_name" in legacy_columns:
        # Older schemas have no procedure_code column to disambiguate.
        name_only_where = "procedure_name = :procedure_name"
        legacy_parts.append(name_only_where)

    legacy_where = " OR ".join(legacy_parts) or "1 = 0"

    # Only uncoded/name-only legacy rows remain ambiguous when names collide.
    if name and name_only_where:
        has_name_only = conn.execute(
            text(
                "SELECT 1 FROM public.form_templates WHERE "
                + name_only_where
                + " LIMIT 1"
            ),
            params,
        ).first()
        if has_name_only:
            exclude_current = (
                " AND id <> :proc_id" if proc_id else ""
            )
            other_same_name = conn.execute(
                text(
                    "SELECT 1 FROM public.procedures "
                    "WHERE name = :procedure_name"
                    + exclude_current
                    + " LIMIT 1"
                ),
                params,
            ).first()
            if other_same_name:
                raise HTTPException(
                    409,
                    "Mẫu tham khảo khớp tên của thủ tục khác; "
                    "chưa thực hiện xóa.",
                )

    deleted = {}

    def remove(table, where, expanding=()):
        if table not in local_schema:
            return
        result = conn.execute(
            _purge_statement(
                f"DELETE FROM public.{table} WHERE {where}",
                expanding,
            ),
            params,
        )
        deleted[f"procedure.{table}"] = max(result.rowcount, 0)

    if bound_custom_ids:
        remove(
            "custom_form_submissions",
            "template_id IN :ids",
            ("ids",),
        )
    elif "custom_form_submissions" in local_schema:
        deleted["procedure.custom_form_submissions"] = 0
    if legacy_columns:
        remove(
            "form_template_preview_pages",
            "template_id IN "
            "(SELECT id FROM public.form_templates WHERE "
            + legacy_where
            + ")",
        )

    for table in (
        "land_procedure_records",
        "business_procedure_records",
    ):
        remove(
            table,
            "procedure_id = :proc_id OR procedure_code = :code",
        )

    for table in (
        "procedure_form_templates",
        "procedure_requirements",
        "procedure_legal_bases",
        "procedure_fees",
        "procedure_conditions",
    ):
        remove(table, "procedure_id = :proc_id")

    if sorted_keys:
        remove(
            "dynamic_form_templates",
            "procedure_id = :proc_id_str OR code IN :keys",
            ("keys",),
        )
        remove(
            "reference_classifications",
            "procedure_id = :proc_id OR code = :code OR key IN :keys",
            ("keys",),
        )
    else:
        remove("dynamic_form_templates", "procedure_id = :proc_id_str")
        remove("reference_classifications", "procedure_id = :proc_id OR code = :code")

    # Remove the procedure before templates referenced by form_template_id.
    # All these deletes remain inside the same primary transaction.
    remove("procedures", "id = :proc_id")
    if bound_custom_ids:
        remove(
            "custom_form_templates",
            "id IN :ids",
            ("ids",),
        )
    elif "custom_form_templates" in local_schema:
        deleted["procedure.custom_form_templates"] = 0
    remove("form_templates", legacy_where)

    return {
        "procedure_id": proc_id_str,
        "code": code,
        "name": name,
        "orphan_cleanup": procedure is None,
        "custom_template_ids": sorted(custom_ids),
        "deleted": deleted,
        "contract_cleanup": {"status": "pending"},
        "_contract_cleanup": {
            "identifier": identifier,
            "keys": sorted(keys),
            "code": code,
            "proc_id_str": proc_id_str,
            "local_uploads": (
                "contract_reference_uploads" in local_schema
            ),
        },
    }


class ProcedureService(ProcedureCatalogService, ProcedureApplicationService):
    """
    Unified Facade Service:
    Kế thừa đầy đủ các phương thức từ ProcedureCatalogService và ProcedureApplicationService.
    Bảo đảm tất cả các endpoint API và test suite gọi qua ProcedureService.<method> hoạt động ổn định.
    """

    @staticmethod
    def get_form_schema(procedure_code: str) -> ProcedureFormSchema:
        """Hàm trung gian gọi Dispatcher lấy form schema của thủ tục"""
        return procedure_form_service.get_procedure_form_schema(procedure_code)
