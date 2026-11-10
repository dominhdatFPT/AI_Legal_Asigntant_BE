"""Body gửi lên của Consultation Service (khớp openapi.yaml). Kết quả trả về dựng ở app/services/views.py."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("Không được chỉ chứa khoảng trắng.")
    return value


class CreateConsultationRequest(_Body):
    question: str = Field(min_length=1, max_length=2000)
    source: Literal["lobby", "direct"]
    agent_session_id: uuid.UUID | None = None
    lawyer_id: uuid.UUID | None = None

    _question_not_blank = field_validator("question")(_not_blank)


class SendMessageRequest(_Body):
    content: str = Field(min_length=1, max_length=5000)

    _content_not_blank = field_validator("content")(_not_blank)


class AcceptTermsRequest(_Body):
    terms_version: str = Field(min_length=1, max_length=20)
