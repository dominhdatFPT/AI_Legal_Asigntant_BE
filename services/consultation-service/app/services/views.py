"""Dựng dữ liệu trả về theo schema trong openapi.yaml."""

import math
import uuid

from sqlalchemy.orm import Session

from app.models.consultation import Consultation, ConsultationMessage, as_utc
from app.repositories import consultation_repository as repo


def message_view(m: ConsultationMessage) -> dict:
    return {"id": m.id, "consultation_id": m.consultation_id, "sender_id": m.sender_id, "content": m.content,
            "is_read": m.is_read, "created_at": as_utc(m.created_at)}


def consultation_views(db: Session, consultations: list[Consultation], viewer_id: uuid.UUID) -> list[dict]:
    ids = [c.id for c in consultations]
    last = repo.last_messages(db, ids)
    unread = repo.unread_counts(db, ids, viewer_id)
    return [{
        "id": c.id,
        "citizen_id": c.citizen_user_id,
        "lawyer_id": c.lawyer_user_id,
        "question": c.question,
        "source": c.source,
        "agent_session_id": c.agent_session_id,
        "status": c.status,
        "close_reason": c.close_reason,
        "accepted_at": as_utc(c.accepted_at),
        "closed_by": c.closed_by,
        "closed_at": as_utc(c.closed_at),
        "last_message": message_view(last[c.id]) if c.id in last else None,
        "unread_count": unread.get(c.id, 0),
        "created_at": as_utc(c.created_at),
        "updated_at": as_utc(c.updated_at),
    } for c in consultations]


def consultation_view(db: Session, consultation: Consultation, viewer_id: uuid.UUID) -> dict:
    return consultation_views(db, [consultation], viewer_id)[0]


def paginated(items: list[dict], total: int, page: int, page_size: int) -> dict:
    return {"items": items,
            "pagination": {"page": page, "page_size": page_size, "total": total,
                           "total_pages": math.ceil(total / page_size)}}
