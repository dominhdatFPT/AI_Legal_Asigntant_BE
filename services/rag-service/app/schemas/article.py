from pydantic import BaseModel, ConfigDict, Field


class ArticleRequest(BaseModel):
    """Body của POST /internal/v1/rag/articles (xem openapi.yaml -> ArticleRequest)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    law: str = Field(min_length=1, max_length=200, description="Tên hoặc số hiệu văn bản, vd 'Luật Đất đai 2024'")
    dieu: str = Field(pattern=r"^\d{1,4}[a-zA-Zđ]?$", description="Số Điều, vd '45', '10a'")
    khoan: str | None = Field(default=None, pattern=r"^\d{1,3}$", description="Số Khoản (không bắt buộc)")
