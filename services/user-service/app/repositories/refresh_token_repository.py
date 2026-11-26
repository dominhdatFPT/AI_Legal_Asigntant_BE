import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import RefreshToken, User


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


def _lock_user(db: Session, user_id: uuid.UUID) -> None:
    # All token mutations acquire the user row first, then token rows.
    db.scalar(select(User.id).where(User.id == user_id).with_for_update())


ROTATED_PREFIX = "rotated:"


def get_by_hash(db: Session, token_hash: str, for_update: bool = False) -> RefreshToken | None:
    hashes = (token_hash, ROTATED_PREFIX + token_hash)
    if for_update:
        user_id = db.scalar(select(RefreshToken.user_id).where(RefreshToken.token_hash.in_(hashes)))
        if user_id is None:
            return None
        _lock_user(db, user_id)
    query = select(RefreshToken).where(RefreshToken.token_hash.in_(hashes)).execution_options(populate_existing=True)
    if for_update:
        query = query.with_for_update()
    return db.scalar(query)


def is_rotation(token: RefreshToken) -> bool:
    return token.revoked_at is not None and token.token_hash.startswith(ROTATED_PREFIX)


def _clear_rotation_markers(db: Session, user_id: uuid.UUID) -> None:
    # Preserve the original digest and revocation time; remove grace eligibility.
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.token_hash.startswith(ROTATED_PREFIX))
        .values(token_hash=func.substr(RefreshToken.token_hash, len(ROTATED_PREFIX) + 1))
        .execution_options(synchronize_session="fetch")
    )


def revoke(db: Session, token: RefreshToken, *, rotated: bool = False) -> None:
    _lock_user(db, token.user_id)
    if not rotated:
        # A logout also cancels grace for earlier rotations of this user's tokens.
        _clear_rotation_markers(db, token.user_id)
    if token.revoked_at is None:
        token.revoked_at = datetime.now(timezone.utc)
        if rotated:
            token.token_hash = ROTATED_PREFIX + token.token_hash
        db.flush()


def revoke_all_for_user(db: Session, user_id: uuid.UUID) -> int:
    _lock_user(db, user_id)
    # Include previously rotated rows, even if every token is already revoked.
    _clear_rotation_markers(db, user_id)
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    db.flush()
    return result.rowcount
