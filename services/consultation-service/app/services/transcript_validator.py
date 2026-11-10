"""Kiểm bản tóm tắt JSON Agent trả về: đúng schema, source_ids có thật, và đánh dấu `verified` cho giá trị nguyên văn."""

import unicodedata
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCHEMA_VERSION = 1

Domain = Literal["dat_dai", "ho_kinh_doanh", "dan_su", "lao_dong", "hon_nhan_gia_dinh", "hanh_chinh", "hinh_su", "khac"]
Party = Literal["citizen", "lawyer"]
Text300 = Annotated[str, Field(max_length=300)]
SourceIds = Annotated[list[str], Field(min_length=1)]


class SummaryInvalid(ValueError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Issue(_Strict):  # dùng cho citizen_issues và lawyer_advice
    content: Text300
    source_ids: SourceIds


class Fact(_Strict):
    type: Literal["date", "amount", "area", "person", "organization", "document_number", "land_parcel",
                  "location", "other"]
    value: str
    context: Text300
    stated_by: Party
    source_ids: SourceIds


class LegalReference(_Strict):
    document: str | None
    article: str | None
    clause: str | None
    point: str | None
    raw_text: str
    cited_by: Party
    source_ids: SourceIds


class Deadline(_Strict):
    value: str
    description: Text300
    stated_by: Party
    source_ids: SourceIds


class NextStep(_Strict):
    actor: Party
    content: Text300
    source_ids: SourceIds


class Conflict(_Strict):
    topic: Text300
    citizen_statement: Text300
    lawyer_statement: Text300
    source_ids: Annotated[list[str], Field(min_length=2)]


class OpenQuestion(_Strict):
    content: Text300
    source_ids: list[str]


def _list(model):
    return Annotated[list[model], Field(max_length=15)]


class Summary(_Strict):
    has_substance: bool
    legal_domains: list[Domain]
    overview: Annotated[str, Field(max_length=600)] | None
    citizen_issues: _list(Issue)
    facts: _list(Fact)
    lawyer_advice: _list(Issue)
    legal_references: _list(LegalReference)
    deadlines: _list(Deadline)
    next_steps: _list(NextStep)
    conflicts: _list(Conflict)
    open_questions: _list(OpenQuestion)


_LISTS = ("citizen_issues", "facts", "lawyer_advice", "legal_references", "deadlines", "next_steps", "conflicts",
          "open_questions")
_VERBATIM = {"facts": "value", "legal_references": "raw_text", "deadlines": "value"}


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split()).casefold()


def validate_summary(summary: dict, messages: list[dict]) -> dict:
    """Trả bản đã kiểm; facts, legal_references, deadlines có thêm `verified`. Sai cấu trúc → SummaryInvalid."""
    try:
        data = Summary.model_validate(summary).model_dump()
    except ValidationError as e:
        raise SummaryInvalid(str(e)) from None
    if not data["has_substance"] and (data["overview"] is not None or data["legal_domains"]
                                      or any(data[k] for k in _LISTS)):
        raise SummaryInvalid("has_substance=false nhưng còn dữ liệu")

    text_of = {m["id"]: _norm(m["content"]) for m in messages}
    for name in _LISTS:
        for item in data[name]:
            unknown = [i for i in item["source_ids"] if i not in text_of]
            if unknown:
                raise SummaryInvalid(f"{name}: source_ids không có trong đầu vào: {unknown}")
            if name in _VERBATIM:
                needle = _norm(item[_VERBATIM[name]])
                item["verified"] = any(needle in text_of[i] for i in item["source_ids"])
    return data
