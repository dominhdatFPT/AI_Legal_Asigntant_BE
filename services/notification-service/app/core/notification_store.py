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
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, Column, DateTime, String, Text, create_engine, desc, inspect, text
from sqlalchemy.exc import IntegrityError
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
    action_url = Column(String(512), nullable=False, default="/")
    source_type = Column(String(24), nullable=True)
    source_id = Column(String(64), nullable=True)
    metadata_json = Column(Text, nullable=False, default="{}")
    is_read = Column(Boolean, default=False, nullable=False)
    read_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "scope": "personal",
            "user_id": self.user_id,
            "event_type": self.event_type,
            "category": getattr(self, "category", None) or "system",
            "priority": getattr(self, "priority", None) or "normal",
            "title": self.title,
            "message": self.message,
            "icon": self.icon,
            "action_url": self.action_url,
            "source_type": getattr(self, "source_type", None),
            "source_id": getattr(self, "source_id", None),
            "is_read": self.is_read,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


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


def _get_db_url() -> str:
    env_url = os.getenv("NOTIFICATION_DATABASE_URL", "").strip()
    if env_url:
        return env_url

    docker_data_dir = Path("/app/data")
    if docker_data_dir.exists():
        return "sqlite:////app/data/notifications.db"

    local_data_dir = Path("data")
    local_data_dir.mkdir(parents=True, exist_ok=True)
    return "sqlite:///data/notifications.db"


_engine = None
_SessionFactory = None
_init_lock = threading.Lock()


def _ensure_notification_columns(engine) -> None:
    """Bổ sung cột mới cho bảng notifications nếu file SQLite/DB cũ đã tạo từ phiên bản trước."""
    try:
        insp = inspect(engine)
        if not insp.has_table("notifications"):
            return
        existing = {col["name"] for col in insp.get_columns("notifications")}
        alter_stmts = []
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
        if alter_stmts:
            with engine.begin() as conn:
                for stmt in alter_stmts:
                    conn.execute(text(stmt))
    except Exception as exc:
        logger.warning("Không thể auto-migrate cột mới cho bảng notifications: %s", exc)


def seed_report_catalog(session) -> None:
    """Seed danh mục nhóm vấn đề (categories) và nguyên nhân (reasons) nếu chưa có."""
    try:
        for cat in DEFAULT_CATEGORIES:
            if session.get(ReportCategoryRecord, cat["code"]) is None:
                session.add(ReportCategoryRecord(**cat))
        session.flush()
        for rsn in DEFAULT_REASONS:
            if session.get(ReportReasonRecord, rsn["code"]) is None:
                session.add(ReportReasonRecord(**rsn))
        session.commit()
    except IntegrityError:
        session.rollback()
    except Exception as exc:
        session.rollback()
        logger.warning("Không thể seed report catalog: %s", exc)


def get_engine():
    global _engine, _SessionFactory
    if _engine is None:
        with _init_lock:
            if _engine is None:
                db_url = _get_db_url()
                connect_args = {}
                if db_url.startswith("sqlite"):
                    connect_args = {"check_same_thread": False}
                engine = create_engine(db_url, pool_pre_ping=True, connect_args=connect_args)
                Base.metadata.create_all(engine)
                _ensure_notification_columns(engine)
                session_factory = sessionmaker(bind=engine, expire_on_commit=False)
                with session_factory() as s:
                    seed_report_catalog(s)
                _SessionFactory = session_factory
                _engine = engine
                logger.info("NotificationStore initialized with DB URL: %s", db_url.split("@")[-1])
    return _engine


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
            action_url=str(notif.get("action_url", "/")),
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
    expires_dt = _parse_dt(notif["expires_at"]) if notif.get("expires_at") else None

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
            action_url=str(notif.get("action_url", "/")),
            source_type=notif.get("source_type"),
            source_id=notif.get("source_id"),
            created_by=notif.get("created_by"),
            created_at=dt,
            expires_at=expires_dt,
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


