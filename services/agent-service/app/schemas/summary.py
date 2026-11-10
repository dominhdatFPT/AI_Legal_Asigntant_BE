# Pydantic schemas cho API nội bộ tóm tắt hội thoại (theo openapi.yaml)
import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator


class SummaryRequest(BaseModel):
    user_id: uuid.UUID  # người tạo yêu cầu tư vấn; phải là chủ hội thoại
    until: datetime  # chỉ dùng tin gửi trước mốc này (lúc user gửi yêu cầu tư vấn)

    @field_validator("until")
    @classmethod
    def has_timezone(cls, value: datetime) -> datetime:
        # Thiếu múi giờ thì không biết là giờ nào, so với created_at dễ lệch nên từ chối thay vì đoán
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("until phải có múi giờ, ví dụ 2026-11-02T09:00:00Z")
        return value


class SummaryOut(BaseModel):
    conversation_id: uuid.UUID
    summary: str
