from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from app.schemas.catalog import Payload, TemplateField


class ImportInput(Payload):
    document_id: UUID
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def nonblank(cls,value):
        if not value.strip():
            raise ValueError("Blank name")
        return value


class PdfPlacement(Payload):
    kind: Literal["pdf_box"]
    page: Annotated[StrictInt, Field(ge=1, le=100)]
    x: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)
    y: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)
    width: float = Field(gt=0, le=1, allow_inf_nan=False, strict=True)
    height: float = Field(gt=0, le=1, allow_inf_nan=False, strict=True)
    font_size: float = Field(ge=8, le=72, allow_inf_nan=False, strict=True)
    multiline: StrictBool

    @model_validator(mode="after")
    def bounds(self):
        if self.x+self.width > 1 or self.y+self.height > 1:
            raise ValueError("Outside page")
        return self


class DocxPlacement(Payload):
    kind: Literal["docx_anchor"]
    locator: str = Field(min_length=1, max_length=500, pattern=r"^word/(document|header[0-9]+|footer[0-9]+)\.xml:[0-9]+:[0-9]+:[0-9]+$")
    preview_boxes: list[PdfPlacement] = Field(min_length=1, max_length=20)


class MappedField(TemplateField):
    confidence: float | None = Field(ge=0, le=1, allow_inf_nan=False)
    placements: list[Annotated[PdfPlacement | DocxPlacement, Field(discriminator="kind")]] = Field(min_length=1, max_length=20)


class MappingInput(Payload):
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    fields: list[MappedField] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique(self):
        if len({f.name for f in self.fields}) != len(self.fields):
            raise ValueError("Duplicate field")
        if any(f.identity_key and f.type != ("date" if f.identity_key in {"date_of_birth","issue_date"} else "text") for f in self.fields):
            raise ValueError("Invalid identity binding")
        return self


class ConfirmInput(Payload):
    expected_revision: Annotated[StrictInt, Field(ge=0)]
