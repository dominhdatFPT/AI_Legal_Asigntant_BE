import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models import UserRole, UserStatus

# Chưa có chức năng admin duyệt hồ sơ: khai xong hồ sơ CCCD thì tạm coi là "approved".
# Khi làm chức năng duyệt sẽ thêm "pending" / "rejected", các field khác giữ nguyên.
ProfileStatus = Literal["not_submitted", "approved"]

# Lý do không được tạo bộ hồ sơ (đơn từ), null khi được phép.
BlockReason = Literal["ACCOUNT_LOCKED", "PROFILE_NOT_SUBMITTED"]


class UserStatusOut(BaseModel):
    """Trạng thái tài khoản cho service khác (Contract Service kiểm tra trước khi tạo bộ hồ sơ)."""

    user_id: uuid.UUID
    role: UserRole
    status: UserStatus
    is_locked: bool
    locked_until: datetime | None
    has_citizen_profile: bool
    profile_status: ProfileStatus
    can_create_dossier: bool
    block_reason: BlockReason | None
    block_message: str | None
