import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import RefreshToken


def add(
    db: Session,
    *,
    user_id: uuid.UUID,
    token_hash: str,
    expires_at: datetime,
    user_agent: str | None = None,
) -> RefreshToken:
    token = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at, user_agent=user_agent)
    db.add(token)
    db.flush()
    return token


def get_by_hash(db: Session, token_hash: str, for_update: bool = False) -> RefreshToken | None:
    query = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    if for_update:
        query = query.with_for_update()
    return db.scalar(query)


def has_any(db: Session, user_id: uuid.UUID) -> bool:
    """Tài khoản đã từng đăng nhập (có refresh token, kể cả đã thu hồi) chưa."""
    return db.scalar(select(RefreshToken.id).where(RefreshToken.user_id == user_id).limit(1)) is not None


def revoke(db: Session, token: RefreshToken) -> None:
    if token.revoked_at is None:
        token.revoked_at = datetime.now(timezone.utc)
        db.flush()


ROLE_CHANGED = "role_changed"


def revoke_all_for_user(db: Session, user_id: uuid.UUID, reason: str | None = None) -> int:
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc), revoked_reason=reason)
    )
    db.flush()
    return result.rowcount
