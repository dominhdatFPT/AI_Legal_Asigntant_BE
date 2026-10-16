"""Dữ liệu gửi lên / trả về của hồ sơ cá nhân theo CCCD.

Ràng buộc chi tiết từng trường (định dạng CCCD, số điện thoại, ngày tháng…) để bước validate sau.
"""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Address(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    detail: str      # số nhà, đường, thôn/xóm
    ward: str        # xã/phường/đặc khu
    province: str    # tỉnh/thành phố


class CitizenProfileRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    # Định danh
    id_number: str
    full_name: str
    date_of_birth: date
    gender: str
    nationality: str
    # Thẻ
    issue_date: date
    issue_place: str
    expiry_date: date | None = None
    # Địa chỉ
    place_of_origin: str | None = None
    place_of_residence: str | None = None
    permanent_address: Address
    current_same_as_permanent: bool
    current_address: Address | None = None
    # Nhập tay
    phone: str
    ethnicity: str | None = None
    tax_code: str | None = None
    # Nguồn dữ liệu
    source: Literal["ekyc", "manual"]
    ekyc_id: uuid.UUID | None = None
    # Xác nhận (2 ô tick trên form)
    declared: bool   # cam đoan thông tin đúng sự thật
    consented: bool  # đồng ý lưu và dùng thông tin cá nhân


class CitizenProfileOut(BaseModel):
    user_id: uuid.UUID
    id_number: str
    full_name: str
    date_of_birth: date
    gender: str
    nationality: str
    issue_date: date
    issue_place: str
    expiry_date: date | None
    place_of_origin: str | None
    place_of_residence: str | None
    permanent_address: Address
    current_same_as_permanent: bool
    current_address: Address | None
    phone: str | None
    email: str
    ethnicity: str | None
    tax_code: str | None
    source: str
    ekyc_id: uuid.UUID | None
    declared_at: datetime
    consented_at: datetime
    updated_at: datetime
