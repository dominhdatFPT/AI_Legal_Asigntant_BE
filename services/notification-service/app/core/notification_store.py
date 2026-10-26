"""Lớp lưu trữ dữ liệu thông báo (Notification Store) cho Notification Service.

Hỗ trợ lưu trữ bền vững (Persistent Storage):
- Kết nối PostgreSQL qua NOTIFICATION_DATABASE_URL (khi triển khai production).
- Tự động fallback về SQLite file (/app/data/notifications.db hoặc local notifications.db)
  khi chưa cấu hình PostgreSQL, giúp chia sẻ trạng thái đồng bộ giữa FastAPI process và Worker process.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, Column, DateTime, String, Text, create_engine, desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker

logger = logging.getLogger("NotificationStore")

Base = declarative_base()


class NotificationRecord(Base):
    __tablename__ = "notifications"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), index=True, nullable=False)
    event_type = Column(String(64), nullable=False)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    icon = Column(String(64), nullable=False, default="bell")
    action_url = Column(String(512), nullable=False, default="/")
    is_read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "event_type": self.event_type,
            "title": self.title,
            "message": self.message,
            "icon": self.icon,
            "action_url": self.action_url,
            "is_read": self.is_read,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def _get_db_url() -> str:
    env_url = os.getenv("NOTIFICATION_DATABASE_URL", "").strip()
    if env_url:
        return env_url

    # Fallback sang SQLite dùng chung
    # Nếu thư mục /app/data tồn tại (docker volume mount)
    docker_data_dir = Path("/app/data")
    if docker_data_dir.exists():
        return "sqlite:////app/data/notifications.db"

    # Hoặc thư mục data cục bộ
    local_data_dir = Path("data")
    local_data_dir.mkdir(parents=True, exist_ok=True)
    return "sqlite:///data/notifications.db"


_engine = None
_SessionFactory = None
_init_lock = threading.Lock()


def get_engine():
    global _engine, _SessionFactory
    if _engine is None:
        # Consumer gọi song song từ nhiều thread: chỉ khởi tạo một lần, gán _engine sau cùng.
        with _init_lock:
            if _engine is None:
                db_url = _get_db_url()
                connect_args = {}
                if db_url.startswith("sqlite"):
                    connect_args = {"check_same_thread": False}
                engine = create_engine(db_url, pool_pre_ping=True, connect_args=connect_args)
                Base.metadata.create_all(engine)
                _SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
                _engine = engine
                logger.info("NotificationStore initialized with DB URL: %s", db_url.split("@")[-1])
    return _engine


def get_session():
    get_engine()
    return _SessionFactory()


def save_notification(notif: Dict[str, Any]) -> None:
    """Lưu thông báo; trùng id (event giao lại) thì bỏ qua. Lỗi DB được ném ra để consumer chuyển message sang DLQ."""
    created_dt = notif.get("created_at")
    if isinstance(created_dt, str):
        try:
            dt = datetime.fromisoformat(created_dt)
        except Exception:
            dt = datetime.now(timezone.utc)
    elif isinstance(created_dt, datetime):
        dt = created_dt
    else:
        dt = datetime.now(timezone.utc)

    with get_session() as session:
        record = NotificationRecord(
            id=str(notif.get("id")),
            user_id=str(notif.get("user_id")),
            event_type=str(notif.get("event_type", "general")),
            title=str(notif.get("title", "Thông báo")),
            message=str(notif.get("message", "")),
            icon=str(notif.get("icon", "bell")),
            action_url=str(notif.get("action_url", "/")),
            is_read=bool(notif.get("is_read", False)),
            created_at=dt,
        )
        if session.get(NotificationRecord, record.id) is None:
            session.add(record)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                # Hai bản sao cùng event lưu song song: bản kia đã lưu thì bỏ qua, lỗi khác thì ném ra.
                if session.get(NotificationRecord, record.id) is None:
                    raise


def get_user_notifications(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Truy vấn danh sách thông báo của người dùng từ cơ sở dữ liệu."""
    try:
        with get_session() as session:
            records = (
                session.query(NotificationRecord)
                .filter(NotificationRecord.user_id == str(user_id))
                .order_by(desc(NotificationRecord.created_at))
                .limit(limit)
                .all()
            )
            return [r.to_dict() for r in records]
    except Exception as e:
        logger.error("Lỗi khi truy vấn notifications của user [%s]: %s", user_id, e)
        return []


def mark_as_read(user_id: str, notification_id: str) -> bool:
    """Đánh dấu thông báo là đã đọc."""
    try:
        with get_session() as session:
            record = (
                session.query(NotificationRecord)
                .filter(
                    NotificationRecord.id == str(notification_id),
                    NotificationRecord.user_id == str(user_id),
                )
                .first()
            )
            if record:
                record.is_read = True
                session.commit()
                return True
        return False
    except Exception as e:
        logger.error("Lỗi khi đánh dấu đã đọc notification: %s", e)
        return False
