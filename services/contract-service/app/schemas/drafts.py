"""Partial draft values are allowed; generation later enforces required fields."""
from datetime import date
import json
import math
import re
from uuid import UUID

from pydantic import ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, field_validator, model_validator

from app.core.errors import ContractError
from app.schemas.catalog import Payload

Value = StrictStr | StrictInt | StrictFloat | StrictBool | None


class DraftPayload(Payload):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @field_validator("title", check_fields=False)
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Blank title")
        return value

    @field_validator("field_values", check_fields=False)
    @classmethod
    def bounded_values(cls, values):
        if len(json.dumps(values, ensure_ascii=False).encode("utf-8")) > 524288:
            raise ValueError("Values too large")
        for name, value in values.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) or isinstance(value, str) and "\x00" in value:
                raise ValueError("Invalid value")
        return values

    @field_validator("document_ids", check_fields=False)
    @classmethod
    def unique_documents(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("Duplicate document")
        return values


class DraftInput(DraftPayload):
    template_version_id: UUID
    procedure_id: UUID | None = None
    title: str = Field(min_length=1, max_length=200)
    field_values: dict[str, Value] = Field(default_factory=dict, max_length=500)
    document_ids: list[UUID] = Field(default_factory=list, max_length=100)


class DraftPatch(DraftPayload):
    # Only these keys are patchable; template/procedure identity stays pinned.
    title: str | None = Field(default=None, min_length=1, max_length=200)
    field_values: dict[str, Value] | None = Field(default=None, max_length=500)
    document_ids: list[UUID] | None = Field(default=None, max_length=100)

    @field_validator("title", "field_values", "document_ids", mode="before")
    @classmethod
    def not_null(cls, value):
        if value is None:
            raise ValueError("Null patch")
        return value

    @model_validator(mode="after")
    def patch_keys(self):
        if not self.model_fields_set or self.model_fields_set - {"title", "field_values", "document_ids"}:
            raise ValueError("Invalid patch keys")
        return self


def validate_values(values, fields):
    types = {field["name"]: field["type"] for field in fields}
    for name, value in values.items():
        valid = name in types
        if valid and value is not None:
            kind = types[name]
            valid = (
                kind == "text" and isinstance(value, str)
                or kind == "boolean" and type(value) is bool
                or kind == "number" and type(value) in {int, float} and (type(value) is int or math.isfinite(value))
            )
            if kind == "date" and isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                try:
                    date.fromisoformat(value)
                    valid = True
                except ValueError:
                    valid = False
        if not valid:
            raise ContractError("VALIDATION_ERROR", "Trường điền chưa khai báo hoặc sai kiểu dữ liệu.", 422,
                details=[{"field": "field_values." + name, "message": "Kiểm tra tên và kiểu dữ liệu trường."}])
