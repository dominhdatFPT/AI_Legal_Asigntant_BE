# Schema API kiểm tra mẫu đơn bằng AI. Không có user_id: người dùng luôn lấy từ JWT.
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_FIELDS = 60
MAX_VALUE_CHARS = 1000


class FormCheckField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=120)
    label: str = Field(default="", max_length=300)
    type: Literal["text", "textarea"]
    value: str = Field(min_length=1, max_length=MAX_VALUE_CHARS)
    section: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=300)
    placeholder: str | None = Field(default=None, max_length=300)
    word_key: str | None = Field(default=None, max_length=120)


class FormCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    procedure_code: str = Field(default="", max_length=40)  # mẫu admin tự tạo có thể không có mã
    form_name: str = Field(default="", max_length=300)
    fields: list[FormCheckField] = Field(max_length=MAX_FIELDS)

    @model_validator(mode="after")
    def validate_unique_keys(self) -> "FormCheckRequest":
        if len({field.key for field in self.fields}) != len(self.fields):
            raise ValueError("Các ô trong fields phải có key khác nhau.")
        return self


class FormCheckResult(BaseModel):
    key: str
    status: Literal["suggest", "warning"]
    suggested_value: str | None = None
    reason: str = ""


class FormCheckOut(BaseModel):
    checked_keys: list[str]
    unchecked_keys: list[str] = []
    results: list[FormCheckResult]
    cached: int = 0  # số ô lấy từ cache, không tốn lượt gọi LLM
    model: str | None = None
