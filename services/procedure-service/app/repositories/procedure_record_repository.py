import copy
import time
import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import uuid
from uuid import UUID

import requests
from requests.adapters import HTTPAdapter
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, DataError, OperationalError

from app.core.exceptions import (
    ApplicationLockedError,
    ApplicationNotFoundError,
    ApplicationPermissionError,
    ApplicationValidationError,
)
from app.database import get_engine, get_supabase_config, is_postgres_circuit_open, mark_postgres_failed
from app.repositories.procedure_repository import db_connection, ProcedureInvalidData, ProcedureDbUnavailable

logger = logging.getLogger(__name__)

# Pattern chuẩn cho 8 ký tự hex mã định danh rút gọn
HEX_8_PATTERN = re.compile(r"^[0-9a-fA-F]{8}$")

# Reusable in-memory cache cho tra cứu hồ sơ (TTL 60s)
_lookup_cache: dict[str, tuple[float, any]] = {}
LOOKUP_CACHE_TTL = 60.0

# HTTP Session riêng cho từng thread (requests.Session không an toàn khi dùng chung giữa các thread), có Connection Pool chống socket exhaustion (M5)
_local = threading.local()


def _session():
    s = getattr(_local, "session", None)
    if s is None:
        s = _local.session = requests.Session()
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=1)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
    return s


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


