"""Đổi từ ngữ đời thường trong câu hỏi sang thuật ngữ trong văn bản luật trước khi tạo embedding.

Người dân hỏi "sổ đỏ", "mua đất", "sang tên", còn luật viết "Giấy chứng nhận quyền sử dụng đất",
"chuyển nhượng quyền sử dụng đất", "đăng ký biến động". Embedding không nối được hết các cặp này
nên hay tìm ra đoạn lệch. Ở đây chỉ THÊM thuật ngữ luật vào cuối câu hỏi (không xóa từ gốc).

Thêm cặp mới: thêm 1 dòng vào SYNONYMS, rồi chạy scripts/eval_retrieval.py --compare để xem có tốt hơn không.
"""
import re
import unicodedata

# (các cách nói đời thường, thuật ngữ trong luật)
SYNONYMS: list[tuple[tuple[str, ...], str]] = [
    # Đất đai
    (("sổ đỏ", "sổ hồng", "bìa đỏ", "bìa hồng"),
     "Giấy chứng nhận quyền sử dụng đất, quyền sở hữu tài sản gắn liền với đất"),
    (("mua đất", "bán đất", "mua bán đất", "mua nhà đất", "bán nhà đất", "mua nhà", "bán nhà"),
     "chuyển nhượng quyền sử dụng đất, nhận chuyển nhượng quyền sử dụng đất"),
    (("sang tên", "đổi tên chủ", "chuyển tên"),
     "đăng ký biến động, chuyển quyền sử dụng đất"),
    (("tách sổ", "chia sổ", "tách đất", "chia đất"), "tách thửa đất"),
    (("gộp sổ", "nhập sổ", "gộp 2 sổ", "gộp hai sổ", "gộp đất", "gộp thửa"), "hợp thửa đất"),
    (("lên thổ cư", "chuyển thổ cư", "chuyển lên đất ở", "chuyển đổi đất"),
     "chuyển mục đích sử dụng đất sang đất ở"),
    (("thổ cư",), "đất ở"),
    (("đất ruộng", "đất vườn", "đất rẫy", "đất trồng lúa"), "đất nông nghiệp"),
    (("làm sổ lần đầu", "làm sổ đỏ", "cấp sổ", "xin sổ"),
     "đăng ký lần đầu, cấp Giấy chứng nhận quyền sử dụng đất"),
    (("ghi sai", "sai tên", "sửa sổ", "sai thông tin"),
     "đính chính Giấy chứng nhận đã cấp"),
    (("vay ngân hàng", "cầm sổ", "thế chấp sổ"), "thế chấp quyền sử dụng đất"),
    (("giải chấp", "xóa thế chấp", "rút sổ"), "xóa đăng ký thế chấp"),
    (("cho con đất", "cho đất", "tặng đất", "cho tặng"), "tặng cho quyền sử dụng đất"),
    (("đất ông bà để lại", "đất bố mẹ để lại", "chia thừa kế", "hưởng thừa kế"),
     "thừa kế quyền sử dụng đất"),
    (("lấn đất", "lấn chiếm", "tranh chấp đất", "kiện đất", "cãi nhau về đất"),
     "tranh chấp đất đai, hòa giải tranh chấp đất đai, thẩm quyền giải quyết tranh chấp đất đai"),
    (("đền bù", "giải tỏa", "bị lấy đất"),
     "bồi thường, hỗ trợ, tái định cư khi Nhà nước thu hồi đất"),
    (("tiền đất", "thuế đất"), "tiền sử dụng đất, tiền thuê đất"),
    (("dùng bao nhiêu năm", "sử dụng bao lâu", "hết hạn sử dụng"), "thời hạn sử dụng đất"),
    (("người nước ngoài", "việt kiều"),
     "người nước ngoài, người gốc Việt Nam định cư ở nước ngoài"),
    # Kinh doanh
    (("mở công ty", "lập công ty", "thành lập công ty", "mở doanh nghiệp"),
     "hồ sơ đăng ký doanh nghiệp, trình tự thủ tục đăng ký doanh nghiệp"),
    (("cty",), "công ty"),
    (("tnhh",), "công ty trách nhiệm hữu hạn"),
    (("đóng cửa công ty", "dẹp công ty", "đóng công ty", "giải tán công ty"),
     "giải thể doanh nghiệp"),
    (("tạm nghỉ kinh doanh", "tạm nghỉ bán", "nghỉ kinh doanh"),
     "tạm ngừng kinh doanh"),
    (("đổi địa chỉ công ty", "chuyển trụ sở", "đổi trụ sở", "thay đổi thông tin công ty"),
     "thay đổi nội dung đăng ký doanh nghiệp"),
    (("đặt tên công ty", "tên công ty"), "tên doanh nghiệp"),
    (("giấy phép kinh doanh", "giấy phép kd"),
     "Giấy chứng nhận đăng ký doanh nghiệp, Giấy chứng nhận đăng ký hộ kinh doanh"),
    (("mở quán", "mở cửa hàng", "buôn bán nhỏ", "kinh doanh nhỏ", "bán hàng tại nhà"),
     "đăng ký hộ kinh doanh"),
    (("mst",), "mã số thuế, mã số doanh nghiệp"),
    (("htx",), "hợp tác xã"),
]


def _words(text: str, keep_accents: bool) -> str:
    """Chữ thường, gộp khoảng trắng, bọc 2 đầu bằng dấu cách để khớp nguyên từ.
    keep_accents=False: bỏ dấu, để câu gõ không dấu ("so do") vẫn khớp "sổ đỏ"."""
    text = unicodedata.normalize("NFC", text.lower())
    if not keep_accents:
        text = unicodedata.normalize("NFD", text.replace("đ", "d"))
        text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " " + " ".join(re.findall(r"\w+", text)) + " "


def _has_accents(text: str) -> bool:
    return _words(text, True) != _words(text, False)


_PATTERNS = [
    ({accent: tuple(_words(p, accent) for p in phrases) for accent in (True, False)}, legal)
    for phrases, legal in SYNONYMS
]


def legal_terms(query: str) -> list[str]:
    """Các thuật ngữ luật tương ứng với từ đời thường có trong câu hỏi (không trùng, giữ thứ tự).
    Câu có dấu thì so có dấu (tránh "thuê đất" khớp nhầm "thuế đất"); câu gõ không dấu thì so không dấu."""
    accent = _has_accents(query)
    words = _words(query, accent)
    terms: list[str] = []
    for phrases, legal in _PATTERNS:
        if any(p in words for p in phrases[accent]) and _words(legal, accent) not in words and legal not in terms:
            terms.append(legal)
    return terms


def expand(query: str) -> str:
    """'mua đất cần gì' -> 'mua đất cần gì (chuyển nhượng quyền sử dụng đất, ...)'."""
    terms = legal_terms(query)
    return f"{query} ({'; '.join(terms)})" if terms else query
