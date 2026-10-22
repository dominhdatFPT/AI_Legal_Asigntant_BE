"""Tách câu hỏi nhiều ý thành các ý nhỏ, để search.py tìm đoạn luật riêng cho từng ý.

Vấn đề: "Tôi muốn tách thửa đất rồi tặng cho con một phần, cần điều kiện gì và thời hạn sử dụng đất thế nào?"
có 3 ý. Tìm 1 lần theo cả câu thì vector nghiêng về 1 ý, top_k đoạn dồn vào ý đó, các ý còn lại thiếu căn cứ.

Cách tách (chỉ dùng quy tắc, không gọi AI):
1. Cắt ở dấu ? ; ! xuống dòng, dấu chấm hết câu, và các từ nối ý: và, rồi, sau đó, đồng thời, ngoài ra.
2. Dấu phẩy chỉ cắt khi phần trước kết thúc bằng từ để hỏi (không, gì, nào, sao, đâu, chưa, bao nhiêu, bao lâu),
   để câu kể bối cảnh như "Đất ở Bình Dương, diện tích 100m2, tách sổ được không" vẫn là 1 ý.
3. Ý quá ít nội dung (vd "cần điều kiện gì", "lệ phí bao nhiêu", "xin chào") không đứng riêng được
   vì không biết đang nói về gì: ghép vào ý liền trước (ý đầu thì ghép sang ý sau).
4. Tối đa MAX_PARTS ý; thừa thì ghép vào ý cuối. Còn 1 ý thì trả lại nguyên câu gốc (search.py chạy như cũ).
5. Ý không còn từ chỉ lĩnh vực (đất, sổ đỏ, công ty...) thì mượn từ lĩnh vực của câu gốc, ghi trong ngoặc.
   Lỗi gặp thật 20/10: "tặng cho con một phần, cần điều kiện gì" mất chữ "đất" (nằm ở ý khác) nên tìm ra
   Đ.53 Luật Doanh nghiệp (tặng cho phần vốn góp). Thành "... cần điều kiện gì (đất)" thì đúng lĩnh vực.
"""
import re
import unicodedata

MAX_PARTS = 3
MIN_CONTENT_WORDS = 2  # ý có ít hơn số từ nội dung này thì ghép vào ý bên cạnh

_STRONG = re.compile(
    r"[?;!\n]+|\.(?=\s)|\s(?:và|rồi|sau đó|đồng thời|ngoài ra|va|roi|sau do|dong thoi|ngoai ra)\s",
    re.IGNORECASE,
)

# Từ để hỏi ở cuối vế (đã bỏ dấu): dấu phẩy sau các từ này là ranh giới giữa 2 câu hỏi.
_QUESTION_ENDINGS = {"khong", "ko", "gi", "j", "nao", "sao", "dau", "chua", "nhieu", "lau"}

# Từ không mang chủ đề: đại từ, từ hỏi, hư từ, và các "khía cạnh" chung của mọi thủ tục
# (điều kiện, hồ sơ, giấy tờ, thời gian, lệ phí...). Ý chỉ gồm những từ này thì không biết đang hỏi về việc gì.
# So CÓ DẤU với từ có dấu; bản không dấu chỉ dùng cho từ gõ không dấu và bỏ các từ dễ nhầm
# (con/còn, ban/bán, so/sổ, do/đỏ, dang/đăng) để "tach so do cho con" không bị coi là rỗng.
_NON_CONTENT = set("""
tôi mình em anh chị bạn bác ông bà cô chú xin chào cảm ơn ạ nhé nha vậy thế thì là mà
cho hỏi muốn cần phải có không được gì nào sao làm bao nhiêu lâu đâu chưa rồi và hay hoặc
của các những một này đó kia nếu khi để với trong ra vào lên nên sẽ đã đang vẫn còn cũng như khoảng tầm
điều kiện hồ sơ giấy tờ thủ tục thời gian lệ phí chi tiền quy định luật pháp lý
""".split())
_NON_CONTENT_PLAIN = set("""
toi minh em anh chi bac ong ba co chu xin chao cam on a nhe nha vay the thi la ma
cho hoi muon can phai khong ko duoc dc gi j nao sao lam bao nhieu lau dau chua roi va hay hoac
cua cac nhung mot nay kia neu khi de voi trong ra vao len nen se da van cung nhu khoang tam
dieu kien ho giay to thu tuc thoi gian le phi tien quy dinh luat phap ly
""".split())


def _strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", unicodedata.normalize("NFC", text.lower()))


def _is_content(word: str) -> bool:
    if word in _NON_CONTENT:
        return False
    return not (word == _strip_accents(word) and word in _NON_CONTENT_PLAIN)


def _content_words(text: str) -> int:
    return sum(map(_is_content, _words(text)))


# Từ chỉ lĩnh vực của câu hỏi. Cụm dài đứng trước để "hộ kinh doanh" không bị tính thêm "doanh".
_DOMAINS = ["quyền sử dụng đất", "sổ đỏ", "sổ hồng", "thửa đất", "đất", "nhà ở", "bất động sản",
            "hộ kinh doanh", "hợp tác xã", "doanh nghiệp", "công ty"]


def _domains(text: str) -> list[str]:
    """Các từ lĩnh vực có trong câu (so có dấu; câu gõ không dấu thì so không dấu), không trùng."""
    padded = " " + " ".join(_words(text)) + " "
    plain = " " + " ".join(_words(_strip_accents(text))) + " "
    found = []
    for d in _DOMAINS:
        hit = f" {d} " in padded or (padded == plain and f" {_strip_accents(d)} " in plain)
        if hit and not any(d in f for f in found):
            found.append(d)
    return found


def _split_commas(segment: str) -> list[str]:
    pieces: list[str] = []
    for piece in segment.split(","):
        words = _words(_strip_accents(pieces[-1])) if pieces else []
        if pieces and not (words and words[-1] in _QUESTION_ENDINGS):
            pieces[-1] = f"{pieces[-1]},{piece}"  # phẩy kể bối cảnh: giữ trong cùng 1 ý
        else:
            pieces.append(piece)
    return pieces


def split_question(query: str) -> list[str]:
    """Trả về danh sách ý. Câu chỉ có 1 ý thì trả về [query] (nguyên văn, đã bỏ khoảng trắng 2 đầu)."""
    query = query.strip()
    pieces = [
        p.strip(" ,.;:?!-\t")
        for segment in _STRONG.split(query)
        for p in _split_commas(segment)
    ]
    parts: list[str] = []
    for piece in filter(None, pieces):
        if parts and _content_words(piece) < MIN_CONTENT_WORDS:
            parts[-1] = f"{parts[-1]} {piece}"
        else:
            parts.append(piece)
    if len(parts) > 1 and _content_words(parts[0]) < MIN_CONTENT_WORDS:
        first = parts.pop(0)
        parts[0] = f"{first} {parts[0]}"
    if len(parts) <= 1:
        return [query]
    if len(parts) > MAX_PARTS:
        parts[MAX_PARTS - 1:] = [" ".join(parts[MAX_PARTS - 1:])]
    context = _domains(query)
    if context:
        parts = [p if _domains(p) else f"{p} ({', '.join(context)})" for p in parts]
    return parts
