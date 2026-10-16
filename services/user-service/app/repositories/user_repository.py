import uuid
from datetime import datetime

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.models import CitizenProfile, User, UserRole, UserStatus


def get_by_id(db: Session, user_id: uuid.UUID) -> User | None:
    return db.scalar(select(User).where(User.id == user_id, User.deleted_at.is_(None)))


def get_with_citizen_profile(db: Session, user_id: uuid.UUID) -> tuple[User, CitizenProfile | None] | None:
    """User (chưa xóa) kèm hồ sơ CCCD trong 1 câu query. Không có user → None; chưa khai hồ sơ → (user, None)."""
    row = db.execute(
        select(User, CitizenProfile)
        .outerjoin(CitizenProfile, CitizenProfile.user_id == User.id)
        .where(User.id == user_id, User.deleted_at.is_(None))
    ).first()
    return (row.User, row.CitizenProfile) if row else None


def get_by_firebase_uid(db: Session, firebase_uid: str) -> User | None:
    return db.scalar(select(User).where(User.firebase_uid == firebase_uid, User.deleted_at.is_(None)))


def get_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email, User.deleted_at.is_(None)))


def get_by_username(db: Session, username: str) -> User | None:
    return db.scalar(
        select(User).where(func.lower(User.username) == username.lower(), User.deleted_at.is_(None))
    )


def create(
    db: Session,
    *,
    firebase_uid: str | None,
    email: str,
    full_name: str | None = None,
    avatar_url: str | None = None,
    auth_provider: str | None = None,
) -> User:
    user = User(
        firebase_uid=firebase_uid,
        email=email,
        full_name=full_name,
        avatar_url=avatar_url,
        auth_provider=auth_provider,
    )
    db.add(user)
    db.flush()
    return user


# ---------------------------------------------------------------- Quản lý tài khoản (admin)


def get_by_id_including_deleted(db: Session, user_id: uuid.UUID) -> User | None:
    """Admin xem / khôi phục được cả tài khoản đã xóa mềm."""
    return db.get(User, user_id)


def find_deleted(db: Session, *, firebase_uid: str | None = None, email: str | None = None) -> User | None:
    conditions = []
    if firebase_uid:
        conditions.append(User.firebase_uid == firebase_uid)
    if email:
        conditions.append(func.lower(User.email) == email.lower())
    if not conditions:
        return None
    return db.scalar(select(User).where(User.deleted_at.is_not(None), or_(*conditions)).limit(1))


def find_by_email_or_username(db: Session, value: str) -> User | None:
    """Tìm cả tài khoản đã xóa mềm, dùng khi admin tạo tài khoản mới để báo trùng."""
    value = value.lower()
    return db.scalar(
        select(User).where(or_(func.lower(User.email) == value, func.lower(User.username) == value)).limit(1)
    )


def phone_taken(db: Session, phone: str, except_user_id: uuid.UUID | None = None) -> bool:
    """SĐT là duy nhất, tính cả tài khoản đã xóa mềm (giống email)."""
    query = select(User.id).where(User.phone == phone)
    if except_user_id is not None:
        query = query.where(User.id != except_user_id)
    return db.scalar(query.limit(1)) is not None


def release_expired_locks(db: Session, now: datetime) -> int:
    """Mở khóa mọi tài khoản đã hết hạn khóa (gọi trước khi lọc danh sách theo trạng thái)."""
    result = db.execute(
        update(User)
        .where(User.status == UserStatus.locked, User.locked_until.is_not(None), User.locked_until <= now)
        .values(status=UserStatus.active, locked_reason=None, locked_by=None, locked_at=None, locked_until=None)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount


def search(
    db: Session,
    *,
    role: UserRole | None = None,
    status: UserStatus | None = None,
    q: str | None = None,
    deleted: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[User], int, set[uuid.UUID]]:
    """Trả về (user của trang, tổng số dòng khớp điều kiện, id những user đã khai hồ sơ CCCD)."""
    conditions = [User.deleted_at.is_not(None) if deleted else User.deleted_at.is_(None)]
    if role is not None:
        conditions.append(User.role == role)
    if status is not None:
        conditions.append(User.status == status)
    if q:
        pattern = f"%{q.strip().lower()}%"
        conditions.append(
            or_(
                func.lower(User.email).like(pattern),
                func.lower(func.coalesce(User.full_name, "")).like(pattern),
                func.lower(func.coalesce(User.username, "")).like(pattern),
                func.coalesce(User.phone, "").like(pattern),
            )
        )
    where = and_(*conditions)
    # 1 câu duy nhất: trang dữ liệu + tổng số dòng (window count) + đã khai CCCD chưa.
    # DB ở cloud nên gộp câu giúp bớt các lượt hỏi-đáp qua mạng.
    has_profile = select(CitizenProfile.user_id).where(CitizenProfile.user_id == User.id).exists()
    rows = db.execute(
        select(User, func.count().over().label("total"), has_profile.label("has_profile"))
        .where(where)
        .order_by(User.created_at.desc(), User.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    if rows:
        total = rows[0].total
    elif page > 1:
        # Trang vượt quá cuối danh sách: không có dòng nào để đọc tổng → đếm riêng.
        total = db.scalar(select(func.count()).select_from(User).where(where)) or 0
    else:
        total = 0
    items = [row.User for row in rows]
    with_profile = {row.User.id for row in rows if row.has_profile}
    return items, total, with_profile


def count_summary(db: Session) -> dict:
    """Số đếm cho các tab trên màn hình quản lý (không tính tài khoản đã xóa, trừ ô `deleted`)."""
    alive = User.deleted_at.is_(None)
    by_role = dict(db.execute(select(User.role, func.count()).where(alive).group_by(User.role)).all())
    by_status = dict(db.execute(select(User.status, func.count()).where(alive).group_by(User.status)).all())
    deleted = db.scalar(select(func.count()).select_from(User).where(User.deleted_at.is_not(None))) or 0
    return {
        "total": sum(by_role.values()),
        "by_role": {r.value: by_role.get(r, 0) for r in UserRole},
        "by_status": {s.value: by_status.get(s, 0) for s in UserStatus},
        "deleted": deleted,
    }
