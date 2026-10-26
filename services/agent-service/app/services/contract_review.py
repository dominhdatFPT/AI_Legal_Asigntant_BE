"""Rà soát tài liệu đính kèm: nhận diện loại hợp đồng và dựng câu tìm luật theo NỘI DUNG tài liệu.
Vì sao: người dùng chỉ gõ "kiểm tra giúp tôi" thì tìm luật theo câu đó ra đoạn không liên quan."""
import re
import unicodedata

from app.services.rag_client import MAX_QUERY_LENGTH

DETECT_CHARS = 3000  # tiêu đề hợp đồng nằm ở đầu, đọc cả tài liệu chỉ thêm nhiễu
FALLBACK_QUERY_CHARS = 300
MAX_QUERIES = 5
MAX_REVIEW_CHUNKS = 10

# Thứ tự khai báo = thứ tự ưu tiên khi hai loại khớp cùng một vị trí (vd "hợp đồng mua bán nhà" cũng khớp "hợp đồng mua bán")
# shortcut: nhận diện bằng từ khóa trên chữ đã bỏ dấu, hợp đồng đặt tên lạ hoặc viết tắt sẽ thành "khac"; nâng cấp khi cần thì cho AI phân loại
CONTRACT_PATTERNS = {
    "chuyen_nhuong_dat": [r"chuyen nhuong[^.\n]{0,30}quyen su dung dat", r"chuyen nhuong dat"],
    "tang_cho_dat": [r"tang cho[^.\n]{0,30}quyen su dung dat", r"tang cho dat"],
    "the_chap": [r"\bthe chap\b"],
    "thue_dat_nha": [r"\bthue (?:quyen su dung dat|dat|nha|mat bang|can ho|kho|xuong)\b"],
    "mua_ban_nha": [r"mua ban (?:nha|can ho|cong trinh)"],
    "mua_ban_hang_hoa": [r"mua ban hang hoa", r"cung (?:ung|cap) hang hoa"],
    "dich_vu": [r"hop dong dich vu", r"cung (?:ung|cap) dich vu", r"hop dong tu van", r"hop dong thi cong"],
    "gop_von_doanh_nghiep": [r"\bgop von\b", r"\bgop co phan\b", r"dieu le cong ty"],
}

# "Hợp đồng mua bán" không nói rõ mua bán gì: chỉ dùng khi không loại nào khác khớp, kẻo thắng "mua bán nhà" vì đứng sớm hơn
GENERIC_SALE = r"hop dong mua ban"

# Mỗi loại 3-4 khía cạnh pháp lý để tìm luật; rag-service nhận mỗi câu tối đa 1000 ký tự
REVIEW_QUERIES = {
    "chuyen_nhuong_dat": [
        "điều kiện chuyển nhượng quyền sử dụng đất",
        "công chứng chứng thực hợp đồng chuyển nhượng quyền sử dụng đất",
        "đăng ký biến động khi chuyển nhượng quyền sử dụng đất",
        "nghĩa vụ tài chính thuế phí khi chuyển nhượng quyền sử dụng đất",
    ],
    "tang_cho_dat": [
        "điều kiện tặng cho quyền sử dụng đất",
        "công chứng chứng thực hợp đồng tặng cho quyền sử dụng đất",
        "đăng ký biến động khi tặng cho quyền sử dụng đất",
        "thuế thu nhập cá nhân lệ phí trước bạ khi tặng cho đất",
    ],
    "thue_dat_nha": [
        "điều kiện cho thuê quyền sử dụng đất",
        "nội dung hợp đồng thuê nhà ở quyền nghĩa vụ của các bên",
        "thời hạn thuê chấm dứt hợp đồng thuê và hoàn trả tiền cọc",
        "công chứng đăng ký hợp đồng thuê quyền sử dụng đất",
    ],
    "the_chap": [
        "điều kiện thế chấp quyền sử dụng đất",
        "công chứng chứng thực hợp đồng thế chấp quyền sử dụng đất",
        "đăng ký thế chấp quyền sử dụng đất tài sản gắn liền với đất",
        "xử lý tài sản thế chấp quyền của bên nhận thế chấp",
    ],
    "mua_ban_nha": [
        "điều kiện giao dịch mua bán nhà ở",
        "công chứng chứng thực hợp đồng mua bán nhà ở",
        "nội dung bắt buộc của hợp đồng mua bán nhà ở",
        "đăng ký quyền sở hữu nhà ở nghĩa vụ tài chính khi mua bán nhà",
    ],
    "mua_ban_hang_hoa": [
        "hợp đồng mua bán hàng hóa quyền và nghĩa vụ của các bên",
        "giao hàng thanh toán và chuyển rủi ro trong mua bán hàng hóa",
        "vi phạm hợp đồng phạt vi phạm bồi thường thiệt hại",
    ],
    "dich_vu": [
        "hợp đồng dịch vụ quyền và nghĩa vụ của các bên",
        "giá dịch vụ phương thức thanh toán và nghiệm thu dịch vụ",
        "vi phạm hợp đồng phạt vi phạm bồi thường thiệt hại",
        "đơn phương chấm dứt hợp đồng dịch vụ",
    ],
    "gop_von_doanh_nghiep": [
        "góp vốn thành lập doanh nghiệp thời hạn và hình thức góp vốn",
        "quyền và nghĩa vụ của thành viên góp vốn",
        "chuyển nhượng phần vốn góp trong công ty",
    ],
}