def get_user_notifications(
    user_id: str,
    limit: int = 50,
    user_role: Optional[str] = None,
    unread_only: bool = False,
) -> List[Dict[str, Any]]:
    """Truy vấn danh sách thông báo của người dùng (gộp cả thông báo cá nhân và thông báo nhóm/broadcast)."""
    try:
        now = datetime.now(timezone.utc)
        audiences = ["all"]
        if user_role:
            audiences.append(str(user_role))

        with get_session() as session:
            # 1. Thông báo cá nhân
            p_query = session.query(NotificationRecord).filter(
                NotificationRecord.user_id == str(user_id),
                NotificationRecord.deleted_at.is_(None),
            )
            if unread_only:
                p_query = p_query.filter(NotificationRecord.is_read.is_(False))
            personal_records = p_query.order_by(desc(NotificationRecord.created_at)).limit(limit).all()
            items: List[Dict[str, Any]] = [r.to_dict() for r in personal_records]

            # 2. Thông báo nhóm / toàn hệ thống (Fan-out on read)
            g_records = (
                session.query(GroupNotificationRecord)
                .filter(
                    GroupNotificationRecord.is_deleted.is_(False),
                    GroupNotificationRecord.audience.in_(audiences),
                )
                .order_by(desc(GroupNotificationRecord.created_at))
                .limit(limit)
                .all()
            )
            if g_records:
                g_ids = [g.id for g in g_records]
                read_rows = (
                    session.query(GroupNotificationReadRecord)
                    .filter(
                        GroupNotificationReadRecord.user_id == str(user_id),
                        GroupNotificationReadRecord.group_notification_id.in_(g_ids),
                    )
                    .all()
                )
                read_map = {r.group_notification_id: r for r in read_rows}
                for g in g_records:
                    if g.expires_at:
                        exp = _parse_dt(g.expires_at)
                        if exp < now:
                            continue
                    r_state = read_map.get(g.id)
                    if r_state and r_state.hidden:
                        continue
                    is_read = bool(r_state and r_state.is_read)
                    if unread_only and is_read:
                        continue
                    items.append(g.to_dict(is_read=is_read))

            items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
            return items[:limit]
    except Exception as e:
        logger.error("Lỗi khi truy vấn notifications của user [%s]: %s", user_id, e)
        return []


def count_unread_notifications(user_id: str, user_role: Optional[str] = None) -> int:
    items = get_user_notifications(user_id=user_id, limit=200, user_role=user_role, unread_only=True)
    return len(items)


def mark_as_read(user_id: str, notification_id: str, user_role: Optional[str] = None) -> bool:
    """Đánh dấu thông báo cá nhân hoặc thông báo nhóm (`grp-...`) là đã đọc."""
    try:
        now = datetime.now(timezone.utc)
        with get_session() as session:
            if str(notification_id).startswith("grp-"):
                grp = session.get(GroupNotificationRecord, str(notification_id))
                if not grp or grp.is_deleted:
                    return False
                if user_role and grp.audience not in ("all", str(user_role)):
                    return False
                existing = (
                    session.query(GroupNotificationReadRecord)
                    .filter(
                        GroupNotificationReadRecord.user_id == str(user_id),
                        GroupNotificationReadRecord.group_notification_id == str(notification_id),
                    )
                    .first()
                )
                if existing:
                    existing.is_read = True
                    existing.read_at = now
                else:
                    session.add(
                        GroupNotificationReadRecord(
                            group_notification_id=str(notification_id),
                            user_id=str(user_id),
                            is_read=True,
                            read_at=now,
                            hidden=False,
                        )
                    )
                session.commit()
                return True

            record = (
                session.query(NotificationRecord)
                .filter(
                    NotificationRecord.id == str(notification_id),
                    NotificationRecord.user_id == str(user_id),
                    NotificationRecord.deleted_at.is_(None),
                )
                .first()
            )
            if record:
                record.is_read = True
                record.read_at = now
                session.commit()
                return True
        return False
    except Exception as e:
        logger.error("Lỗi khi đánh dấu đã đọc notification: %s", e)
        return False


def mark_all_as_read(user_id: str, user_role: Optional[str] = None) -> int:
    """Đánh dấu toàn bộ thông báo hiện tại của người dùng là đã đọc."""
    unread_items = get_user_notifications(user_id=user_id, limit=200, user_role=user_role, unread_only=True)
    count = 0
    for item in unread_items:
        if mark_as_read(user_id, item["id"], user_role=user_role):
            count += 1
    return count


def hide_notification(user_id: str, notification_id: str, user_role: Optional[str] = None) -> bool:
    """Xóa mềm / ẩn thông báo khỏi danh sách của người dùng."""
    try:
        now = datetime.now(timezone.utc)
        with get_session() as session:
            if str(notification_id).startswith("grp-"):
                grp = session.get(GroupNotificationRecord, str(notification_id))
                if not grp or grp.is_deleted:
                    return False
                if user_role and grp.audience not in ("all", str(user_role)):
                    return False
                existing = (
                    session.query(GroupNotificationReadRecord)
                    .filter(
                        GroupNotificationReadRecord.user_id == str(user_id),
                        GroupNotificationReadRecord.group_notification_id == str(notification_id),
                    )
                    .first()
                )
                if existing:
                    existing.hidden = True
                    existing.is_read = True
                else:
                    session.add(
                        GroupNotificationReadRecord(
                            group_notification_id=str(notification_id),
                            user_id=str(user_id),
                            is_read=True,
                            read_at=now,
                            hidden=True,
                        )
                    )
                session.commit()
                return True

            record = (
                session.query(NotificationRecord)
                .filter(
                    NotificationRecord.id == str(notification_id),
                    NotificationRecord.user_id == str(user_id),
                    NotificationRecord.deleted_at.is_(None),
                )
                .first()
            )
            if record:
                record.deleted_at = now
                session.commit()
                return True
        return False
    except Exception as e:
        logger.error("Lỗi khi ẩn notification: %s", e)
        return False