def _supabase_headers() -> dict:
    """Header xác thực kết nối Supabase PostgREST"""
    _, anon_key = get_supabase_config()
    return {
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _to_number(value, cast):
    try:
        return cast(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def get_procedure_id_by_code(conn_or_code, maybe_code: Optional[str] = None) -> Optional[str]:
    """Tìm UUID của procedure dựa vào code (hỗ trợ cả PostgreSQL, SQLite và fallback PostgREST)"""
    if maybe_code is None:
        code = str(conn_or_code).strip().upper()
        if not is_postgres_circuit_open():
            try:
                with db_connection() as conn:
                    row = conn.execute(
                        text("SELECT id FROM public.procedures WHERE UPPER(code) = :code LIMIT 1"),
                        {"code": code},
                    ).fetchone()
                    if row:
                        return str(row[0])
            except Exception:
                pass
        # Fallback PostgREST
        try:
            supabase_url, _ = get_supabase_config()
            if supabase_url:
                r = _session().get(f"{supabase_url}/rest/v1/procedures?code=eq.{code}&select=id&limit=1", headers=_supabase_headers(), timeout=3)
                if r.status_code == 200 and r.json():
                    return str(r.json()[0]["id"])
        except Exception:
            pass
        return None
    else:
        conn = conn_or_code
        code = str(maybe_code).strip().upper()
        row = conn.execute(
            text("SELECT id FROM public.procedures WHERE UPPER(code) = :code LIMIT 1"),
            {"code": code},
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
            "applicant_name": form_data.get("applicant_name") or form_data.get("authorizer_name") or form_data.get("lessor_name"),
            "applicant_id_number": form_data.get("applicant_id_number") or form_data.get("authorizer_id_number"),
            "applicant_phone": form_data.get("applicant_phone") or form_data.get("headquarters_phone"),
            "business_type": form_data.get("business_type") or form_data.get("llc_type"),
            "business_name_vn": form_data.get("business_name_vn"),
            "business_code": form_data.get("business_code"),
            "headquarters_address": form_data.get("headquarters_address") or form_data.get("rental_address"),
            "charter_capital": _to_number(form_data.get("charter_capital"), int),
        })

    # 1. Thử qua SQLAlchemy
    if not is_postgres_circuit_open():
        try:
            with db_connection(write=True) as conn:
                proc_id = get_procedure_id_by_code(conn, code_upper)
                if proc_id is None:
                    return None
                values["procedure_id"] = proc_id
                columns = ", ".join(values)
                placeholders = ", ".join(f":{k}" for k in values)
                row = conn.execute(
                    text(f"INSERT INTO public.{table} ({columns}, updated_at) VALUES ({placeholders}, :created_at) "
                         "RETURNING id, user_id, procedure_code, procedure_id, status, applicant_name, created_at, updated_at"),
                    values,
                ).mappings().fetchone()
            result = _serialize_row(dict(row))
            result["domain"] = domain
            result["target_table"] = table
            return result
        except Exception as e:
            logger.warning(f"Lỗi khi lưu hồ sơ qua SQLAlchemy: {e}. Thử fallback PostgREST...")

    # 2. Fallback qua Supabase PostgREST
    try:
        supabase_url, _ = get_supabase_config()
        if supabase_url:
            proc_id = get_procedure_id_by_code(code_upper)
            if proc_id:
                values["procedure_id"] = proc_id
            pg_values = dict(values)
            pg_values["created_at"] = now_dt.isoformat()
            pg_values["updated_at"] = now_dt.isoformat()
            pg_values["form_data"] = form_data
            pg_values["extra_data"] = extra_data
            r = _session().post(
                f"{supabase_url}/rest/v1/{table}",
                headers=_supabase_headers(),
                json=pg_values,
                timeout=8,
            )
            if r.status_code in (200, 201):
                rows = r.json()
                if rows:
                    res = _serialize_row(rows[0])
                    res["domain"] = domain
                    res["target_table"] = table
                    return res
    except Exception as e:
        logger.warning(f"Lỗi fallback PostgREST trong save_procedure_record: {e}")

    return None


create_procedure_record = save_procedure_record


def get_procedure_record_by_id(domain: str, record_id: str, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Chi tiết hồ sơ theo lĩnh vực và ID. owner_id=None (chỉ admin) thì không lọc chủ."""
    clean_domain = domain.strip().lower()
    table = "land_procedure_records" if clean_domain == "land" else "business_procedure_records"
    rec_uuid = safe_uuid_str(record_id)
    if not rec_uuid:
        return None
    params = {"id": rec_uuid}
    owner_sql = ""
    if owner_id is not None:
        owner_sql = " AND user_id = :owner_id"
        params["owner_id"] = str(owner_id)

    if not is_postgres_circuit_open():
        try:
            with db_connection() as conn:
                row = conn.execute(
                    text(f"SELECT * FROM public.{table} WHERE id = :id{owner_sql} LIMIT 1"),
                    params,
                ).mappings().fetchone()
                if row:
                    res = _serialize_row(dict(row))
                    res["domain"] = clean_domain
                    res["target_table"] = table
                    return res
        except Exception as e:
            logger.warning(f"Lỗi khi lấy chi tiết hồ sơ qua SQLAlchemy: {e}. Thử fallback PostgREST...")

    # Fallback PostgREST
    try:
        supabase_url, _ = get_supabase_config()
        if supabase_url:
            p = {"id": f"eq.{rec_uuid}", "limit": 1}
            if owner_id is not None:
                p["user_id"] = f"eq.{owner_id}"
            r = _session().get(f"{supabase_url}/rest/v1/{table}", headers=_supabase_headers(), params=p, timeout=5)
            if r.status_code == 200:
                rows = r.json()
                if rows:
                    res = _serialize_row(rows[0])
                    res["domain"] = clean_domain
                    res["target_table"] = table
                    return res
    except Exception as e:
        logger.warning(f"Lỗi fallback PostgREST trong get_procedure_record_by_id: {e}")

    return None


def lookup_procedure_record(code_or_id: str, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Tra cứu chi tiết hồ sơ bằng:
    - Mã hồ sơ chuẩn (ví dụ: DD-001-811F7E21)
    - ID prefix 8 ký tự hex (811F7E21)
    - Hoặc full UUID (36 ký tự).
    
    BẮT BUỘC ĐÚNG ĐỊNH DẠNG:
    Nếu gõ thiếu (ví dụ 6 ký tự như 811F7E hoặc DD-001-811F7E), trả về None ngay lập tức để API trả lỗi 404 APPLICATION_NOT_FOUND,
    không tự động tìm khớp mờ.
    owner_id=None (chỉ admin) thì không lọc theo người tạo.
    """
    if not code_or_id or not str(code_or_id).strip():
        return None

    raw = str(code_or_id).strip()
    full_uuid = safe_uuid_str(raw)
    procedure_code_filter = None
    id_part = raw

    if not full_uuid and "-" in raw:
        parts = raw.split("-")
        if len(parts) >= 3 and (parts[0].upper().startswith("DD") or parts[0].upper().startswith("HKD") or parts[0].upper().startswith("DN")):
            procedure_code_filter = f"{parts[0].upper()}-{parts[1].upper()}"
            id_part = "-".join(parts[2:])
            full_uuid = safe_uuid_str(id_part)

    clean_id = id_part.replace("-", "").strip().lower()

    # YÊU CẦU: Nếu không phải full UUID 36 ký tự, thì clean_id BẮT BUỘC phải là đúng 8 ký tự hex.
    # Nếu gõ thiếu/không đủ 8 ký tự hex -> trả về None ngay lập tức!
    if not full_uuid:
        if not HEX_8_PATTERN.match(clean_id):
            return None

    cache_key = f"lookup:{owner_id}:{raw.upper()}"
    now = time.time()
    if cache_key in _lookup_cache:
        c_time, c_data = _lookup_cache[cache_key]
        if now - c_time < LOOKUP_CACHE_TTL:
            return copy.deepcopy(c_data)

    domains = ["land", "business"]
    if procedure_code_filter:
        domains = ["land"] if procedure_code_filter.startswith("DD") else ["business"]
    elif raw.upper().startswith("DD"):
        domains = ["land"]
    elif raw.upper().startswith("HKD") or raw.upper().startswith("DN"):
        domains = ["business"]

    owner_sql = " AND user_id = :owner_id" if owner_id is not None else ""
    base_params: Dict[str, Any] = {"owner_id": str(owner_id)} if owner_id is not None else {}

    # 1. Thử qua SQLAlchemy
    if not is_postgres_circuit_open():
        try:
            with db_connection() as conn:
                for dom in domains:
                    table = "land_procedure_records" if dom == "land" else "business_procedure_records"

                    if full_uuid:
                        p = dict(base_params, id=full_uuid)
                        p_sql = " AND upper(procedure_code) = :p_code" if procedure_code_filter else ""
                        if procedure_code_filter:
                            p["p_code"] = procedure_code_filter
                        row = conn.execute(
                            text(f"SELECT * FROM public.{table} WHERE id = :id{p_sql}{owner_sql} LIMIT 1"),
                            p,
                        ).mappings().fetchone()
                        if row:
                            res = _serialize_row(dict(row))
                            res["domain"] = dom
                            res["target_table"] = table
                            return res
                    else:
                        # clean_id đúng 8 ký tự hex: dùng B-Tree index scan trên UUID range (0.1ms)
                        p = dict(
                            base_params,
                            id_min=f"{clean_id}-0000-0000-0000-000000000000",
                            id_max=f"{clean_id}-ffff-ffff-ffff-ffffffffffff",
                        )
                        p_sql = " AND upper(procedure_code) = :p_code" if procedure_code_filter else ""
                        if procedure_code_filter:
                            p["p_code"] = procedure_code_filter
                        row = conn.execute(
                            text(f"SELECT * FROM public.{table} WHERE id >= :id_min AND id <= :id_max{p_sql}{owner_sql} ORDER BY created_at DESC LIMIT 1"),
                            p,
                        ).mappings().fetchone()
                        if row:
                            res = _serialize_row(dict(row))
                            res["domain"] = dom
                            res["target_table"] = table
                            return res
        except Exception as e:
            logger.warning(f"Lỗi khi tra cứu hồ sơ qua SQLAlchemy: {e}. Thử fallback PostgREST...")
            mark_postgres_failed(60.0)

    # 2. Fallback qua Supabase PostgREST
    try:
        supabase_url, _ = get_supabase_config()
        if supabase_url:
            for dom in domains:
                table = "land_procedure_records" if dom == "land" else "business_procedure_records"
                p = {"select": "*", "limit": 1}
                if owner_id is not None:
                    p["user_id"] = f"eq.{owner_id}"

                if full_uuid:
                    p["id"] = f"eq.{full_uuid}"
                    if procedure_code_filter:
                        p["procedure_code"] = f"eq.{procedure_code_filter}"
                else:
                    # clean_id đúng 8 ký tự hex -> dùng phạm vi trên UUID index
                    p["id"] = f"gte.{clean_id}-0000-0000-0000-000000000000"
                    p["and"] = f"(id.lte.{clean_id}-ffff-ffff-ffff-ffffffffffff)"
                    if procedure_code_filter:
                        p["procedure_code"] = f"eq.{procedure_code_filter}"
                    p["order"] = "created_at.desc"

                r = _session().get(f"{supabase_url}/rest/v1/{table}", headers=_supabase_headers(), params=p, timeout=5)
                if r.status_code == 200:
                    rows = r.json()
                    if rows and len(rows) > 0:
                        res = _serialize_row(rows[0])
                        res["domain"] = dom
                        res["target_table"] = table
                        return res
    except Exception as e:
        logger.warning(f"Lỗi fallback PostgREST trong lookup_procedure_record: {e}")

    return None


def list_procedure_records(
    user_id: Optional[str] = None,
    domain: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None,
) -> tuple[List[Dict[str, Any]], int]:
    """Lấy danh sách hồ sơ người dùng có phân trang và bộ lọc"""
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    offset = (page - 1) * page_size
    user_uuid = None
    if user_id:
        try:
            user_uuid = str(UUID(user_id))
        except ValueError:
            user_uuid = str(user_id)

    clean_domain = domain.strip().lower() if domain else None
    if clean_domain not in ("land", "business"):
        clean_domain = None

    # 1. Thử qua SQLAlchemy
    if not is_postgres_circuit_open():
        engine = get_engine()
        if engine:
            try:
                with engine.begin() as conn:
                    queries = []
                    params = {"limit": page_size, "offset": offset}
                    
                    user_filter = " AND user_id = :user_id" if user_uuid else ""
                    if user_uuid:
                        params["user_id"] = user_uuid
                        
                    status_filter = " AND status = :status" if status else ""
                    if status:
                        params["status"] = status
                        
                    search_filter_land = ""
                    search_filter_biz = ""
                    if search:
                        params["search"] = f"%{search.strip()}%"
                        search_filter_land = " AND (applicant_name ILIKE :search OR procedure_code ILIKE :search OR land_address ILIKE :search)"
                        search_filter_biz = " AND (applicant_name ILIKE :search OR procedure_code ILIKE :search OR business_name_vn ILIKE :search)"

                    if clean_domain == "land" or clean_domain is None:
                        queries.append(f"""
                            SELECT id, user_id, procedure_code, procedure_id, status, applicant_name, created_at, updated_at, form_data, extra_data, 'land' as domain, 'land_procedure_records' as target_table
                            FROM public.land_procedure_records
                            WHERE 1=1 {user_filter} {status_filter} {search_filter_land}
                        """)

                    if clean_domain == "business" or clean_domain is None:
                        queries.append(f"""
                            SELECT id, user_id, procedure_code, procedure_id, status, applicant_name, created_at, updated_at, form_data, extra_data, 'business' as domain, 'business_procedure_records' as target_table
                            FROM public.business_procedure_records
                            WHERE 1=1 {user_filter} {status_filter} {search_filter_biz}
                        """)

                    full_query = " UNION ALL ".join(queries)
                    count_sql = text(f"SELECT count(*) FROM ({full_query}) as total_records")
                    total = conn.execute(count_sql, params).scalar() or 0

                    data_sql = text(f"SELECT * FROM ({full_query}) as records ORDER BY updated_at DESC LIMIT :limit OFFSET :offset")
                    rows = conn.execute(data_sql, params).mappings().all()
                    items = [_serialize_row(dict(r)) for r in rows]
                    return items, total
            except OperationalError as e:
                mark_postgres_failed()
                logger.warning(f"Lỗi kết nối PostgreSQL khi lấy danh sách hồ sơ: {e}. Thử fallback PostgREST...")
            except Exception as e:
                logger.warning(f"Lỗi khi lấy danh sách hồ sơ qua SQLAlchemy: {e}. Thử fallback PostgREST...")

    # 2. Fallback qua Supabase PostgREST
    try:
        supabase_url, _ = get_supabase_config()
        if supabase_url:
            all_records = []
            tables = []
            if clean_domain == "land" or clean_domain is None:
                tables.append(("land", "land_procedure_records"))
            if clean_domain == "business" or clean_domain is None:
                tables.append(("business", "business_procedure_records"))

            for d_name, tbl in tables:
                p = {"select": "*", "order": "updated_at.desc", "limit": 100}
                if user_uuid:
                    p["user_id"] = f"eq.{user_uuid}"
                if status:
                    p["status"] = f"eq.{status}"
                if search:
                    p["applicant_name"] = f"ilike.*{search.strip()}*"

                r = _session().get(f"{supabase_url}/rest/v1/{tbl}", headers=_supabase_headers(), params=p, timeout=8)
                if r.status_code == 200:
                    for item in r.json():
                        row = _serialize_row(item)
                        row["domain"] = d_name
                        row["target_table"] = tbl
                        all_records.append(row)

            all_records.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
            total = len(all_records)
            paginated = all_records[offset : offset + page_size]
            return paginated, total
    except Exception as e:
        logger.warning(f"Lỗi khi lấy danh sách hồ sơ qua PostgREST: {e}")

    return [], 0


def update_procedure_record(
    domain: str,
    record_id: str,
    user_id: Optional[str] = None,
    form_data: Optional[Dict[str, Any]] = None,
    extra_data: Optional[Dict[str, Any]] = None,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """Cập nhật dữ liệu bản nháp hồ sơ người dùng"""
    clean_domain = domain.strip().lower()
    table = "land_procedure_records" if clean_domain == "land" else "business_procedure_records"
    existing = get_procedure_record_by_id(clean_domain, record_id)
    if not existing:
        raise ApplicationNotFoundError("Không tìm thấy hồ sơ yêu cầu.")

    # Kiểm tra quyền sở hữu chặt chẽ (H2)
    owner_id = existing.get("user_id")
    if owner_id:
        if not user_id or str(owner_id) != str(user_id):
            raise ApplicationPermissionError("FORBIDDEN: Bạn không có quyền chỉnh sửa hồ sơ này.")

    # Chỉ cho phép sửa khi hồ sơ ở trạng thái nháp
    current_status = existing.get("status") or "draft"
    if current_status not in ("draft", None):
        raise ApplicationLockedError(f"Hồ sơ đang ở trạng thái '{current_status}', không thể chỉnh sửa.")

    # Không cho phép chuyển sang submitted qua API cập nhật (C6)
    if status and status.strip().lower() == "submitted":
        raise ApplicationValidationError("Không thể chuyển trạng thái sang 'submitted' qua API cập nhật. Vui lòng sử dụng endpoint /submit.")

    # Merge dữ liệu
    merged_form = {**(existing.get("form_data") or {}), **(form_data or {})}
    merged_extra = {**(existing.get("extra_data") or {}), **(extra_data or {})}
    new_status = status or current_status

    # 1. Thử qua SQLAlchemy
    if not is_postgres_circuit_open():
        engine = get_engine()
        if engine:
            try:
                with engine.begin() as conn:
                    if clean_domain == "land":
                        applicant_name = merged_form.get("applicant_name")
                        applicant_id_number = merged_form.get("applicant_id_number")
                        applicant_phone = merged_form.get("applicant_phone")
                        plot_number = str(merged_form.get("plot_number")) if merged_form.get("plot_number") is not None else None
                        map_sheet_number = str(merged_form.get("map_sheet_number")) if merged_form.get("map_sheet_number") is not None else None
                        land_address = merged_form.get("land_address")
                        land_area = float(merged_form["land_area"]) if merged_form.get("land_area") is not None else None

                        sql = text("""
                            UPDATE public.land_procedure_records
                            SET applicant_name = :applicant_name,
                                applicant_id_number = :applicant_id_number,
                                applicant_phone = :applicant_phone,
                                plot_number = :plot_number,
                                map_sheet_number = :map_sheet_number,
                                land_address = :land_address,
                                land_area = :land_area,
                                form_data = :form_data,
                                extra_data = :extra_data,
                                status = :status,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = :id
                            RETURNING *
                        """)
                        params = {
                            "id": safe_uuid_str(record_id) or str(record_id),
                            "applicant_name": applicant_name,
                            "applicant_id_number": applicant_id_number,
                            "applicant_phone": applicant_phone,
                            "plot_number": plot_number,
                            "map_sheet_number": map_sheet_number,
                            "land_address": land_address,
                            "land_area": land_area,
                            "form_data": json.dumps(merged_form, ensure_ascii=False),
                            "extra_data": json.dumps(merged_extra, ensure_ascii=False),
                            "status": new_status,
                        }
                    else:
                        applicant_name = merged_form.get("applicant_name") or merged_form.get("authorizer_name") or merged_form.get("lessor_name")
                        applicant_id_number = merged_form.get("applicant_id_number") or merged_form.get("authorizer_id_number") or merged_form.get("lessor_id_number")
                        applicant_phone = merged_form.get("applicant_phone") or merged_form.get("headquarters_phone") or merged_form.get("authorizer_phone") or merged_form.get("lessor_phone")
                        business_type = merged_form.get("business_type") or merged_form.get("llc_type")
                        business_name_vn = merged_form.get("business_name_vn")
                        business_code = merged_form.get("business_code")
                        headquarters_address = merged_form.get("headquarters_address") or merged_form.get("rental_address")
                        
                        charter_capital = None
                        if merged_form.get("charter_capital") is not None:
                            try:
                                charter_capital = int(float(str(merged_form["charter_capital"]).replace(",", "").strip()))
                            except (ValueError, TypeError):
                                charter_capital = None

                        sql = text("""
                            UPDATE public.business_procedure_records
                            SET applicant_name = :applicant_name,
                                applicant_id_number = :applicant_id_number,
                                applicant_phone = :applicant_phone,
                                business_type = :business_type,
                                business_name_vn = :business_name_vn,
                                business_code = :business_code,
                                headquarters_address = :headquarters_address,
                                charter_capital = :charter_capital,
                                form_data = :form_data,
                                extra_data = :extra_data,
                                status = :status,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = :id
                            RETURNING *
                        """)
                        params = {
                            "id": safe_uuid_str(record_id) or str(record_id),
                            "applicant_name": applicant_name,
                            "applicant_id_number": applicant_id_number,
                            "applicant_phone": applicant_phone,
                            "business_type": business_type,
                            "business_name_vn": business_name_vn,
                            "business_code": business_code,
                            "headquarters_address": headquarters_address,
                            "charter_capital": charter_capital,
                            "form_data": json.dumps(merged_form, ensure_ascii=False),
                            "extra_data": json.dumps(merged_extra, ensure_ascii=False),
                            "status": new_status,
                        }

                    row = conn.execute(sql, params).mappings().fetchone()
                    if row:
                        res = _serialize_row(dict(row))
                        res["domain"] = clean_domain
                        res["target_table"] = table
                        return res
            except OperationalError as e:
                mark_postgres_failed()
                logger.warning(f"Lỗi kết nối PostgreSQL khi cập nhật hồ sơ {record_id}: {e}")
            except Exception as e:
                logger.warning(f"Lỗi khi cập nhật hồ sơ {record_id} qua SQLAlchemy: {e}. Thử fallback PostgREST...")

    # 2. Fallback qua Supabase PostgREST
    supabase_url, _ = get_supabase_config()
    if not supabase_url:
        raise RuntimeError("Không có kết nối CSDL hoặc cấu hình SUPABASE_URL.")

    patch_payload = {
        "form_data": merged_form,
        "extra_data": merged_extra,
        "status": new_status,
    }
    if clean_domain == "land":
        if "applicant_name" in merged_form: patch_payload["applicant_name"] = merged_form["applicant_name"]
        if "applicant_id_number" in merged_form: patch_payload["applicant_id_number"] = merged_form["applicant_id_number"]
        if "applicant_phone" in merged_form: patch_payload["applicant_phone"] = merged_form["applicant_phone"]
        if "plot_number" in merged_form: patch_payload["plot_number"] = str(merged_form["plot_number"])
        if "map_sheet_number" in merged_form: patch_payload["map_sheet_number"] = str(merged_form["map_sheet_number"])
        if "land_address" in merged_form: patch_payload["land_address"] = merged_form["land_address"]
        if "land_area" in merged_form:
            try: patch_payload["land_area"] = float(merged_form["land_area"])
            except (ValueError, TypeError): pass
    else:
        name_val = merged_form.get("applicant_name") or merged_form.get("authorizer_name") or merged_form.get("lessor_name")
        if name_val: patch_payload["applicant_name"] = name_val
        id_val = merged_form.get("applicant_id_number") or merged_form.get("authorizer_id_number") or merged_form.get("lessor_id_number")
        if id_val: patch_payload["applicant_id_number"] = id_val
        phone_val = merged_form.get("applicant_phone") or merged_form.get("headquarters_phone") or merged_form.get("authorizer_phone") or merged_form.get("lessor_phone")
        if phone_val: patch_payload["applicant_phone"] = phone_val
        type_val = merged_form.get("business_type") or merged_form.get("llc_type")
        if type_val: patch_payload["business_type"] = type_val
        addr_val = merged_form.get("headquarters_address") or merged_form.get("rental_address")
        if addr_val: patch_payload["headquarters_address"] = addr_val
        if "business_name_vn" in merged_form: patch_payload["business_name_vn"] = merged_form["business_name_vn"]
        if "business_code" in merged_form: patch_payload["business_code"] = merged_form["business_code"]
        if "charter_capital" in merged_form:
            try: patch_payload["charter_capital"] = int(float(str(merged_form["charter_capital"]).replace(",", "").strip()))
            except (ValueError, TypeError): pass

    r = _session().patch(
        f"{supabase_url}/rest/v1/{table}",
        headers=_supabase_headers(),
        params={"id": f"eq.{record_id}"},
        json=patch_payload,
        timeout=8,
    )
    if r.status_code in (200, 204):
        if r.status_code == 200 and r.text:
            try:
                res_json = r.json()
                if res_json and len(res_json) > 0:
                    res = _serialize_row(res_json[0])
                    res["domain"] = clean_domain
                    res["target_table"] = table
                    return res
            except Exception:
                pass
        updated = get_procedure_record_by_id(clean_domain, record_id)
        if updated:
            return updated
    raise RuntimeError(f"Lỗi khi cập nhật hồ sơ vào {table} (HTTP {r.status_code}): {r.text}")


def delete_procedure_record(domain: str, record_id: str, user_id: Optional[str] = None) -> bool:
    """Xóa bản nháp hồ sơ người dùng"""
    clean_domain = domain.strip().lower()
    table = "land_procedure_records" if clean_domain == "land" else "business_procedure_records"
    existing = get_procedure_record_by_id(clean_domain, record_id)
    if not existing:
        raise ApplicationNotFoundError("Không tìm thấy hồ sơ yêu cầu.")

    # Kiểm tra quyền sở hữu chặt chẽ (H2)
    owner_id = existing.get("user_id")
    if owner_id:
        if not user_id or str(owner_id) != str(user_id):
            raise ApplicationPermissionError("FORBIDDEN: Bạn không có quyền xóa hồ sơ này.")

    # Chỉ cho phép xóa khi đang là nháp
    current_status = existing.get("status") or "draft"
    if current_status != "draft":
        raise ApplicationLockedError(f"Chỉ có thể xóa hồ sơ ở trạng thái nháp (draft). Hiện tại: '{current_status}'.")

    # 1. Thử qua SQLAlchemy
    if not is_postgres_circuit_open():
        engine = get_engine()
        if engine:
            try:
                with engine.begin() as conn:
                    conn.execute(text(f"DELETE FROM public.{table} WHERE id = :id"), {"id": safe_uuid_str(record_id) or str(record_id)})
                    return True
            except OperationalError as e:
                mark_postgres_failed()
                logger.warning(f"Lỗi kết nối PostgreSQL khi xóa hồ sơ: {e}. Thử fallback PostgREST...")
            except Exception as e:
                logger.warning(f"Lỗi khi xóa hồ sơ qua SQLAlchemy: {e}. Thử fallback PostgREST...")

    # 2. Fallback qua Supabase PostgREST
    supabase_url, _ = get_supabase_config()
    if not supabase_url:
        raise RuntimeError("Không có kết nối CSDL hoặc cấu hình SUPABASE_URL.")

    r = _session().delete(
        f"{supabase_url}/rest/v1/{table}",
        headers=_supabase_headers(),
        params={"id": f"eq.{record_id}"},
        timeout=8,
    )
    if r.status_code in (200, 204):
        return True
    raise RuntimeError(f"Lỗi khi xóa hồ sơ khỏi {table} (HTTP {r.status_code}): {r.text}")


def submit_procedure_record(domain: str, record_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    """Chuyển trạng thái hồ sơ từ nháp (draft) sang chính thức (submitted)"""
    clean_domain = domain.strip().lower()
    table = "land_procedure_records" if clean_domain == "land" else "business_procedure_records"
    existing = get_procedure_record_by_id(clean_domain, record_id)
    if not existing:
        raise ApplicationNotFoundError("Không tìm thấy hồ sơ yêu cầu.")

    owner_id = existing.get("user_id")
    if owner_id:
        if not user_id or str(owner_id) != str(user_id):
            raise ApplicationPermissionError("FORBIDDEN: Bạn không có quyền nộp hồ sơ này.")

    current_status = existing.get("status") or "draft"
    if current_status != "draft":
        raise ApplicationLockedError(f"Chỉ có thể nộp hồ sơ ở trạng thái nháp ('draft'). Trạng thái hiện tại: '{current_status}'.")

    # 1. Thử qua SQLAlchemy
    if not is_postgres_circuit_open():
        engine = get_engine()
        if engine:
            try:
                with engine.begin() as conn:
                    row = conn.execute(
                        text(f"UPDATE public.{table} SET status = 'submitted', updated_at = CURRENT_TIMESTAMP WHERE id = :id RETURNING *"),
                        {"id": safe_uuid_str(record_id) or str(record_id)}
                    ).mappings().fetchone()
                    if row:
                        res = _serialize_row(dict(row))
                        res["domain"] = clean_domain
                        res["target_table"] = table
                        return res
            except OperationalError as e:
                mark_postgres_failed()
                logger.warning(f"Lỗi kết nối PostgreSQL khi submit hồ sơ: {e}. Thử fallback PostgREST...")
            except Exception as e:
                logger.warning(f"Lỗi khi submit hồ sơ qua SQLAlchemy: {e}. Thử fallback PostgREST...")

    # 2. Fallback qua Supabase PostgREST
    supabase_url, _ = get_supabase_config()
    if not supabase_url:
        raise RuntimeError("Không có kết nối CSDL hoặc cấu hình SUPABASE_URL.")

    r = _session().patch(
        f"{supabase_url}/rest/v1/{table}",
        headers=_supabase_headers(),
        params={"id": f"eq.{record_id}"},
        json={"status": "submitted"},
        timeout=8,
    )
    if r.status_code in (200, 204):
        if r.status_code == 200 and r.text:
            try:
                res_json = r.json()
                if res_json and len(res_json) > 0:
                    res = _serialize_row(res_json[0])
                    res["domain"] = clean_domain
                    res["target_table"] = table
                    return res
            except Exception:
                pass
        updated = get_procedure_record_by_id(clean_domain, record_id)
        if updated:
            return updated
    raise RuntimeError(f"Lỗi khi nộp hồ sơ vào {table} (HTTP {r.status_code}): {r.text}")


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


def list_records(owner_id: Optional[str], domain: Optional[str], status: Optional[str], q: Optional[str],
                 page: int, page_size: int) -> tuple[list[dict], int]:
    """Hồ sơ của cả hai bảng, mới nhất trước. owner_id=None (chỉ admin) thì lấy của mọi người."""
    page = max(1, page)
    page_size = max(1, min(100, page_size))

    # 1. Thử qua SQLAlchemy
    if not is_postgres_circuit_open():
        try:
            where, params = [], {"limit": page_size, "offset": (page - 1) * page_size}
            if owner_id is not None:
                where.append("user_id = :owner_id")
                params["owner_id"] = str(owner_id)
            if domain:
                where.append("domain = :domain")
                params["domain"] = domain
            if status:
                where.append("status = :status")
                params["status"] = status
            if q and q.strip():
                q_clean = q.strip().lower()
                where_or = [
                    "lower(coalesce(applicant_name, '')) LIKE :q",
                    "coalesce(applicant_id_number, '') LIKE :q",
                    "lower(procedure_code) LIKE :q",
                ]
                params["q"] = f"%{q_clean}%"
                q_uuid = safe_uuid_str(q_clean)
                if q_uuid:
                    where_or.append("id = :q_uuid")
                    params["q_uuid"] = q_uuid
                elif HEX_8_PATTERN.match(q_clean):
                    where_or.append("(id >= :q_id_min AND id <= :q_id_max)")
                    params["q_id_min"] = f"{q_clean}-0000-0000-0000-000000000000"
                    params["q_id_max"] = f"{q_clean}-ffff-ffff-ffff-ffffffffffff"
                else:
                    where_or.append("lower(cast(id as text)) LIKE :q")

                if "-" in q_clean:
                    parts = q_clean.split("-")
                    if len(parts) >= 3:
                        p_code = f"{parts[0]}-{parts[1]}".upper()
                        short_id = parts[2].lower()
                        if HEX_8_PATTERN.match(short_id):
                            where_or.append("(upper(procedure_code) = :p_code_exact AND id >= :short_id_min AND id <= :short_id_max)")
                            params["p_code_exact"] = p_code
                            params["short_id_min"] = f"{short_id}-0000-0000-0000-000000000000"
                            params["short_id_max"] = f"{short_id}-ffff-ffff-ffff-ffffffffffff"
                        else:
                            where_or.append("(upper(procedure_code) = :p_code_exact AND lower(cast(id as text)) LIKE :short_id_like)")
                            params["p_code_exact"] = p_code
                            params["short_id_like"] = f"{short_id}%"
                where.append(f"({' OR '.join(where_or)})")
            where_sql = f"WHERE {' AND '.join(where)}" if where else ""
            with db_connection() as conn:
                total = conn.execute(text(f"SELECT count(*) FROM ({_LIST_SOURCE}) a {where_sql}"), params).scalar() or 0
                rows = conn.execute(text(f"SELECT * FROM ({_LIST_SOURCE}) a {where_sql} "
                                         "ORDER BY created_at DESC LIMIT :limit OFFSET :offset"), params).mappings().all()
            return [_list_item(dict(r), include_owner=owner_id is None) for r in rows], total
        except Exception as e:
            logger.warning(f"Lỗi khi lấy danh sách hồ sơ qua SQLAlchemy: {e}. Thử fallback PostgREST...")

    # 2. Fallback qua Supabase PostgREST
    try:
        supabase_url, _ = get_supabase_config()
        if supabase_url:
            proc_map = {}
            try:
                pr = _session().get(f"{supabase_url}/rest/v1/procedures?select=code,name,fee,receiving_agency_level,processing_days", headers=_supabase_headers(), timeout=5)
                if pr.status_code == 200:
                    for p in pr.json():
                        proc_map[p.get("code", "").upper()] = p
            except Exception:
                pass

            all_rows = []
            targets = []
            if domain == "land" or domain is None:
                targets.append(("land", "land_procedure_records"))
            if domain == "business" or domain is None:
                targets.append(("business", "business_procedure_records"))

            for dom_name, tbl in targets:
                p_query: Dict[str, Any] = {"select": "*", "order": "created_at.desc", "limit": 100}
                if owner_id is not None:
                    p_query["user_id"] = f"eq.{owner_id}"
                if status:
                    p_query["status"] = f"eq.{status}"

                r = _session().get(f"{supabase_url}/rest/v1/{tbl}", headers=_supabase_headers(), params=p_query, timeout=5)
                if r.status_code == 200:
                    for row in r.json():
                        p_meta = proc_map.get(str(row.get("procedure_code", "")).upper(), {})
                        enriched = dict(row)
                        enriched["domain"] = dom_name
                        enriched["procedure_name"] = p_meta.get("name")
                        enriched["fee"] = p_meta.get("fee")
                        enriched["receiving_agency_level"] = p_meta.get("receiving_agency_level")
                        enriched["processing_days"] = p_meta.get("processing_days")
                        if dom_name == "land":
                            enriched["address"] = row.get("land_address")
                        else:
                            enriched["address"] = row.get("headquarters_address")
                        all_rows.append(enriched)

            if q and q.strip():
                q_str = q.strip().lower()
                all_rows = [
                    r for r in all_rows
                    if q_str in str(r.get("applicant_name") or "").lower()
                    or q_str in str(r.get("applicant_id_number") or "").lower()
                    or q_str in str(r.get("procedure_code") or "").lower()
                    or q_str in str(r.get("id") or "").lower()
                ]

            all_rows.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
            total = len(all_rows)
            offset = (page - 1) * page_size
            sliced = all_rows[offset : offset + page_size]
            return [_list_item(r, include_owner=owner_id is None) for r in sliced], total
    except Exception as e:
        logger.warning(f"Lỗi fallback PostgREST trong list_records: {e}")

    return [], 0


def count_records() -> dict:
    """Số hồ sơ theo lĩnh vực và trạng thái (cho dashboard admin)."""
    by_domain = {"land": 0, "business": 0}
    by_status = dict.fromkeys(STATUSES, 0)

    if not is_postgres_circuit_open():
        try:
            with db_connection() as conn:
                rows = conn.execute(text(f"SELECT domain, status, count(*) AS n FROM ({_LIST_SOURCE}) a GROUP BY domain, status")).all()
            for domain, status, n in rows:
                if domain in by_domain:
                    by_domain[domain] += n
                if status in by_status:
                    by_status[status] += n
            return {"total": sum(by_domain.values()), "by_domain": by_domain, "by_status": by_status}
        except Exception as e:
            logger.warning(f"Lỗi khi đếm hồ sơ qua SQLAlchemy: {e}. Thử fallback PostgREST...")

    # Fallback PostgREST
    try:
        supabase_url, _ = get_supabase_config()
        if supabase_url:
            for d_name, tbl in [("land", "land_procedure_records"), ("business", "business_procedure_records")]:
                r = _session().get(f"{supabase_url}/rest/v1/{tbl}?select=status", headers=_supabase_headers(), timeout=5)
                if r.status_code == 200:
                    for row in r.json():
                        st = row.get("status")
                        by_domain[d_name] += 1
                        if st in by_status:
                            by_status[st] += 1
            return {"total": sum(by_domain.values()), "by_domain": by_domain, "by_status": by_status}
    except Exception as e:
        logger.warning(f"Lỗi fallback PostgREST trong count_records: {e}")

    return {"total": 0, "by_domain": by_domain, "by_status": by_status}