# Từ chung chung trong câu yêu cầu rà soát (đã bỏ dấu): câu chỉ gồm các từ này không thêm vào câu tìm luật
GENERIC_WORDS = set(
    "kiem tra giup toi minh em xem nhan xet ra soat danh gia phan tich doc lai hop dong nay tai lieu file van ban "
    "check review co khong duoc the nao ok on chua vay a nhe nha hay cho gium voi dung sai hop le ho so giay to "
    "cua toi giup do day sau gi nhu".split()
)

_WORD = re.compile(r"\w+")
_PLACEHOLDER = re.compile(r"[.…_\-]{3,}")  # mẫu trống: "........", "………"


def _fold(text: str) -> str:
    """Chữ thường, bỏ dấu, đ -> d: để so khóa một lần cho cả bản có dấu và không dấu."""
    text = text.lower().replace("đ", "d")
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def detect_contract_type(text: str) -> str:
    """Loại có từ khóa xuất hiện sớm nhất trong đầu tài liệu thắng (đúng với tiêu đề). Không nhận ra thì "khac"."""
    head = _fold(text[:DETECT_CHARS])
    best = None  # (vị trí, thứ tự khai báo, loại)
    for order, (kind, patterns) in enumerate(CONTRACT_PATTERNS.items()):
        for pattern in patterns:
            match = re.search(pattern, head)
            if match and (best is None or (match.start(), order) < best[:2]):
                best = (match.start(), order, kind)
    if best:
        return best[2]
    return "mua_ban_hang_hoa" if re.search(GENERIC_SALE, head) else "khac"


def _is_specific(question: str) -> bool:
    return any(w not in GENERIC_WORDS for w in _WORD.findall(_fold(question)))


def _opening(text: str) -> str:
    return " ".join(_PLACEHOLDER.sub(" ", text).split())[:FALLBACK_QUERY_CHARS]


def review_queries(contract_type: str, user_question: str, document_text: str = "") -> list[str]:
    """Câu tìm luật cho rag-service: câu hỏi cụ thể của người dùng (nếu có) đứng đầu, rồi các khía cạnh của loại hợp đồng."""
    aspects = REVIEW_QUERIES.get(contract_type)
    if aspects is None:  # "khac": không biết khía cạnh nào, dùng phần mở đầu tài liệu
        opening = _opening(document_text)
        aspects = [opening] if opening else []
    queries = [user_question.strip()] if _is_specific(user_question) else []
    queries += aspects
    return [q[:MAX_QUERY_LENGTH] for q in queries[:MAX_QUERIES]]


def merge_chunks(results: list[list[dict]], limit: int = MAX_REVIEW_CHUNKS) -> list[dict]:
    """Xếp xen kẽ (đoạn 1 của mỗi câu tìm, rồi đoạn 2...) để câu nào cũng có đoạn trong prompt; bỏ đoạn trùng theo id."""
    merged, seen = [], set()
    for rank in range(max((len(r) for r in results), default=0)):
        for chunks in results:
            if rank >= len(chunks):
                continue
            chunk = chunks[rank]
            key = chunk.get("id") or chunk.get("content")
            if key in seen:
                continue
            seen.add(key)
            merged.append(chunk)
            if len(merged) >= limit:
                return merged
    return merged
