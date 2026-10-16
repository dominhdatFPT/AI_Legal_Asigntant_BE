"""Tách trường từ chữ OCR của thẻ luật sư (Liên đoàn Luật sư Việt Nam cấp). Không đoán: không chắc thì None.

Thẻ thường có: "ĐOÀN LUẬT SƯ ...", "THẺ LUẬT SƯ", "Họ và tên: ...", "Số thẻ: 12345/LS" (hoặc "Số: ..."),
"Ngày cấp: 12/05/2020" (hoặc "..., ngày 12 tháng 05 năm 2020").
"""

import re
from datetime import date

from app.services.identity_parser import folded

FIELDS = ("full_name", "bar_card_number", "bar_association", "issue_date")
_NUMBER = re.compile(r"[0-9][0-9A-Za-z/.\-]{0,48}[0-9A-Za-z]|[0-9]")
_DMY = re.compile(r"(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{4})")
_WORDS = re.compile(r"ngay\s+(\d{1,2})\s+thang\s+(\d{1,2})\s+nam\s+(\d{4})")


def _after_label(text: str, folded_text: str, labels: tuple[str, ...]) -> str | None:
    for label in labels:
        index = folded_text.find(label)
        if index >= 0:
            # folded() giữ nguyên số ký tự (chỉ bỏ dấu), nên cắt bản gốc theo cùng vị trí.
            value = text[index + len(label):].lstrip(" :.-–").strip()
            return value or ""
    return None


def _date(day: str, month: str, year: str) -> str | None:
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def parse_lawyer_card(lines: list[dict]) -> dict:
    texts = [" ".join(str(line.get("text", "")).split()) for line in lines]
    texts = [t for t in texts if t]
    fields = dict.fromkeys(FIELDS)

    for i, text in enumerate(texts):
        low = folded(text)
        if fields["full_name"] is None:
            value = _after_label(text, low, ("ho va ten", "ho ten"))
            if value is not None:
                value = value or (texts[i + 1] if i + 1 < len(texts) else "")
                if 1 <= len(value) <= 200 and not any(ch.isdigit() for ch in value):
                    fields["full_name"] = value.upper()
        if fields["bar_card_number"] is None and re.match(r"^\s*so(\s+the)?\b", low):
            match = _NUMBER.search(text[3:])
            if match:
                fields["bar_card_number"] = match.group(0).upper()
        if fields["bar_association"] is None and "doan luat su" in low and "lien doan" not in low:
            if len(text) <= 200:
                fields["bar_association"] = text
        if fields["issue_date"] is None and ("ngay cap" in low or re.search(r"ngay\s+\d", low)):
            match = _DMY.search(text) or _WORDS.search(low)
            if match:
                fields["issue_date"] = _date(*match.groups())
    return fields


def is_readable(fields: dict) -> bool:
    """Đọc được ít nhất họ tên hoặc số thẻ thì coi là OCR xong (người dùng vẫn phải kiểm tra lại)."""
    return bool(fields.get("full_name") or fields.get("bar_card_number"))
