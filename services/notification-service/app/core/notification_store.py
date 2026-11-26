"""Lớp lưu trữ dữ liệu thông báo & báo cáo (Notification & Report Store) cho Notification Service.

Hỗ trợ lưu trữ bền vững (Persistent Storage):
- Kết nối PostgreSQL qua NOTIFICATION_DATABASE_URL (khi triển khai production).
- Tự động fallback về SQLite file (/app/data/notifications.db hoặc local notifications.db)
  khi chưa cấu hình PostgreSQL, giúp chia sẻ trạng thái đồng bộ giữa FastAPI process và Worker process.
- Tích hợp mô hình Fan-out on Read cho Group Notifications / Broadcast (cho phép gửi thông báo
  tới toàn bộ user + lawyer + chính admin gửi mà chỉ cần 1 lệnh INSERT).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    String,
    Text,
    and_,
    cast,
    create_engine,
    desc,
    func,
    inspect,
    literal,
    null,
    or_,
    select,
    text,
    true,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker

from app.models.reports import DEFAULT_CATEGORIES, DEFAULT_REASONS, register_report_models

logger = logging.getLogger("NotificationStore")

Base = declarative_base()


class NotificationRecord(Base):
    __tablename__ = "notifications"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), index=True, nullable=False)
    event_type = Column(String(64), nullable=False)
    category = Column(String(24), nullable=False, default="system")
    priority = Column(String(10), nullable=False, default="normal")
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    icon = Column(String(64), nullable=False, default="bell")
    action_url = Column(String(512), nullable=False, default="")
    source_type = Column(String(24), nullable=True)
    source_id = Column(String(64), nullable=True)
    metadata_json = Column(Text, nullable=False, default="{}")
    is_read = Column(Boolean, default=False, nullable=False)
    read_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": self.id,
            "scope": "personal",
            "user_id": self.user_id,
            "event_type": self.event_type,
            "category": getattr(self, "category", None) or "system",
            "priority": getattr(self, "priority", None) or "normal",
            "title": self.title,
            "message": self.message,
            "icon": self.icon,
            "action_url": self.action_url or "",
            "is_read": self.is_read,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        source_type = getattr(self, "source_type", None)
        source_id = getattr(self, "source_id", None)
        if source_type is not None:
            data["source_type"] = source_type
        if source_id is not None:
            data["source_id"] = source_id
        return data


# Đăng ký toàn bộ bảng Report & Group Notification vào cùng Base
_MODELS = register_report_models(Base)
ReportCategoryRecord = _MODELS["ReportCategoryRecord"]
ReportReasonRecord = _MODELS["ReportReasonRecord"]
ReportRecord = _MODELS["ReportRecord"]
ReportAttachmentRecord = _MODELS["ReportAttachmentRecord"]
ReportStatusHistoryRecord = _MODELS["ReportStatusHistoryRecord"]
GroupNotificationRecord = _MODELS["GroupNotificationRecord"]
GroupNotificationReadRecord = _MODELS["GroupNotificationReadRecord"]
BroadcastRecord = _MODELS["BroadcastRecord"]


class NotificationOutboxRecord(Base):
    __tablename__ = "notification_outbox"
    id = Column(String(128), primary_key=True)
    routing_key = Column(String(100), nullable=False)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    available_at = Column(DateTime(timezone=True), nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=True)


def _get_db_url() -> str:
    env_url = os.getenv("NOTIFICATION_DATABASE_URL", "").strip()
    if env_url:
        if env_url.startswith("postgres://"):
            return "postgresql+psycopg://" + env_url[len("postgres://"):]
        if env_url.startswith("postgresql://"):
            return "postgresql+psycopg://" + env_url[len("postgresql://"):]
        return env_url

    docker_data_dir = Path("/app/data")
    if docker_data_dir.exists():
        return "sqlite:////app/data/notifications.db"

    local_data_dir = Path("data")
    local_data_dir.mkdir(parents=True, exist_ok=True)
    return "sqlite:///data/notifications.db"


_engine = None
_ro_engine = None
_SessionFactory = None
_init_lock = threading.Lock()


def _ensure_notification_columns(engine) -> None:
    """Bổ sung cột mới cho bảng notifications, group_notifications & broadcasts nếu file SQLite/DB cũ đã tạo từ phiên bản trước."""
    try:
        insp = inspect(engine)
        alter_stmts: List[str] = []
        if insp.has_table("notifications"):
            existing = {col["name"] for col in insp.get_columns("notifications")}
            if "category" not in existing:
                alter_stmts.append("ALTER TABLE notifications ADD COLUMN category VARCHAR(24) NOT NULL DEFAULT 'system'")
            if "priority" not in existing:
                alter_stmts.append("ALTER TABLE notifications ADD COLUMN priority VARCHAR(10) NOT NULL DEFAULT 'normal'")
            if "source_type" not in existing:
                alter_stmts.append("ALTER TABLE notifications ADD COLUMN source_type VARCHAR(24)")
            if "source_id" not in existing:
                alter_stmts.append("ALTER TABLE notifications ADD COLUMN source_id VARCHAR(64)")
            if "metadata_json" not in existing:
                alter_stmts.append("ALTER TABLE notifications ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")
            if "read_at" not in existing:
                alter_stmts.append("ALTER TABLE notifications ADD COLUMN read_at TIMESTAMP")
            if "deleted_at" not in existing:
                alter_stmts.append("ALTER TABLE notifications ADD COLUMN deleted_at TIMESTAMP")

        if insp.has_table("group_notifications"):
            grp_existing = {col["name"] for col in insp.get_columns("group_notifications")}
            if "expires_at" not in grp_existing:
                alter_stmts.append("ALTER TABLE group_notifications ADD COLUMN expires_at TIMESTAMP")

        if insp.has_table("broadcasts"):
            bc_existing = {col["name"] for col in insp.get_columns("broadcasts")}
            if "recipient_id" not in bc_existing:
                alter_stmts.append("ALTER TABLE broadcasts ADD COLUMN recipient_id VARCHAR(255)")
            if "personal_notification_id" not in bc_existing:
                alter_stmts.append("ALTER TABLE broadcasts ADD COLUMN personal_notification_id VARCHAR(64)")

        if alter_stmts:
            if engine.dialect.name == "sqlite":
                raw = engine.raw_connection()
                try:
                    raw.executescript(";\n".join(alter_stmts))
                    raw.commit()
                finally:
                    raw.close()
            else:
                table_clauses: Dict[str, List[str]] = {}
                for stmt in alter_stmts:
                    parts = stmt.split(" ADD COLUMN ", 1)
                    if len(parts) == 2:
                        tbl = parts[0].replace("ALTER TABLE ", "").strip()
                        table_clauses.setdefault(tbl, []).append(f"ADD COLUMN IF NOT EXISTS {parts[1]}")
                if table_clauses:
                    ddl_list = [
                        f"ALTER TABLE {tbl} {', '.join(clauses)}" for tbl, clauses in table_clauses.items()
                    ]
                    ddl_sql = ddl_list[0] if len(ddl_list) == 1 else f"DO $$ BEGIN {'; '.join(ddl_list)}; END $$;"
                    with engine.begin() as conn:
                        conn.execute(text(ddl_sql))
    except Exception as exc:
        logger.warning("Không thể auto-migrate cột mới cho bảng notifications/broadcasts: %s", exc)


def seed_report_catalog(session) -> None:
    """Seed danh mục nhóm vấn đề (categories) và nguyên nhân (reasons) nếu chưa có."""
    try:
        existing_cats = {code for (code,) in session.query(ReportCategoryRecord.code).all()}
        missing_cats = [ReportCategoryRecord(**cat) for cat in DEFAULT_CATEGORIES if cat["code"] not in existing_cats]
        existing_reasons = {code for (code,) in session.query(ReportReasonRecord.code).all()}
        missing_reasons = [ReportReasonRecord(**rsn) for rsn in DEFAULT_REASONS if rsn["code"] not in existing_reasons]
        if not missing_cats and not missing_reasons:
            return
        if missing_cats:
            session.add_all(missing_cats)
            session.flush()
        if missing_reasons:
            session.add_all(missing_reasons)
        session.commit()
    except IntegrityError:
        session.rollback()
    except Exception as exc:
        session.rollback()
        logger.warning("Không thể seed report catalog: %s", exc)


def get_engine():
    global _engine, _ro_engine, _SessionFactory
    if _engine is None:
        with _init_lock:
            if _engine is None:
                _ro_engine = None
                db_url = _get_db_url()
                url = make_url(db_url)
                if url.get_backend_name() == "sqlite":
                    engine = create_engine(
                        db_url,
                        pool_pre_ping=False,
                        connect_args={"check_same_thread": False},
                    )
                    Base.metadata.create_all(engine)
                    _ensure_notification_columns(engine)
                else:
                    transaction_pooler = url.port == 6543 and "pooler" in (url.host or "").lower()
                    connect_args: Dict[str, Any] = {
                        "connect_timeout": 5,
                        "keepalives": 1,
                        "keepalives_idle": 15,
                        "keepalives_interval": 5,
                        "keepalives_count": 3,
                    }
                    if transaction_pooler and url.get_driver_name() == "psycopg":
                        connect_args["prepare_threshold"] = None
                    if sys.platform.startswith("linux"):
                        connect_args["tcp_user_timeout"] = 10000
                    engine = create_engine(
                        db_url,
                        pool_pre_ping=False,
                        pool_use_lifo=True,
                        pool_size=4,
                        max_overflow=2,
                        pool_timeout=5,
                        pool_recycle=300,
                        connect_args=connect_args,
                    )
                    with engine.connect() as conn:
                        row = conn.execute(
                            text(
                                "SELECT "
                                "to_regclass('public.notifications') IS NOT NULL, "
                                "to_regclass('public.group_notifications') IS NOT NULL, "
                                "to_regclass('public.group_notification_reads') IS NOT NULL, "
                                "to_regclass('public.broadcasts') IS NOT NULL, "
                                "to_regclass('public.report_categories') IS NOT NULL, "
                                "to_regclass('public.report_reasons') IS NOT NULL, "
                                "to_regclass('public.notification_outbox') IS NOT NULL"
                            )
                        ).first()
                    if row is None or not all(row):
                        Base.metadata.create_all(engine)
                        _ensure_notification_columns(engine)

                ro_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
                session_factory = sessionmaker(bind=engine, expire_on_commit=False)
                with session_factory() as s:
                    seed_report_catalog(s)
                _SessionFactory = session_factory
                _ro_engine = ro_engine
                _engine = engine
                logger.info("NotificationStore initialized with DB URL: %s", db_url.split("@")[-1])
    return _engine


def get_ro_engine():
    if _engine is None or _ro_engine is None:
        get_engine()
    return _ro_engine


def execute_readonly(fn):
    """Thực thi truy vấn chỉ đọc trên engine AUTOCOMMIT, tự thử lại 1 lần khi mất kết nối cũ."""
    ro_engine = get_ro_engine()
    for attempt in range(2):
        try:
            with ro_engine.connect() as conn:
                return fn(conn)
        except (OperationalError, DBAPIError) as exc:
            is_conn_err = isinstance(exc, OperationalError) or bool(getattr(exc, "connection_invalidated", False))
            if attempt == 0 and is_conn_err:
                logger.warning("Readonly DB connection error, retrying once: %s", exc)
                continue
            raise


def get_session():
    get_engine()
    return _SessionFactory()


def _parse_dt(val: Any) -> datetime:
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def save_notification(notif: Dict[str, Any]) -> None:
    """Lưu thông báo cá nhân; trùng id (event giao lại) thì bỏ qua. Lỗi DB được ném ra để consumer chuyển sang DLQ."""
    dt = _parse_dt(notif.get("created_at"))

    with get_session() as session:
        record = NotificationRecord(
            id=str(notif.get("id")),
            user_id=str(notif.get("user_id")),
            event_type=str(notif.get("event_type", "general")),
            category=str(notif.get("category", "system")),
            priority=str(notif.get("priority", "normal")),
            title=str(notif.get("title", "Thông báo")),
            message=str(notif.get("message", "")),
            icon=str(notif.get("icon", "bell")),
            action_url=str(notif.get("action_url") or ""),
            source_type=notif.get("source_type"),
            source_id=notif.get("source_id"),
            is_read=bool(notif.get("is_read", False)),
            created_at=dt,
        )
        if session.get(NotificationRecord, record.id) is None:
            session.add(record)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                if session.get(NotificationRecord, record.id) is None:
                    raise


def save_group_notification(notif: Dict[str, Any]) -> None:
    """Lưu thông báo nhóm (broadcast toàn hệ thống hoặc thông báo gửi cho đội ngũ Admin). Idempotent theo id."""
    dt = _parse_dt(notif.get("created_at"))
    expires_at = _parse_dt(notif["expires_at"]) if notif.get("expires_at") is not None else None

    with get_session() as session:
        record = GroupNotificationRecord(
            id=str(notif.get("id")),
            audience=str(notif.get("audience", "all")),
            event_type=str(notif.get("event_type", "broadcast.published")),
            category=str(notif.get("category", "system")),
            priority=str(notif.get("priority", "normal")),
            title=str(notif.get("title", "Thông báo hệ thống")),
            message=str(notif.get("message", "")),
            icon=str(notif.get("icon", "bell")),
            action_url=str(notif.get("action_url") or ""),
            source_type=notif.get("source_type"),
            source_id=notif.get("source_id"),
            created_by=notif.get("created_by"),
            created_at=dt,
            expires_at=expires_at,
            is_deleted=False,
        )
        if session.get(GroupNotificationRecord, record.id) is None:
            session.add(record)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                if session.get(GroupNotificationRecord, record.id) is None:
                    raise


def _apply_before_core(stmt, table, before: Optional[datetime], before_id: Optional[str]):
    if before is not None:
        before_utc = _parse_dt(before).astimezone(timezone.utc)
        condition = table.c.created_at < before_utc
        if before_id is not None:
            condition = or_(
                condition,
                and_(table.c.created_at == before_utc, table.c.id < str(before_id)),
            )
        stmt = stmt.where(condition)
    return stmt


def _unread_count_expr(uid: str, audiences: List[str], now: datetime):
    N = NotificationRecord.__table__
    G = GroupNotificationRecord.__table__
    R = GroupNotificationReadRecord.__table__
    personal_unread = (
        select(func.count(N.c.id))
        .where(
            N.c.user_id == uid,
            N.c.deleted_at.is_(None),
            N.c.title.is_not(None),
            N.c.message.is_not(None),
            N.c.is_read.is_(False),
        )
        .scalar_subquery()
    )
    group_unread = (
        select(func.count(G.c.id))
        .select_from(
            G.outerjoin(
                R,
                and_(R.c.group_notification_id == G.c.id, R.c.user_id == uid),
            )
        )
        .where(
            G.c.is_deleted.is_(False),
            G.c.audience.in_(audiences),
            G.c.title.is_not(None),
            G.c.message.is_not(None),
            or_(G.c.expires_at.is_(None), G.c.expires_at > now),
            or_(R.c.hidden.is_(None), R.c.hidden.is_(False)),
            or_(R.c.is_read.is_(None), R.c.is_read.is_(False)),
        )
        .scalar_subquery()
    )
    return (personal_unread + group_unread).label("unread_count")


def _row_to_item(row: Any) -> Dict[str, Any]:
    scope = row["scope"]
    item: Dict[str, Any] = {
        "id": row["id"],
        "scope": scope,
    }
    if scope == "personal" and row["user_id"] is not None:
        item["user_id"] = row["user_id"]
    elif scope == "group" and row["audience"] is not None:
        item["audience"] = row["audience"]
    item.update(
        {
            "event_type": row["event_type"],
            "category": row["category"] or "system",
            "priority": row["priority"] or "normal",
            "title": row["title"],
            "message": row["message"],
            "icon": row["icon"] or "bell",
            "action_url": row["action_url"] or "",
            "is_read": bool(row["is_read"]),
            "created_at": _parse_dt(row["created_at"]).astimezone(timezone.utc).isoformat(),
        }
    )
    if row["source_type"] is not None:
        item["source_type"] = row["source_type"]
    if row["source_id"] is not None:
        item["source_id"] = row["source_id"]
    return item


def get_notification_feed(
    user_id: str,
    limit: int = 20,
    user_role: Optional[str] = None,
    is_read: Optional[bool] = None,
    unread_only: bool = False,
    before: Optional[datetime] = None,
    before_id: Optional[str] = None,
    include_unread_count: bool = True,
) -> Dict[str, Any]:
    """Lấy feed thông báo (personal + group) và unread_count chỉ trong 1 round-trip DB."""
    N = NotificationRecord.__table__
    G = GroupNotificationRecord.__table__
    R = GroupNotificationReadRecord.__table__

    uid = str(user_id)
    audiences = ["all"] + ([str(user_role)] if user_role else [])
    now = datetime.now(timezone.utc)
    read_filter = False if unread_only else is_read
    fetch_limit = limit + 1

    personal_stmt = select(
        N.c.id,
        literal("personal").label("scope"),
        N.c.user_id,
        cast(null(), String).label("audience"),
        N.c.event_type,
        N.c.category,
        N.c.priority,
        N.c.title,
        N.c.message,
        N.c.icon,
        N.c.action_url,
        N.c.source_type,
        N.c.source_id,
        N.c.is_read,
        N.c.created_at,
    ).where(
        N.c.user_id == uid,
        N.c.deleted_at.is_(None),
        N.c.title.is_not(None),
        N.c.message.is_not(None),
    )
    if read_filter is False:
        personal_stmt = personal_stmt.where(N.c.is_read.is_(False))
    elif read_filter is True:
        personal_stmt = personal_stmt.where(N.c.is_read.is_(True))
    personal_stmt = _apply_before_core(personal_stmt, N, before, before_id)
    personal_cte = personal_stmt.order_by(desc(N.c.created_at), desc(N.c.id)).limit(fetch_limit).cte("personal_page")

    group_stmt = (
        select(
            G.c.id,
            literal("group").label("scope"),
            cast(null(), String).label("user_id"),
            G.c.audience,
            G.c.event_type,
            G.c.category,
            G.c.priority,
            G.c.title,
            G.c.message,
            G.c.icon,
            G.c.action_url,
            G.c.source_type,
            G.c.source_id,
            func.coalesce(R.c.is_read, False).label("is_read"),
            G.c.created_at,
        )
        .select_from(
            G.outerjoin(
                R,
                and_(R.c.group_notification_id == G.c.id, R.c.user_id == uid),
            )
        )
        .where(
            G.c.is_deleted.is_(False),
            G.c.audience.in_(audiences),
            G.c.title.is_not(None),
            G.c.message.is_not(None),
            or_(G.c.expires_at.is_(None), G.c.expires_at > now),
            or_(R.c.hidden.is_(None), R.c.hidden.is_(False)),
        )
    )
    if read_filter is False:
        group_stmt = group_stmt.where(or_(R.c.is_read.is_(None), R.c.is_read.is_(False)))
    elif read_filter is True:
        group_stmt = group_stmt.where(R.c.is_read.is_(True))
    group_stmt = _apply_before_core(group_stmt, G, before, before_id)
    group_cte = group_stmt.order_by(desc(G.c.created_at), desc(G.c.id)).limit(fetch_limit).cte("group_page")

    merged = union_all(select(personal_cte), select(group_cte)).subquery("merged")
    page_cte = (
        select(merged)
        .order_by(desc(merged.c.created_at), desc(merged.c.id))
        .limit(fetch_limit)
        .cte("page_items")
    )

    if include_unread_count:
        unread_cte = select(_unread_count_expr(uid, audiences, now)).cte("unread")
        stmt = (
            select(page_cte, unread_cte.c.unread_count)
            .select_from(unread_cte.outerjoin(page_cte, true()))
            .order_by(desc(page_cte.c.created_at), desc(page_cte.c.id))
        )
        rows = execute_readonly(lambda conn: conn.execute(stmt).mappings().all())
        unread_count = int(rows[0]["unread_count"] or 0) if rows else 0
        raw_items = [_row_to_item(r) for r in rows if r["id"] is not None]
    else:
        stmt = select(page_cte).order_by(desc(page_cte.c.created_at), desc(page_cte.c.id))
        rows = execute_readonly(lambda conn: conn.execute(stmt).mappings().all())
        unread_count = None
        raw_items = [_row_to_item(r) for r in rows]

    has_more = len(raw_items) > limit
    return {
        "items": raw_items[:limit],
        "has_more": has_more,
        "unread_count": unread_count,
    }


def get_user_notifications(
    user_id: str,
    limit: int = 50,
    user_role: Optional[str] = None,
    unread_only: bool = False,
    before: Optional[datetime] = None,
    before_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return get_notification_feed(
        user_id=user_id,
        limit=limit,
        user_role=user_role,
        unread_only=unread_only,
        before=before,
        before_id=before_id,
        include_unread_count=False,
    )["items"]


def count_unread_notifications(user_id: str, user_role: Optional[str] = None) -> int:
    uid = str(user_id)
    audiences = ["all"] + ([str(user_role)] if user_role else [])
    now = datetime.now(timezone.utc)
    stmt = select(_unread_count_expr(uid, audiences, now))
    val = execute_readonly(lambda conn: conn.execute(stmt).scalar())
    return int(val or 0)


def _group_upsert_stmt(
    dialect_name: str,
    *,
    uid: str,
    audiences: List[str],
    now: datetime,
    hidden: bool,
    notification_id: Optional[str] = None,
    only_unread: bool = False,
):
    G = GroupNotificationRecord.__table__
    R = GroupNotificationReadRecord.__table__
    r_old = R.alias("r_old")

    sel = select(
        G.c.id,
        cast(literal(uid), String).label("user_id"),
        cast(literal(True), Boolean).label("is_read"),
        cast(literal(now), DateTime(timezone=True)).label("read_at"),
        cast(literal(hidden), Boolean).label("hidden"),
    )
    if only_unread:
        sel = sel.select_from(
            G.outerjoin(
                r_old,
                and_(r_old.c.group_notification_id == G.c.id, r_old.c.user_id == uid),
            )
        )
    sel = sel.where(
        G.c.is_deleted.is_(False),
        G.c.audience.in_(audiences),
        G.c.title.is_not(None),
        G.c.message.is_not(None),
        or_(G.c.expires_at.is_(None), G.c.expires_at > now),
    )
    if notification_id is not None:
        sel = sel.where(G.c.id == notification_id)
    if only_unread:
        sel = sel.where(
            or_(r_old.c.hidden.is_(None), r_old.c.hidden.is_(False)),
            or_(r_old.c.is_read.is_(None), r_old.c.is_read.is_(False)),
        )

    insert_fn = pg_insert if dialect_name == "postgresql" else sqlite_insert
    ins = insert_fn(R).from_select(
        ["group_notification_id", "user_id", "is_read", "read_at", "hidden"],
        sel,
    )
    update_fields: Dict[str, Any] = {
        "is_read": True,
        "read_at": func.coalesce(R.c.read_at, ins.excluded.read_at),
    }
    if hidden:
        update_fields["hidden"] = True
    return ins.on_conflict_do_update(
        index_elements=["group_notification_id", "user_id"],
        set_=update_fields,
    )


def mark_as_read(user_id: str, notification_id: str, user_role: Optional[str] = None) -> bool:
    """Đánh dấu thông báo cá nhân hoặc thông báo nhóm (`grp-...`) là đã đọc trong 1 round-trip DB."""
    now = datetime.now(timezone.utc)
    uid = str(user_id)
    nid = str(notification_id)
    audiences = ["all"] + ([str(user_role)] if user_role else [])
    N = NotificationRecord.__table__
    R = GroupNotificationReadRecord.__table__

    with get_engine().begin() as conn:
        if nid.startswith("grp-"):
            stmt = _group_upsert_stmt(
                conn.dialect.name,
                uid=uid,
                audiences=audiences,
                now=now,
                hidden=False,
                notification_id=nid,
            ).returning(R.c.group_notification_id)
            return conn.execute(stmt).first() is not None

        stmt = (
            update(N)
            .where(N.c.id == nid, N.c.user_id == uid, N.c.deleted_at.is_(None))
            .values(is_read=True, read_at=func.coalesce(N.c.read_at, now))
            .returning(N.c.id)
        )
        return conn.execute(stmt).first() is not None


def mark_all_as_read(user_id: str, user_role: Optional[str] = None) -> int:
    now = datetime.now(timezone.utc)
    uid = str(user_id)
    audiences = ["all"] + ([str(user_role)] if user_role else [])
    N = NotificationRecord.__table__

    with get_engine().begin() as conn:
        personal_stmt = (
            update(N)
            .where(
                N.c.user_id == uid,
                N.c.deleted_at.is_(None),
                N.c.title.is_not(None),
                N.c.message.is_not(None),
                N.c.is_read.is_(False),
            )
            .values(is_read=True, read_at=now)
        )
        group_stmt = _group_upsert_stmt(
            conn.dialect.name,
            uid=uid,
            audiences=audiences,
            now=now,
            hidden=False,
            only_unread=True,
        )
        if conn.dialect.name == "postgresql":
            p_cte = personal_stmt.returning(literal(1).label("v")).cte("p")
            g_cte = group_stmt.returning(literal(1).label("v")).cte("g")
            stmt = select(
                select(func.count()).select_from(p_cte).scalar_subquery()
                + select(func.count()).select_from(g_cte).scalar_subquery()
            )
            return int(conn.execute(stmt).scalar() or 0)

        personal_updated = conn.execute(personal_stmt).rowcount or 0
        group_updated = conn.execute(group_stmt).rowcount or 0
        return int(personal_updated) + int(group_updated)


def hide_notification(user_id: str, notification_id: str, user_role: Optional[str] = None) -> bool:
    """Xóa mềm / ẩn thông báo khỏi danh sách của người dùng trong 1 round-trip DB."""
    now = datetime.now(timezone.utc)
    uid = str(user_id)
    nid = str(notification_id)
    audiences = ["all"] + ([str(user_role)] if user_role else [])
    N = NotificationRecord.__table__
    R = GroupNotificationReadRecord.__table__

    with get_engine().begin() as conn:
        if nid.startswith("grp-"):
            stmt = _group_upsert_stmt(
                conn.dialect.name,
                uid=uid,
                audiences=audiences,
                now=now,
                hidden=True,
                notification_id=nid,
            ).returning(R.c.group_notification_id)
            return conn.execute(stmt).first() is not None

        stmt = (
            update(N)
            .where(N.c.id == nid, N.c.user_id == uid, N.c.deleted_at.is_(None))
            .values(deleted_at=now, is_read=True, read_at=func.coalesce(N.c.read_at, now))
            .returning(N.c.id)
        )
        return conn.execute(stmt).first() is not None
