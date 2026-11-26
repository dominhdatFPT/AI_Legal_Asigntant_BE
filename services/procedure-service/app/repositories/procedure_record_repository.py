import json
import re
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import text

from app.core.exceptions import (
    ApplicationLockedError,
    ApplicationNotFoundError,
    ApplicationValidationError,
)
from app.repositories.procedure_repository import db_connection

HEX_8_PATTERN = re.compile(r"^[0-9a-fA-F]{8}$")

def safe_uuid_str(val) -> Optional[str]:
    """Chuyển đổi an toàn sang chuỗi UUID hợp lệ, chống TypeError trong Python 3.14"""
    if val is None:
        return None
    try:
        return str(UUID(str(val)))
    except (ValueError, TypeError, AttributeError):
        return None


def _serialize_row(row: dict) -> dict:
    """Chuyển đổi kiểu dữ liệu UUID, datetime để an toàn serialize JSON và cấp application_code chuẩn"""
    result = {}
    for k, v in row.items():
        if isinstance(v, UUID):
            result[k] = str(v)
        elif hasattr(v, "isoformat"):
            result[k] = v.isoformat()
        elif k in ("form_data", "extra_data") and isinstance(v, str):
            try:
                result[k] = json.loads(v)
            except Exception:
                result[k] = v
        else:
            result[k] = v

    if "id" in result and result.get("procedure_code"):
        proc_code = str(result["procedure_code"]).strip().upper()
        rec_id_str = str(result["id"])
        short_id = rec_id_str[:8].upper() if len(rec_id_str) >= 8 else rec_id_str.upper()
        result["application_code"] = f"{proc_code}-{short_id}"
    return result


