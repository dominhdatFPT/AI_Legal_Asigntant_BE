"""Pydantic Schemas cho hệ thống Report (Ticket & Quick Error) và Thông báo toàn hệ thống (Broadcast)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

SENSITIVE_CONTEXT_KEYS = {
    "password",
    "pass",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
    "secret",
    "cccd",
    "otp",
    "api_key",
}


def sanitize_context_dict(data: Any, depth: int = 0) -> Any:
    """Loại bỏ các trường nhạy cảm (PII/Secret) khỏi context báo lỗi trước khi lưu DB."""
    if depth > 4:
        return "[TRUNCATED]"
    if isinstance(data, dict):
        cleaned: Dict[str, Any] = {}
        for k, v in list(data.items())[:40]:
            key_str = str(k)
            if any(s in key_str.lower() for s in SENSITIVE_CONTEXT_KEYS):
                cleaned[key_str] = "[REDACTED]"
            else:
                cleaned[key_str] = sanitize_context_dict(v, depth + 1)
        return cleaned
    if isinstance(data, list):
        return [sanitize_context_dict(item, depth + 1) for item in data[:25]]
    if isinstance(data, str):
        return data[:1000]
    return data


class TicketCreateRequest(BaseModel):
    category_code: str = Field(..., min_length=2, max_length=40, description="Mã nhóm vấn đề (VD: AI_ASSISTANT, PROCEDURE)")
    reason_code: str = Field(..., min_length=2, max_length=40, description="Mã nguyên nhân cụ thể (VD: AI_WRONG_ANSWER)")
    title: str = Field(..., min_length=10, max_length=150, description="Tiêu đề ngắn gọn của vấn đề")
    description: str = Field(..., min_length=20, max_length=4000, description="Mô tả chi tiết vấn đề")
    feature_code: Optional[str] = Field(default=None, max_length=40)
    target_type: Optional[str] = Field(default=None, max_length=40, description="Loại đối tượng liên quan (ai_message, consultation, form_template...)")
    target_id: Optional[str] = Field(default=None, max_length=64, description="ID đối tượng liên quan")
    attachment_ids: List[str] = Field(default_factory=list, max_length=3, description="Danh sách tối đa 3 ảnh đính kèm")

    @field_validator("title", "description")
    @classmethod
    def strip_text(cls, v: str) -> str:
        val = v.strip()
        if not val:
            raise ValueError("Nội dung không được để trống")
        return val


class QuickErrorCreateRequest(BaseModel):
    reason_code: str = Field(..., min_length=2, max_length=40, description="Mã chip nguyên nhân nhanh (VD: QE_SAVE_FAILED, QE_AI_ERROR)")
    note: Optional[str] = Field(default=None, max_length=500, description="Ghi chú ngắn của người dùng (<= 500 ký tự)")
    feature_code: Optional[str] = Field(default=None, max_length=40, description="Mã tính năng đang thao tác (VD: PROCEDURE_SUBMIT)")
    target_type: Optional[str] = Field(default=None, max_length=40)
    target_id: Optional[str] = Field(default=None, max_length=64)
    context: Dict[str, Any] = Field(default_factory=dict, description="Ngữ cảnh lỗi tự động từ FE (route, error_code, http_status, request_id...)")
    attachment_ids: List[str] = Field(default_factory=list, max_length=3)

    @field_validator("context")
    @classmethod
    def clean_context(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        return sanitize_context_dict(v or {})


class AdminClaimReleaseRequest(BaseModel):
    version: int = Field(default=1, ge=1, description="Phiên bản hiện tại của report (Optimistic Locking)")
    note: Optional[str] = Field(default=None, max_length=1000)


class AdminAcceptResolveRequest(BaseModel):
    version: int = Field(default=1, ge=1, description="Phiên bản hiện tại của report (Optimistic Locking)")
    note: Optional[str] = Field(default=None, max_length=1000, description="Ghi chú phản hồi gửi về cho người dùng/luật sư")


class AdminRejectRequest(BaseModel):
    version: int = Field(default=1, ge=1, description="Phiên bản hiện tại của report (Optimistic Locking)")
    reject_reason_code: str = Field(
        ...,
        min_length=3,
        max_length=40,
        description="Mã lý do từ chối (REJ_DUPLICATE, REJ_NOT_REPRODUCIBLE, REJ_EXPECTED_BEHAVIOR, REJ_INSUFFICIENT_INFO, REJ_OUT_OF_SCOPE, REJ_ABUSE)",
    )
    note: str = Field(..., min_length=10, max_length=1000, description="Giải thích lý do từ chối gửi về cho người báo cáo")
    duplicate_of_code: Optional[str] = Field(default=None, max_length=24, description="Mã report gốc nếu trùng (VD: RPT-2026-000001)")


class BroadcastCreateRequest(BaseModel):
    title: str = Field(..., min_length=5, max_length=150, description="Tiêu đề thông báo hệ thống")
    content: str = Field(..., min_length=10, max_length=4000, description="Nội dung thông báo")
    audience: Literal["all", "citizen", "lawyer", "admin"] = Field(
        default="all",
        description="Đối tượng nhận: 'all' (toàn bộ user + luật sư + cả chính admin đó), hoặc 'citizen', 'lawyer', 'admin'",
    )
    priority: Literal["normal", "high", "critical"] = Field(default="normal")
    channels: List[Literal["in_app", "email", "push"]] = Field(default_factory=lambda: ["in_app"])
    action_url: Optional[str] = Field(default="/", max_length=512)
    scheduled_at: Optional[datetime] = Field(default=None, description="Thời điểm hẹn giờ phát (NULL = phát ngay lập tức)")
    expires_at: Optional[datetime] = Field(default=None, description="Thời điểm thông báo hết hạn hiển thị")
