from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class FieldType(str, Enum):
    text = "text"
    textarea = "textarea"
    number = "number"
    date = "date"
    select = "select"
    checkbox = "checkbox"
    radio = "radio"


class FormFieldOption(BaseModel):
    value: str
    label: str


class FormField(BaseModel):
    name: str = Field(..., description="Tên biến/cột tương ứng trong bảng dữ liệu")
    label: str = Field(..., description="Nhãn tiếng Việt hiển thị trên giao diện form")
    field_type: FieldType = Field(default=FieldType.text, description="Loại ô nhập liệu")
    required: bool = Field(default=True, description="Trường bắt buộc hay không")
    placeholder: Optional[str] = Field(default=None, description="Gợi ý nhập liệu")
    default_value: Optional[Any] = Field(default=None, description="Giá trị mặc định ban đầu")
    options: Optional[List[FormFieldOption]] = Field(default=None, description="Danh sách lựa chọn nếu là select/radio")
    section: str = Field(default="general", description="Khối nhóm thông tin")
    description: Optional[str] = Field(default=None, description="Ghi chú hướng dẫn điền")
    validation_regex: Optional[str] = Field(default=None, description="Biểu thức chính quy kiểm tra định dạng")


class FormSection(BaseModel):
    key: str
    title: str
    description: Optional[str] = None
    order: int = 0


class ProcedureFormSchema(BaseModel):
    procedure_code: str
    procedure_name: str
    domain: str
    target_table: str
    template_filename: Optional[str] = None
    sections: List[FormSection]
    fields: List[FormField]


class ApplicationSubmitRequest(BaseModel):
    # Chủ hồ sơ lấy từ JWT; gửi kèm user_id hay trường lạ → 422.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    procedure_code: str = Field(min_length=1, max_length=50)
    status: Literal["draft", "submitted"] = "draft"
    form_data: Dict[str, Any] = Field(default_factory=dict, description="Dữ liệu các biến theo form schema")
    extra_data: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Dữ liệu mở rộng bổ sung")


class ApplicationRecordResponse(BaseModel):
    id: str
    user_id: Optional[str] = None
    procedure_code: str
    procedure_name: Optional[str] = None
    domain: str
    target_table: str
    status: str
    applicant_name: Optional[str] = None
    form_data: Dict[str, Any]
    extra_data: Optional[Dict[str, Any]] = None
    created_at: str
    updated_at: str
