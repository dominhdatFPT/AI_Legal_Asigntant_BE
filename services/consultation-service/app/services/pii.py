"""Che thông tin cá nhân trước khi gửi nội dung chat cho Agent (LLM). Placeholder cố định: Agent giữ nguyên chúng."""

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CCCD = re.compile(r"(?<![\d.,])\d{12}(?![\d])")
# shortcut: CMND 9 số liền chỉ bỏ qua khi kèm đơn vị tiền ngay sau; viết "850000000" trơ trụi vẫn bị che, nâng cấp khi gặp thực tế.
_CMND = re.compile(r"(?<![\d.,])\d{9}(?![\d.,]|\s*(?:đồng|đ|vnd|vnđ|triệu|tỷ|nghìn)\b)", re.IGNORECASE)
_PHONE = re.compile(r"(?<![\d.,])(?:\+84|84|0)(?:[\s.\-]?\d){9}(?!\d)")
_STK = re.compile(r"((?:\bstk\b|số tài khoản|tài khoản|\btk\b)[^\d\n]{0,15})(\d[\d ]{6,18}\d)", re.IGNORECASE)


def mask_pii(text: str) -> str:
    text = _EMAIL.sub("[EMAIL]", text)
    text = _CCCD.sub("[CCCD]", text)
    text = _CMND.sub("[CCCD]", text)
    text = _PHONE.sub("[SĐT]", text)
    return _STK.sub(r"\1[STK]", text)
