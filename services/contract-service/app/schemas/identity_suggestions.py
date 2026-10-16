"""C6 explicit bindings and the exact confirmed Document profile receipt."""
from datetime import date, datetime, timezone
import re
import unicodedata
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictInt, StrictStr, field_validator, model_validator

from app.schemas.catalog import Payload

IdentityKey = Literal["full_name", "date_of_birth", "identity_number", "issue_date", "issue_place", "permanent_address"]
DATE_KEYS = {"date_of_birth", "issue_date"}


class Binding(Payload):
    field_name: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    identity_key: IdentityKey


class SuggestionInput(Payload):
    bindings: list[Binding] = Field(max_length=100)

    @model_validator(mode="after")
    def unique_bindings(self):
        names = [binding.field_name for binding in self.bindings]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate binding")
        return self


class ConfirmedFields(Payload):
    full_name: StrictStr | None = Field(max_length=200)
    date_of_birth: StrictStr | None
    identity_number: StrictStr | None = Field(pattern=r"^[0-9]{12}$")
    issue_date: StrictStr | None
    issue_place: StrictStr | None = Field(max_length=500)
    permanent_address: StrictStr | None = Field(max_length=2000)

    @field_validator("*")
    @classmethod
    def valid_text(cls, value):
        if value is not None and (
            not value.strip() or any(unicodedata.category(ch).startswith("C") for ch in value)
        ):
            raise ValueError("Invalid confirmed text")
        return value

    @field_validator("date_of_birth", "issue_date")
    @classmethod
    def valid_date(cls, value):
        if value is not None:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                raise ValueError("ISO date required")
            if date.fromisoformat(value) > datetime.now(timezone.utc).date():
                raise ValueError("Future identity date")
        return value

    @model_validator(mode="after")
    def coherent(self):
        values = self.model_dump()
        if not any(value is not None for value in values.values()):
            raise ValueError("No confirmed fields")
        if self.date_of_birth and self.issue_date and self.issue_date < self.date_of_birth:
            raise ValueError("Issue date precedes birth")
        return self


class ConfirmedProfile(Payload):
    owner_user_id: UUID
    source_session_id: UUID | None
    source_document_id: UUID | None
    revision: StrictInt = Field(ge=1, le=2147483647)
    confirmed_at: StrictStr
    updated_at: StrictStr
    fields: ConfirmedFields

    @field_validator("owner_user_id", "source_session_id", "source_document_id", mode="before")
    @classmethod
    def uuid_strings(cls, value):
        if value is not None and not isinstance(value, str):
            raise ValueError("UUID string required")
        return value

    @field_validator("confirmed_at", "updated_at")
    @classmethod
    def timestamps(cls, value):
        if "T" not in value or datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("Timestamp with timezone required")
        return value

    @model_validator(mode="after")
    def confirmed_source(self):
        if (self.source_session_id is None) == (self.source_document_id is None):
            raise ValueError("Exactly one confirmed source required")
        if datetime.fromisoformat(self.updated_at.replace("Z", "+00:00")) < datetime.fromisoformat(self.confirmed_at.replace("Z", "+00:00")):
            raise ValueError("Invalid confirmation timestamps")
        return self
