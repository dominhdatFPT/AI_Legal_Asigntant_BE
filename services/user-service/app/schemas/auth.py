from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.admin_user import EMAIL_PATTERN, PHONE_PATTERN, clean_phone


class RefreshTokenRequest(BaseModel):
    """Body không bắt buộc: trình duyệt dùng HttpOnly cookie, Postman/mobile gửi refresh_token trong body."""

    model_config = ConfigDict(str_strip_whitespace=True)

    refresh_token: str | None = Field(default=None, min_length=1)


class FirebaseLoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id_token: str = Field(min_length=1)


class PasswordLoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class RegisterRequest(BaseModel):
    """Người dùng tự đăng ký bằng email + SĐT + mật khẩu (tài khoản `local`, đăng nhập bằng email)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    email: str = Field(min_length=3, max_length=255, pattern=EMAIL_PATTERN)
    phone: str = Field(pattern=PHONE_PATTERN, description="10 chữ số, bắt đầu bằng 0")
    password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(min_length=1, max_length=128)

    @field_validator("phone", mode="before")
    @classmethod
    def clean_phone(cls, value):
        return clean_phone(value)

    @field_validator("email")
    @classmethod
    def lower_email(cls, value: str) -> str:
        return value.lower()

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError("Mật khẩu xác nhận không khớp.")
        return self
