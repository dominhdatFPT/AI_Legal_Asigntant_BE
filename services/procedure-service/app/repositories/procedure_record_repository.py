import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import text

from app.repositories.procedure_repository import db_connection

RECORD_TABLES = {"land": "land_procedure_records", "business": "business_procedure_records"}

LAND_COLUMNS = ["plot_number", "map_sheet_number", "land_address", "land_area"]
BUSINESS_COLUMNS = ["business_type", "business_name_vn", "business_code", "headquarters_address", "charter_capital"]


def _serialize_row(row: dict) -> dict:
    """UUID, datetime → chuỗi; form_data/extra_data (SQLite trả text) → dict."""
    result = {}
    for k, v in row.items():
        if isinstance(v, UUID):
            v = str(v)
        elif hasattr(v, "isoformat"):
            v = v.isoformat()
        elif k in ("form_data", "extra_data") and isinstance(v, str):
            v = json.loads(v)
        result[k] = v
    return result


def _to_number(value, cast):
    try:
        return cast(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def get_procedure_id_by_code(conn, procedure_code: str) -> Optional[str]:
    row = conn.execute(text("SELECT id FROM public.procedures WHERE upper(code) = :code LIMIT 1"),
                       {"code": procedure_code.strip().upper()}).fetchone()
    return str(row[0]) if row else None


def save_procedure_record(
    procedure_code: str,
    user_id: str,
    status: str = "draft",
    form_data: Optional[Dict[str, Any]] = None,
    extra_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Lưu hồ sơ vào land_procedure_records (mã DD-...) hoặc business_procedure_records.
    Trả None khi mã thủ tục không có trong bảng procedures."""
    code_upper = procedure_code.strip().upper()
    form_data = form_data or {}
    domain = "land" if code_upper.startswith("DD") else "business"
    table = RECORD_TABLES[domain]

    values = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "procedure_code": code_upper,
        "status": status,
        "form_data": json.dumps(form_data, ensure_ascii=False),
        "extra_data": json.dumps(extra_data or {}, ensure_ascii=False),
        "created_at": datetime.now(timezone.utc),
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
            "applicant_name": form_data.get("applicant_name") or form_data.get("authorizer_name") or form_data.get("lessor_name"),
            "applicant_id_number": form_data.get("applicant_id_number") or form_data.get("authorizer_id_number"),
            "applicant_phone": form_data.get("applicant_phone") or form_data.get("headquarters_phone"),
            "business_type": form_data.get("business_type") or form_data.get("llc_type"),
            "business_name_vn": form_data.get("business_name_vn"),
            "business_code": form_data.get("business_code"),
            "headquarters_address": form_data.get("headquarters_address") or form_data.get("rental_address"),
            "charter_capital": _to_number(form_data.get("charter_capital"), int),
        })

    with db_connection(write=True) as conn:
        values["procedure_id"] = get_procedure_id_by_code(conn, code_upper)
        if values["procedure_id"] is None:
            return None
        columns = ", ".join(values)
        placeholders = ", ".join(f":{k}" for k in values)
        # form_data/extra_data gửi dạng chuỗi JSON; Postgres tự ép sang jsonb khi INSERT.
        row = conn.execute(
            text(f"INSERT INTO public.{table} ({columns}, updated_at) VALUES ({placeholders}, :created_at) "
                 "RETURNING id, user_id, procedure_code, procedure_id, status, applicant_name, created_at, updated_at"),
            values,
        ).mappings().fetchone()
    result = _serialize_row(dict(row))
    result["domain"] = domain
    result["target_table"] = table
    return result


def get_procedure_record_by_id(domain: str, record_id: str, owner_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Chi tiết hồ sơ theo lĩnh vực và ID. owner_id=None (chỉ admin) thì không lọc chủ."""
    clean_domain = domain.strip().lower()
    table = RECORD_TABLES[clean_domain]
    try:
        rec_uuid = str(UUID(record_id))
    except ValueError:
        return None
    owner_sql = "" if owner_id is None else " AND user_id = :owner_id"
    with db_connection() as conn:
        row = conn.execute(text(f"SELECT * FROM public.{table} WHERE id = :id{owner_sql} LIMIT 1"),
                           {"id": rec_uuid, "owner_id": owner_id}).mappings().fetchone()
    if not row:
        return None
    res = _serialize_row(dict(row))
    res["domain"] = clean_domain
    res["target_table"] = table
    return res


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
    # Postgres trả datetime; SQLite (test) trả chuỗi.
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def _list_item(row: dict, include_owner: bool) -> dict:
    created_at = _as_datetime(row["created_at"])
    days = row.get("processing_days")
    # shortcut: hạn trả tính theo ngày lịch; khi cần trừ ngày nghỉ/lễ thì thay bằng lịch làm việc.
    due_date = (created_at + timedelta(days=days)).date().isoformat() if days is not None else None
    item = {
        "id": str(row["id"]),
        "domain": row["domain"],
        "procedure_code": row["procedure_code"],
        "procedure_name": row.get("procedure_name"),
        "status": row["status"],
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


def list_records(owner_id: Optional[str], domain: Optional[str], status: Optional[str], q: Optional[str],
                 page: int, page_size: int) -> tuple[list[dict], int]:
    """Hồ sơ của cả hai bảng, mới nhất trước. owner_id=None (chỉ admin) thì lấy của mọi người."""
    where, params = [], {"limit": page_size, "offset": (page - 1) * page_size}
    if owner_id is not None:
        where.append("user_id = :owner_id")
        params["owner_id"] = owner_id
    if domain:
        where.append("domain = :domain")
        params["domain"] = domain
    if status:
        where.append("status = :status")
        params["status"] = status
    if q and q.strip():
        where.append("(lower(coalesce(applicant_name, '')) LIKE :q OR coalesce(applicant_id_number, '') LIKE :q "
                     "OR lower(procedure_code) LIKE :q)")
        params["q"] = f"%{q.strip().lower()}%"
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    with db_connection() as conn:
        total = conn.execute(text(f"SELECT count(*) FROM ({_LIST_SOURCE}) a {where_sql}"), params).scalar() or 0
        rows = conn.execute(text(f"SELECT * FROM ({_LIST_SOURCE}) a {where_sql} "
                                 "ORDER BY created_at DESC LIMIT :limit OFFSET :offset"), params).mappings().all()
    return [_list_item(dict(r), include_owner=owner_id is None) for r in rows], total


def count_records() -> dict:
    """Số hồ sơ theo lĩnh vực và trạng thái (cho dashboard admin)."""
    with db_connection() as conn:
        rows = conn.execute(text(f"SELECT domain, status, count(*) AS n FROM ({_LIST_SOURCE}) a GROUP BY domain, status")).all()
    by_domain = {"land": 0, "business": 0}
    by_status = dict.fromkeys(STATUSES, 0)
    for domain, status, n in rows:
        by_domain[domain] += n
        by_status[status] += n
    return {"total": sum(by_domain.values()), "by_domain": by_domain, "by_status": by_status}
