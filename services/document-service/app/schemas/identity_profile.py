"""Only explicitly user-confirmed six fields. No implicit copy from OCR/User."""
from datetime import date
import re
import unicodedata
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator


class IdentityFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: StrictStr | None = Field(max_length=200)
    date_of_birth: date | None
    identity_number: StrictStr | None = Field(pattern=r"^[0-9]{12}$")
    issue_date: date | None
    issue_place: StrictStr | None = Field(max_length=500)
    permanent_address: StrictStr | None = Field(max_length=2000)

    @field_validator("full_name", "identity_number", "issue_place", "permanent_address", mode="before")
    @classmethod
    def clean_text(cls, value):
        if value is None:
            return None
        if not isinstance(value, str) or any(unicodedata.category(ch).startswith("C") for ch in value):
            raise ValueError("Invalid text")
        value = unicodedata.normalize("NFC", value).strip()
        if not value:
            raise ValueError("Use null for missing fields")
        return value

    @field_validator("date_of_birth", "issue_date", mode="before")
    @classmethod
    def iso_date(cls, value):
        if value is None:
            return None
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("ISO date required")
        parsed = date.fromisoformat(value)
        if parsed > date.today():
            raise ValueError("Future date")
        return parsed

    @model_validator(mode="after")
    def review_fields(self):
        if not any(self.model_dump().values()):
            raise ValueError("At least one confirmed field required")
        if self.date_of_birth and self.issue_date and self.issue_date < self.date_of_birth:
            raise ValueError("Issue date before birth")
        return self


class ConfirmIdentityProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_session_id: UUID | None = None
    source_document_id: UUID | None = None
    expected_revision: StrictInt = Field(ge=0, le=2147483647)
    fields: IdentityFields

    @model_validator(mode="after")
    def one_source(self):
        if (self.source_session_id is None) == (self.source_document_id is None):
            raise ValueError("Exactly one source required")
        return self
