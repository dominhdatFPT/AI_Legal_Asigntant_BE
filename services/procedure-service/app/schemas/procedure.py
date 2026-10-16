from enum import Enum
from pydantic import BaseModel, Field, ConfigDict


class Domain(str, Enum):
    land = "land"
    business = "business"


class SubmissionChannel(str, Enum):
    direct = "direct"
    postal = "postal"
    online = "online"


class ReceivingAgencyLevel(str, Enum):
    commune = "commune"
    district = "district"
    province = "province"


class DocumentForm(str, Enum):
    original = "original"
    copy = "copy"
    certified_copy = "certified_copy"


class FeeType(str, Enum):
    administrative_fee = "administrative_fee"
    appraisal_fee = "appraisal_fee"
    other = "other"


class LegalBasis(BaseModel):
    doc_number: str
    title: str
    article_ref: str | None = None
    source_url: str | None = None
    order_index: int = 0


class Requirement(BaseModel):
    id: str
    name: str
    description: str | None = None
    is_required: bool = True
    copies: int = 1
    document_form: str = "original"
    form_code: str | None = None
    note: str | None = None
    order_index: int = 0


class Condition(BaseModel):
    id: str
    condition_code: str | None = None
    title: str
    description: str | None = None
    is_mandatory: bool = True
    order_index: int = 0


class FeeItem(BaseModel):
    id: str
    fee_type: str = "administrative_fee"
    name: str
    amount: int | None = None
    unit: str = "lần"
    is_exemptible: bool = False
    note: str | None = None


class ProcedureSummary(BaseModel):
    id: str
    code: str
    name: str
    domain: Domain | str
    description: str | None = None
    fee: int | None = None
    processing_days: int | None = None


class ProcedureDetail(BaseModel):
    id: str
    code: str
    name: str
    domain: Domain | str
    description: str | None = None
    fee: int | None = None
    fee_note: str | None = None
    processing_days: int | None = None
    submission_channels: list[str] = Field(default_factory=lambda: ["direct"])
    receiving_agency_level: str | None = "commune"
    legal_bases: list[LegalBasis] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    conditions: list[Condition] = Field(default_factory=list)
    fees: list[FeeItem] = Field(default_factory=list)
    effective_from: str | None = None
    updated_at: str | None = None


class Pagination(BaseModel):
    page: int = Field(ge=1, default=1)
    page_size: int = Field(ge=1, le=100, default=20)
    total: int = Field(ge=0, default=0)
    total_pages: int = Field(ge=0, default=0)


class ProcedureSearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=2000)


class DomainSummary(BaseModel):
    code: str
    name: str
    description: str
    count: int = 0