def _to_number(value, cast):
    try:
        return cast(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def get_procedure_id_by_code(conn_or_code, maybe_code: Optional[str] = None) -> Optional[str]:
    """Tìm ID thủ tục theo mã, dùng kết nối hiện có nếu được truyền vào."""
    if maybe_code is None:
        with db_connection() as conn:
            return get_procedure_id_by_code(conn, str(conn_or_code))

    row = conn_or_code.execute(
        text("SELECT id FROM public.procedures WHERE UPPER(code) = :code LIMIT 1"),
        {"code": maybe_code.strip().upper()},
    ).fetchone()
    return str(row[0]) if row else None


def save_procedure_record(
    procedure_code: str,
    user_id: Optional[str] = None,
    status: str = "draft",
    form_data: Optional[Dict[str, Any]] = None,
    extra_data: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Lưu hồ sơ vào land_procedure_records hoặc business_procedure_records."""
    code_upper = procedure_code.strip().upper()
    form_data = form_data or {}
    extra_data = extra_data or {}
    domain = "land" if code_upper.startswith("DD") else "business"
    table = "land_procedure_records" if domain == "land" else "business_procedure_records"

    user_uuid = safe_uuid_str(user_id) or (str(user_id) if user_id else None)
    now_dt = datetime.now(timezone.utc)
    values = {
        "id": str(uuid.uuid4()),
        "user_id": user_uuid,
        "procedure_code": code_upper,
        "status": status,
        "form_data": json.dumps(form_data, ensure_ascii=False),
        "extra_data": json.dumps(extra_data, ensure_ascii=False),
        "created_at": now_dt,
    }
    if domain == "land":
        values.update({
            "applicant_name": form_data.get("applicant_name"),
            "applicant_id_number": form_data.get("applicant_id_number"),
            "applicant_phone": form_data.get("applicant_phone"),
            "plot_number": None if form_data.get("plot_number") is None else str(form_data["plot_number"]),
            "map_sheet_number": None if form_data.get("map_sheet_number") is None else str(form_data["map_sheet_number"]),
            "land_address": form_data.get("land_address"),
            "land_area": _to_number(form_data.get("land_area"), float),
        })
    else:
        values.update({
            "applicant_name": form_data.get("applicant_name") or form_data.get("authorizer_name") or form_data.get("lessor_name") or form_data.get("authorized_person_name") or form_data.get("coop_unit_head_name"),
            "applicant_id_number": form_data.get("applicant_id_number") or form_data.get("authorizer_id_number") or form_data.get("authorized_person_id_number") or form_data.get("coop_unit_head_id_number"),
            "applicant_phone": form_data.get("applicant_phone") or form_data.get("headquarters_phone") or form_data.get("authorized_person_phone"),
            "business_type": form_data.get("business_type") or form_data.get("llc_type") or form_data.get("coop_unit_type"),
            "business_name_vn": form_data.get("business_name_vn") or form_data.get("coop_unit_name_vn"),
            "business_code": form_data.get("business_code"),
            "headquarters_address": form_data.get("headquarters_address") or form_data.get("rental_address") or form_data.get("coop_unit_address"),
            "charter_capital": _to_number(form_data.get("charter_capital"), int),
        })

    with db_connection(write=True) as conn:
        columns = ", ".join(values)
        placeholders = ", ".join(
            f"CAST(:{column} AS jsonb)" if conn.dialect.name == "postgresql" and column in ("form_data", "extra_data")
            else f":{column}" for column in values
        )
        row = conn.execute(
            text(
                f"INSERT INTO public.{table} ({columns}, procedure_id, updated_at) "
                f"SELECT {placeholders}, p.id, :created_at FROM public.procedures p "
                "WHERE upper(p.code) = :procedure_code "
                "RETURNING id, user_id, procedure_code, procedure_id, status, "
                "applicant_name, created_at, updated_at"
            ),
            values,
        ).mappings().first()
    return _record_result(row, domain, table) if row is not None else None


create_procedure_record = save_procedure_record


def _record_result(row, domain: str, table: str) -> dict:
    result = _serialize_row(dict(row))
    result["domain"] = domain
    result["target_table"] = table
    return result


def _record_table(domain: str) -> str:
    return "land_procedure_records" if domain == "land" else "business_procedure_records"


def _owner_filter(owner_id: Optional[str]) -> tuple[str, dict]:
    if owner_id is None:
        return "", {}
    return " AND user_id = :owner", {"owner": safe_uuid_str(owner_id) or str(owner_id)}


def get_procedure_record_by_id(
    domain: str, record_id: str, owner_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Lấy hồ sơ theo ID, giới hạn theo chủ sở hữu khi có owner_id."""
    rec_uuid = safe_uuid_str(record_id)
    if rec_uuid is None:
        return None
    clean_domain = domain.strip().lower()
    table = _record_table(clean_domain)
    owner_sql, params = _owner_filter(owner_id)
    params["id"] = rec_uuid
    with db_connection() as conn:
        row = conn.execute(
            text(f"SELECT * FROM public.{table} WHERE id = :id{owner_sql} LIMIT 1"),
            params,
        ).mappings().first()
    return _record_result(row, clean_domain, table) if row is not None else None


def lookup_procedure_record(
    code_or_id: str, owner_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Tra cứu theo UUID, tiền tố 8 ký tự hex hoặc mã hồ sơ ghép."""
    if not code_or_id or not str(code_or_id).strip():
        return None
    raw = str(code_or_id).strip()
    full_uuid = safe_uuid_str(raw)
    procedure_code = None
    id_part = raw

    business_prefixes = ("HKD", "DN", "HTX", "BHXH")
    if full_uuid is None:
        parts = raw.split("-", 2)
        if len(parts) == 3 and parts[0].upper().startswith(("DD", *business_prefixes)):
            procedure_code = f"{parts[0].upper()}-{parts[1].upper()}"
            id_part = parts[2]
            full_uuid = safe_uuid_str(id_part)

    clean_id = id_part.replace("-", "").strip().lower()
    if full_uuid is None and not HEX_8_PATTERN.fullmatch(clean_id):
        return None

    domains = ("land", "business")
    if procedure_code and procedure_code.startswith("DD"):
        domains = ("land",)
    elif procedure_code:
        domains = ("business",)
    elif raw.upper().startswith("DD"):
        domains = ("land",)
    elif raw.upper().startswith(business_prefixes):
        domains = ("business",)

    owner_sql, params = _owner_filter(owner_id)
    id_sql = "id = :id"
    if full_uuid is not None:
        params["id"] = full_uuid
    else:
        id_sql = "id >= :id_min AND id <= :id_max"
        params["id_min"] = f"{clean_id}-0000-0000-0000-000000000000"
        params["id_max"] = f"{clean_id}-ffff-ffff-ffff-ffffffffffff"

    code_sql = ""
    if procedure_code:
        code_sql = " AND upper(procedure_code) = :procedure_code"
        params["procedure_code"] = procedure_code

    with db_connection() as conn:
        for domain in domains:
            table = _record_table(domain)
            row = conn.execute(
                text(
                    f"SELECT * FROM public.{table} "
                    f"WHERE {id_sql}{owner_sql}{code_sql} ORDER BY created_at DESC LIMIT 1"
                ),
                params,
            ).mappings().first()
            if row is not None:
                return _record_result(row, domain, table)
    return None


def _mutation_target(domain: str, record_id: str, user_id: Optional[str]):
    rec_uuid = safe_uuid_str(record_id)
    if rec_uuid is None:
        raise ApplicationNotFoundError()
    clean_domain = domain.strip().lower()
    owner_sql, params = _owner_filter(user_id)
    params["id"] = rec_uuid
    return clean_domain, _record_table(clean_domain), f"id = :id{owner_sql}", params


def _read_owned_draft(conn, table: str, scope: str, params: dict) -> dict:
    lock_sql = " FOR UPDATE" if conn.dialect.name == "postgresql" else ""
    row = conn.execute(
        text(f"SELECT * FROM public.{table} WHERE {scope}{lock_sql}"),
        params,
    ).mappings().first()
    if row is None:
        raise ApplicationNotFoundError()
    if row["status"] != "draft":
        raise ApplicationLockedError()
    return _serialize_row(dict(row))


def _raise_write_miss(conn, table: str, scope: str, params: dict) -> None:
    row = conn.execute(
        text(f"SELECT status FROM public.{table} WHERE {scope}"),
        params,
    ).first()
    if row is None:
        raise ApplicationNotFoundError()
    raise ApplicationLockedError()


def update_procedure_record(
    domain: str,
    record_id: str,
    user_id: Optional[str] = None,
    form_data: Optional[Dict[str, Any]] = None,
    extra_data: Optional[Dict[str, Any]] = None,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """Gộp dữ liệu hồ sơ nháp và lưu trong cùng giao dịch."""
    if status and status.strip().lower() == "submitted":
        raise ApplicationValidationError(
            "Không thể chuyển trạng thái sang 'submitted' qua API cập nhật. "
            "Vui lòng sử dụng endpoint /submit."
        )

    clean_domain, table, scope, params = _mutation_target(domain, record_id, user_id)
    with db_connection(write=True) as conn:
        existing = _read_owned_draft(conn, table, scope, params)
        form = {**(existing.get("form_data") or {}), **(form_data or {})}
        extra = {**(existing.get("extra_data") or {}), **(extra_data or {})}
        values = {
            "form_data": json.dumps(form, ensure_ascii=False),
            "extra_data": json.dumps(extra, ensure_ascii=False),
            "status": status or "draft",
        }
        if clean_domain == "land":
            plot_number = form.get("plot_number")
            sheet_number = form.get("map_sheet_number")
            land_area = form.get("land_area")
            values.update({
                "applicant_name": form.get("applicant_name"),
                "applicant_id_number": form.get("applicant_id_number"),
                "applicant_phone": form.get("applicant_phone"),
                "plot_number": str(plot_number) if plot_number is not None else None,
                "map_sheet_number": str(sheet_number) if sheet_number is not None else None,
                "land_address": form.get("land_address"),
                "land_area": float(land_area) if land_area is not None else None,
            })
        else:
            charter_capital = None
            if form.get("charter_capital") is not None:
                try:
                    charter_capital = int(float(str(form["charter_capital"]).replace(",", "").strip()))
                except (ValueError, TypeError):
                    pass
            values.update({
                "applicant_name": form.get("applicant_name") or form.get("authorizer_name") or form.get("lessor_name"),
                "applicant_id_number": form.get("applicant_id_number") or form.get("authorizer_id_number") or form.get("lessor_id_number"),
                "applicant_phone": (
                    form.get("applicant_phone") or form.get("headquarters_phone")
                    or form.get("authorizer_phone") or form.get("lessor_phone")
                ),
                "business_type": form.get("business_type") or form.get("llc_type"),
                "business_name_vn": form.get("business_name_vn"),
                "business_code": form.get("business_code"),
                "headquarters_address": form.get("headquarters_address") or form.get("rental_address"),
                "charter_capital": charter_capital,
            })

        assignments = ", ".join(f"{column} = :{column}" for column in values)
        row = conn.execute(
            text(
                f"UPDATE public.{table} SET {assignments}, updated_at = CURRENT_TIMESTAMP "
                f"WHERE {scope} AND status = 'draft' RETURNING *"
            ),
            {**params, **values},
        ).mappings().first()
        if row is None:
            _raise_write_miss(conn, table, scope, params)
    return _record_result(row, clean_domain, table)


def delete_procedure_record(domain: str, record_id: str, user_id: Optional[str] = None) -> bool:
    """Xóa hồ sơ nháp thuộc phạm vi chủ sở hữu."""
    _, table, scope, params = _mutation_target(domain, record_id, user_id)
    with db_connection(write=True) as conn:
        result = conn.execute(
            text(f"DELETE FROM public.{table} WHERE {scope} AND status = 'draft'"),
            params,
        )
        if result.rowcount == 0:
            _raise_write_miss(conn, table, scope, params)
    return True


def submit_procedure_record(
    domain: str,
    record_id: str,
    user_id: Optional[str] = None,
    *,
    validate: Optional[Callable[[dict], None]] = None,
) -> Dict[str, Any]:
    """Kiểm tra và nộp hồ sơ nháp trong cùng giao dịch."""
    clean_domain, table, scope, params = _mutation_target(domain, record_id, user_id)
    with db_connection(write=True) as conn:
        if validate is not None:
            validate(_read_owned_draft(conn, table, scope, params))
        row = conn.execute(
            text(
                f"UPDATE public.{table} SET status = 'submitted', updated_at = CURRENT_TIMESTAMP "
                f"WHERE {scope} AND status = 'draft' RETURNING *"
            ),
            params,
        ).mappings().first()
        if row is None:
            _raise_write_miss(conn, table, scope, params)
    return _record_result(row, clean_domain, table)


_LIST_SOURCE = """
    SELECT r.id, 'land' AS domain, r.user_id, r.procedure_code, p.name AS procedure_name, r.status,
           r.applicant_name, r.applicant_id_number, r.land_address AS address,
           p.fee, p.receiving_agency_level, p.processing_days, r.created_at
    FROM public.land_procedure_records r LEFT JOIN public.procedures p ON p.id = r.procedure_id
    UNION ALL
    SELECT r.id, 'business' AS domain, r.user_id, r.procedure_code, p.name AS procedure_name, r.status,
           r.applicant_name, r.applicant_id_number, r.headquarters_address AS address,
           p.fee, p.receiving_agency_level, p.processing_days, r.created_at
    FROM public.business_procedure_records r LEFT JOIN public.procedures p ON p.id = r.procedure_id
"""

STATUSES = ("draft", "submitted", "processing", "completed", "cancelled")


def _as_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return datetime.now(timezone.utc)


def _list_item(row: dict, include_owner: bool) -> dict:
    created_at = _as_datetime(row["created_at"])
    days = row.get("processing_days")
    due_date = (created_at + timedelta(days=days)).date().isoformat() if days is not None else None
    proc_code = str(row.get("procedure_code") or "").strip().upper()
    rec_id_str = str(row.get("id") or "")
    short_id = rec_id_str[:8].upper() if len(rec_id_str) >= 8 else rec_id_str.upper()
    app_code = f"{proc_code}-{short_id}" if proc_code and short_id else rec_id_str

    item = {
        "id": rec_id_str,
        "application_code": app_code,
        "domain": row.get("domain"),
        "procedure_code": proc_code,
        "procedure_name": row.get("procedure_name"),
        "status": row.get("status"),
        "applicant_name": row.get("applicant_name"),
        "applicant_id_number": row.get("applicant_id_number"),
        "address": row.get("address"),
        "fee": row.get("fee"),
        "receiving_agency_level": row.get("receiving_agency_level"),
        "created_at": created_at.isoformat(),
        "due_date": due_date,
    }
    if include_owner:
        item["user_id"] = str(row["user_id"]) if row.get("user_id") is not None else None
    return item


def list_records(
    owner_id: Optional[str],
    domain: Optional[str],
    status: Optional[str],
    q: Optional[str],
    page: int,
    page_size: int,
) -> tuple[list[dict], int]:
    """Lấy hồ sơ của cả hai lĩnh vực, mới nhất trước."""
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    where = []
    params = {"limit": page_size, "offset": (page - 1) * page_size}
    if owner_id is not None:
        where.append("user_id = :owner")
        params["owner"] = safe_uuid_str(owner_id) or str(owner_id)
    if domain:
        where.append("domain = :domain")
        params["domain"] = domain
    if status:
        where.append("status = :status")
        params["status"] = status
    if q and q.strip():
        query = q.strip().lower()
        params["q"] = f"%{query}%"
        matches = [
            "lower(coalesce(applicant_name, '')) LIKE :q",
            "coalesce(applicant_id_number, '') LIKE :q",
            "lower(procedure_code) LIKE :q",
        ]
        query_uuid = safe_uuid_str(query)
        if query_uuid:
            matches.append("id = :q_uuid")
            params["q_uuid"] = query_uuid
        elif HEX_8_PATTERN.fullmatch(query):
            matches.append("(id >= :q_id_min AND id <= :q_id_max)")
            params["q_id_min"] = f"{query}-0000-0000-0000-000000000000"
            params["q_id_max"] = f"{query}-ffff-ffff-ffff-ffffffffffff"
        else:
            matches.append("lower(cast(id as text)) LIKE :q")

        parts = query.split("-")
        if len(parts) >= 3:
            params["p_code_exact"] = f"{parts[0]}-{parts[1]}".upper()
            short_id = parts[2]
            if HEX_8_PATTERN.fullmatch(short_id):
                matches.append(
                    "(upper(procedure_code) = :p_code_exact AND id >= :short_id_min AND id <= :short_id_max)"
                )
                params["short_id_min"] = f"{short_id}-0000-0000-0000-000000000000"
                params["short_id_max"] = f"{short_id}-ffff-ffff-ffff-ffffffffffff"
            else:
                matches.append("(upper(procedure_code) = :p_code_exact AND lower(cast(id as text)) LIKE :short_id_like)")
                params["short_id_like"] = f"{short_id}%"
        where.append(f"({' OR '.join(matches)})")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    with db_connection() as conn:
        rows = conn.execute(
            text(
                f"WITH filtered AS (SELECT * FROM ({_LIST_SOURCE}) a {where_sql}), "
                "page AS (SELECT * FROM filtered ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset) "
                "SELECT page.*, totals.total FROM (SELECT count(*) AS total FROM filtered) totals "
                "LEFT JOIN page ON 1=1 ORDER BY page.created_at DESC, page.id DESC"
            ),
            params,
        ).mappings().all()
    total = rows[0]["total"]
    return [_list_item(dict(row), include_owner=owner_id is None) for row in rows if row["id"] is not None], total


def count_records() -> dict:
    """Đếm hồ sơ theo lĩnh vực và trạng thái cho dashboard admin."""
    by_domain = {"land": 0, "business": 0}
    by_status = dict.fromkeys(STATUSES, 0)
    with db_connection() as conn:
        rows = conn.execute(text("""
            SELECT 'land' AS domain, status, count(*) AS n
            FROM public.land_procedure_records GROUP BY status
            UNION ALL
            SELECT 'business' AS domain, status, count(*) AS n
            FROM public.business_procedure_records GROUP BY status
        """)).all()
    for domain, status, count in rows:
        by_domain[domain] += count
        if status in by_status:
            by_status[status] += count
    return {"total": sum(by_domain.values()), "by_domain": by_domain, "by_status": by_status}
