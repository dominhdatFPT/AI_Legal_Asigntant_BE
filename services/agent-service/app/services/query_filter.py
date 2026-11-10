"""Lọc câu hỏi vô nghĩa (gõ bừa bàn phím) trước khi gọi RAG và LLM, trả lời mặc định để tiết kiệm thời gian và lượt gọi AI."""
import re
import unicodedata

DEFAULT_ANSWER = (
    "Mình chưa hiểu câu hỏi của bạn. Bạn hãy hỏi rõ hơn về thủ tục đất đai hoặc hộ kinh doanh, "
    "ví dụ: \"Sang tên sổ đỏ cần giấy tờ gì?\""
)

# Một âm tiết tiếng Việt (đã bỏ dấu): phụ âm đầu + 1-3 nguyên âm + phụ âm cuối
_SYLLABLE = re.compile(r"^(ngh|ng|nh|ch|gh|gi|kh|ph|qu|th|tr|[bcdghklmnprstvx])?[aeiouy]{1,3}(ng|nh|ch|[cmnpt])?$")
_ACRONYM = re.compile(r"^[A-ZĐ]{2,6}$")  # CCCD, GCN, UBND...
_TOKEN = re.compile(r"\w+")


def _strip_accents(word: str) -> str:
    word = word.lower().replace("đ", "d")
    return "".join(c for c in unicodedata.normalize("NFD", word) if unicodedata.category(c) != "Mn")


def _is_valid_token(token: str) -> bool:
    if any(c.isdigit() for c in token):  # số hiệu văn bản, số điều, năm...
        return True
    if _ACRONYM.match(token):
        return True
    return bool(_SYLLABLE.match(_strip_accents(token)))


def valid_token_ratio(text: str) -> float:
    """Số từ hợp lệ chia tổng số từ, 0.0 khi đoạn chữ không có từ nào."""
    tokens = _TOKEN.findall(text)
    if not tokens:
        return 0.0
    return sum(_is_valid_token(t) for t in tokens) / len(tokens)


def is_meaningless(text: str) -> bool:
    """True khi đa số từ không phải âm tiết tiếng Việt, ví dụ "dsadsafsaf", "fdavfsva"."""
    return valid_token_ratio(text) < 0.5
