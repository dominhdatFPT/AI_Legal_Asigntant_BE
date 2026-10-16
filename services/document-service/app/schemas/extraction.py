from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Bbox(StrictModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def inside(self):
        if self.x+self.width>1 or self.y+self.height>1:
            raise ValueError("Outside page")
        return self


class TextBlock(StrictModel):
    text: str = Field(max_length=10000)
    bbox: Bbox
    confidence: float | None = Field(ge=0, le=1)


class Page(StrictModel):
    page: int = Field(ge=1, le=100)
    width_pt: float = Field(gt=0, allow_inf_nan=False)
    height_pt: float = Field(gt=0, allow_inf_nan=False)
    rotation_degrees: Literal[0,90,180,270]
    method: Literal["text","ocr"]
    blocks: list[TextBlock] = Field(max_length=5000)


class ExtractionResult(StrictModel):
    schema_version: Literal["1.0"]
    source_kind: Literal["docx","pdf_text","pdf_scan","pdf_mixed","image"]
    text: str = Field(max_length=1000000)
    pages: list[Page] = Field(max_length=100)
    extracted_fields: list = Field(max_length=0)
    warnings: list[str] = Field(max_length=100)
    engine_version: str = Field(min_length=1,max_length=100)

    @model_validator(mode="after")
    def page_order(self):
        if self.source_kind=="docx" and self.pages:
            raise ValueError("DOCX pages unavailable")
        if [p.page for p in self.pages] != list(range(1,len(self.pages)+1)):
            raise ValueError("Page sequence")
        if any(len(w)>500 for w in self.warnings):
            raise ValueError("Warning too large")
        return self
