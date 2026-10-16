"""C4 system catalog payloads. Plain-text placeholders, never template execution."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="after")
    @classmethod
    def reject_nul(cls, value):
        if isinstance(value, str) and "\x00" in value:
            raise ValueError("Invalid text")
        return value


class TemplateInput(Payload):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=2000)

    @field_validator("name", "description")
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Blank text")
        return value


class TemplatePatch(TemplateInput):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_visible: StrictBool | None = None

    @model_validator(mode="after")
    def valid_patch(self):
        if not self.model_fields_set or ("name" in self.model_fields_set and self.name is None) or (
            "is_visible" in self.model_fields_set and self.is_visible is None
        ):
            raise ValueError("Empty or null patch")
        return self


class TemplateField(Payload):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    label: str = Field(min_length=1, max_length=200)
    type: Literal["text", "date", "number", "boolean"]
    required: StrictBool
    identity_key: Literal["full_name", "date_of_birth", "identity_number", "issue_date", "issue_place", "permanent_address"] | None = None

    @field_validator("label")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Blank label")
        return value


class VersionInput(Payload):
    content: str = Field(min_length=1, max_length=200000)
    fields: list[TemplateField] = Field(max_length=500)

    @model_validator(mode="after")
    def validate_placeholders(self):
        if not self.content.strip():
            raise ValueError("Blank content")
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate field")
        tokens = re.findall(r"\{\{([a-z][a-z0-9_]*)\}\}", self.content)
        leftover = re.sub(r"\{\{[a-z][a-z0-9_]*\}\}", "", self.content)
        if "{{{" in self.content or "}}}" in self.content or "{{" in leftover or "}}" in leftover or not set(tokens).issubset(names):
            raise ValueError("Invalid or undeclared placeholder")
        if any(field.required and field.name not in tokens for field in self.fields):
            raise ValueError("Required field has no placeholder")
        return self
