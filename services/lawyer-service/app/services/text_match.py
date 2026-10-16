"""So khớp họ tên / số thẻ cho cờ "không khớp" khi admin duyệt: bỏ dấu tiếng Việt, hoa thường, khoảng trắng."""

import unicodedata


def _fold(value: str) -> str:
    value = value.replace("đ", "d").replace("Đ", "D")
    return "".join(ch for ch in unicodedata.normalize("NFD", value) if unicodedata.category(ch) != "Mn").upper()


def normalize_name(value: str | None) -> str:
    return " ".join(_fold(value or "").split())


def normalize_card(value: str | None) -> str:
    return "".join(_fold(value or "").split())


def is_mismatch(left: str | None, right: str | None, normalize) -> bool | None:
    """None khi thiếu một bên (không đủ dữ liệu để so)."""
    a, b = normalize(left), normalize(right)
    if not a or not b:
        return None
    return a != b
