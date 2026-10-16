import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import UserRole, UserStatus


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    username: str | None = None
    full_name: str | None
    avatar_url: str | None
    phone: str | None
    role: UserRole
    status: UserStatus
    locked_reason: str | None
    locked_at: datetime | None
    locked_until: datetime | None = None
    created_at: datetime
    updated_at: datetime
