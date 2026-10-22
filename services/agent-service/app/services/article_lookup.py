"""Nhận ra câu "tra cứu nguyên văn điều luật" để trả nguyên văn từ kho, không gọi AI.

Ví dụ được coi là tra cứu: "Điều 45 Luật Đất đai", "khoản 2 điều 220 luật đất đai 2024 quy định gì",
"cho tôi xem điều 38 luật doanh nghiệp", "dieu 45 luat dat dai", "Luật Đất đai điều 45".
Câu có nhắc điều luật nhưng hỏi việc khác thì KHÔNG phải tra cứu, đi luồng hỏi đáp như cũ:
"Theo Điều 45 Luật Đất đai thì tôi có được tặng cho đất không?".
Quy tắc: tìm cụm "Điều X [khoản Y] Luật Z" (hoặc "Luật Z điều X"); phần còn lại của câu chỉ được là
từ đệm kiểu "cho tôi xem", "nội dung", "quy định gì". Có từ nào khác (tặng, tôi có được...) thì là câu hỏi.
"""
import re
import unicodedata

_DIEU = r"(?:điều|dieu)\s+(?P<dieu>\d{1,4}[a-zđ]?)\b"
_KHOAN_BEFORE = r"(?:(?:khoản|khoan)\s+(?P<k1>\d{1,3})\s+(?:(?:của|cua)\s+)?)?"
_KHOAN_AFTER = r"(?:\s*,?\s*(?:khoản|khoan)\s+(?P<k2>\d{1,3}))?"
_LAW_WORD = r"(?:bộ\s+luật|bo\s+luat|luật|luat)"
_LINK = r"\s*,?\s+(?:(?:của|cua|trong|thuộc|thuoc)\s+)?"
_LAW = rf"(?P<law>{_LAW_WORD}\s+[^?.!;,\n]+?|\d{{1,4}}/\d{{4}}/[\w-]+)"
_STOP_LOOKAHEAD = r"(?=\s+(?:thì|thi|mà|ma|để|de|nếu|neu|có|co|được|duoc|không|khong|ko|quy|nói|noi|là|la|về|ve|như|nhu|thế|the|ra|sao|hay|hoặc|hoac|cho|xem|gì|gi|nào|nao|ạ|a|nhé|nhe|với|voi|theo|đối|doi|điều|dieu|khoản|khoan)\b|[?.!;,\n]|$)"
_PATTERNS = [
    re.compile(_KHOAN_BEFORE + _DIEU + _KHOAN_AFTER + _LINK + _LAW + _STOP_LOOKAHEAD, re.IGNORECASE),
    # "Luật Đất đai điều 45 (khoản 2)"
    re.compile(_LAW + r"\s*,?\s+" + _KHOAN_BEFORE + _DIEU + _KHOAN_AFTER, re.IGNORECASE),
]

# Từ đệm được phép quanh cụm tra cứu (so sau khi bỏ dấu)
_FILLER = set("""
tra cuu xem cho toi minh em anh chi ban oi noi dung toan van nguyen quy dinh gi la ve the nao nhu a nhe
hay giup doc trich day du cua hoi vui long voi duoc khong ko co cu tiet xin chao tim ghi viet sao ra thi
cam on in dua liet ke can biet muon nay do
""".split())


def _plain(text: str) -> list[str]:
    text = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.findall(r"\w+", text)


def parse(message: str) -> dict | None:
    """{"law", "dieu", "khoan"} nếu cả câu là yêu cầu xem nguyên văn 1 Điều/Khoản; ngược lại None."""
    text = unicodedata.normalize("NFC", message.strip())
    for pattern in _PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        rest = text[: m.start()] + " " + text[m.end():]
        if any(word not in _FILLER for word in _plain(rest)):
            return None  # còn hỏi thêm việc khác: để AI trả lời
        law = " ".join(m.group("law").split())
        return {"law": law, "dieu": m.group("dieu").lower(), "khoan": m.group("k1") or m.group("k2")}
    return None


def format_article(article: dict) -> str:
    doc = article["document"]
    where = f"Khoản {article['khoan']} Điều {article['dieu']}" if article.get("khoan") else f"Điều {article['dieu']}"
    source = f"{doc['title']} ({doc['doc_number']})" if doc.get("doc_number") else doc["title"]
    lines = [f"Nguyên văn {where}, {source}:", "", article["content"]]
    if article.get("note"):
        lines += ["", f"Lưu ý: {article['note']}"]
    if doc.get("status") and doc["status"] != "active":
        lines += ["", "Lưu ý: văn bản này không còn hiệu lực (đã bị thay thế hoặc là dự thảo)."]
    return "\n".join(lines)
