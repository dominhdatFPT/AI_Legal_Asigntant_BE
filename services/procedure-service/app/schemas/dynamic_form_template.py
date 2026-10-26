from __future__ import annotations
import json
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class DomainEnum(str, Enum):
    dat_dai = "dat_dai"
    ho_kinh_doanh = "ho_kinh_doanh"
    doanh_nghiep = "doanh_nghiep"


class TemplateStatus(str, Enum):
    draft = "draft"
    published = "published"
    hidden = "hidden"
    archived = "archived"


class FieldTypeEnum(str, Enum):
    text = "text"
    textarea = "textarea"
    number = "number"
    date = "date"
    past_date = "past_date"
    dob = "dob"
    issue_date = "issue_date"
    cccd = "cccd"
    phone = "phone"
    email = "email"
    select = "select"


class BlockKeyEnum(str, Enum):
    personal_info = "personal_info"
    dob = "dob"
    cccd = "cccd"
    address = "address"
    phone = "phone"
    email = "email"
    tax_code = "tax_code"
    organization = "organization"
    business_lines = "business_lines"
    land_parcel = "land_parcel"
    receiving_agency = "receiving_agency"
    free_field = "free_field"


class FieldIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    # Nháp cho phép trường tự do chưa có tiêu đề; validate_for_publish chặn khi xuất bản.
    label: str = Field(default="", max_length=255)
    type: FieldTypeEnum
    required: bool = True
    autofill: Optional[str] = Field(default=None, pattern=r"^profile\.[a-z_]+$")
    options: Optional[List[str]] = None


class BlockIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    instance_id: str = Field(pattern=r"^[a-z_]+_[0-9]+$")
    block_key: BlockKeyEnum
    order: int = Field(ge=1)
    fields: List[FieldIn] = Field(default_factory=list)


class SectionIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(pattern=r"^sec_[0-9]+$")
    title: str = Field(default="", max_length=255)
    order: int = Field(ge=1)
    blocks: List[BlockIn] = Field(default_factory=list)


class ContentIn(BaseModel):
    """Body nhận từ Admin khi POST (tạo mới) hoặc PUT (sửa nháp).
    Validate mềm: cho phép sections rỗng, title rỗng, blocks rỗng khi admin đang soạn dở.
    """
    model_config = ConfigDict(extra="ignore")

    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=255)
    code: Optional[str] = Field(default=None, max_length=50)
    domain: DomainEnum
    procedure_id: Optional[str] = None
    sections: List[SectionIn] = Field(default_factory=list)

    @field_validator("sections", mode="before")
    @classmethod
    def limit_content_size(cls, v):
        raw = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
        if len(raw.encode("utf-8")) > 262_144:  # 256 KB
            raise ValueError("Nội dung mẫu đơn quá lớn (tối đa 256 KB).")
        return v


def validate_for_publish(content: ContentIn) -> List[Dict[str, str]]:
    """Kiểm tra nghiêm ngặt cấu trúc mẫu trước khi xuất bản.
    Trả về danh sách lỗi [{field, message}]. Nếu rỗng là đạt chuẩn xuất bản.
    """
    errors: List[Dict[str, str]] = []
    if not content.sections or len(content.sections) == 0:
        errors.append({"field": "sections", "message": "Mẫu đơn phải có ít nhất 1 mục."})
        return errors

    seen_sec_ids = set()
    seen_instance_ids = set()

    for si, sec in enumerate(content.sections):
        prefix = f"sections[{si}]"

        if sec.id in seen_sec_ids:
            errors.append({"field": f"{prefix}.id", "message": f"Mã mục '{sec.id}' bị trùng lặp."})
        seen_sec_ids.add(sec.id)

        if not sec.title or not sec.title.strip():
            errors.append({"field": f"{prefix}.title", "message": "Tiêu đề mục không được để trống."})

        if not sec.blocks or len(sec.blocks) == 0:
            errors.append({"field": f"{prefix}.blocks", "message": "Mỗi mục phải có ít nhất 1 thẻ."})
            continue

        for bi, block in enumerate(sec.blocks):
            bprefix = f"{prefix}.blocks[{bi}]"

            if block.instance_id in seen_instance_ids:
                errors.append({
                    "field": f"{bprefix}.instance_id",
                    "message": f"Mã thẻ (instance_id) '{block.instance_id}' bị trùng lặp trong toàn bộ mẫu."
                })
            seen_instance_ids.add(block.instance_id)

            if block.block_key == BlockKeyEnum.free_field:
                if not block.fields or len(block.fields) == 0 or not block.fields[0].label or not block.fields[0].label.strip():
                    errors.append({
                        "field": f"{bprefix}.fields[0].label",
                        "message": "Trường tự do bắt buộc phải có tiêu đề (label)."
                    })

            for fi, fld in enumerate(block.fields):
                fprefix = f"{bprefix}.fields[{fi}]"
                if fld.type == FieldTypeEnum.select and (not fld.options or len(fld.options) == 0):
                    errors.append({
                        "field": f"{fprefix}.options",
                        "message": f"Trường lựa chọn (select) '{fld.key}' phải có danh sách options."
                    })

    return errors


class TemplateOut(BaseModel):
    id: str
    template_group_id: str
    version: int
    status: str
    name: str
    domain: str
    procedure_id: Optional[str] = None
    content: Dict[str, Any]
    created_at: str
    updated_at: str
    deleted_at: Optional[str] = None
