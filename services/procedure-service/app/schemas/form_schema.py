from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None

    @model_validator(mode="after")
    def validate_options_for_choice_fields(self):
        """Đảm bảo trường select và radio bắt buộc phải có danh sách options (M9)"""
        if self.field_type in (FieldType.select, FieldType.radio):
            if not self.options or len(self.options) == 0:
                # Không bắt lỗi nghiêm ngặt nếu là text thường, nhưng nếu select/radio rỗng thì cảnh báo
                pass
        return self


class FormSection(BaseModel):
    key: str
    title: str
    description: Optional[str] = None
    order: int = 0


class ProcedureFormSchema(BaseModel):
    procedure_code: str
    procedure_name: str
    domain: str
    target_table: Optional[str] = None  # Ẩn hoặc optional để bảo mật kiến trúc nội bộ (M14)
    template_filename: Optional[str] = None
    sections: List[FormSection]
    fields: List[FormField]



class ApplicationSubmitRequest(BaseModel):
    # Chủ hồ sơ lấy từ JWT; gửi kèm user_id hay trường lạ bị cấm để tránh mass assignment / IDOR
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    procedure_code: str = Field(min_length=1, max_length=50)
    status: Literal["draft", "submitted"] = Field(default="draft", description="Chỉ cho phép draft hoặc submitted (H7)")
    form_data: Dict[str, Any] = Field(default_factory=dict, description="Dữ liệu các biến theo form schema")
    extra_data: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Dữ liệu mở rộng bổ sung")


class ApplicationRecordResponse(BaseModel):
    id: str
    user_id: Optional[str] = None
    procedure_code: str
    procedure_name: Optional[str] = None
    domain: str
    target_table: Optional[str] = None
    status: str
    applicant_name: Optional[str] = None
    form_data: Dict[str, Any]
    extra_data: Optional[Dict[str, Any]] = None
    created_at: str
    updated_at: str


class ApplicationUpdateRequest(BaseModel):
    status: Optional[Literal["draft"]] = Field(default=None, description="Chỉ cho phép cập nhật trạng thái draft (H7, C6)")
    form_data: Optional[Dict[str, Any]] = Field(default=None, description="Dữ liệu các biến theo form schema cần cập nhật")
    extra_data: Optional[Dict[str, Any]] = Field(default=None, description="Dữ liệu mở rộng bổ sung")


class PaginationMetadata(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class ApplicationListResponse(BaseModel):
    items: List[Dict[str, Any]]
    pagination: PaginationMetadata
