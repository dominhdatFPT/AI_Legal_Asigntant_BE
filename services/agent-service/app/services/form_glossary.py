"""Bảng thuật ngữ tĩnh cho prompt kiểm tra mẫu đơn: chỉ chèn mục nào khớp nhãn / word_key của các ô được gửi.
Đổi nội dung ở đây thì tăng form_check.PROMPT_VERSION (cache theo ô)."""
import re

# Chép nguyên văn Điều 9 Luật Đất đai 2024 (data/luat_dat_dai_2024.txt, dòng 216-260), chỉ bỏ dòng trống.
_LAND_ARTICLE = """Điều 9. Phân loại đất
1. Căn cứ vào mục đích sử dụng, đất đai được phân loại bao gồm nhóm đất nông nghiệp, nhóm đất phi nông nghiệp, nhóm đất chưa sử dụng.
2. Nhóm đất nông nghiệp bao gồm các loại đất sau đây:
a) Đất trồng cây hằng năm, gồm đất trồng lúa và đất trồng cây hằng năm khác;
b) Đất trồng cây lâu năm;
c) Đất lâm nghiệp, gồm đất rừng đặc dụng, đất rừng phòng hộ, đất rừng sản xuất;
d) Đất nuôi trồng thủy sản;
đ) Đất chăn nuôi tập trung;
e) Đất làm muối;
g) Đất nông nghiệp khác.
3. Nhóm đất phi nông nghiệp bao gồm các loại đất sau đây:
a) Đất ở, gồm đất ở tại nông thôn, đất ở tại đô thị;
b) Đất xây dựng trụ sở cơ quan;
c) Đất sử dụng vào mục đích quốc phòng, an ninh (sau đây gọi là đất quốc phòng, an ninh);
d) Đất xây dựng công trình sự nghiệp, gồm: đất xây dựng cơ sở văn hóa, xã hội, y tế, giáo dục và đào tạo, thể dục, thể thao, khoa học và công nghệ, môi trường, khí tượng thủy văn, ngoại giao và công trình sự nghiệp khác hoặc đất xây dựng trụ sở của đơn vị sự nghiệp công lập khác;
đ) Đất sản xuất, kinh doanh phi nông nghiệp, gồm: đất khu công nghiệp, cụm công nghiệp; đất thương mại, dịch vụ; đất cơ sở sản xuất phi nông nghiệp; đất sử dụng cho hoạt động khoáng sản;
e) Đất sử dụng vào mục đích công cộng, gồm: đất công trình giao thông; đất công trình thủy lợi; đất công trình cấp nước, thoát nước; đất công trình phòng, chống thiên tai; đất có di tích lịch sử - văn hóa, danh lam thắng cảnh, di sản thiên nhiên; đất công trình xử lý chất thải; đất công trình năng lượng, chiếu sáng công cộng; đất công trình hạ tầng bưu chính, viễn thông, công nghệ thông tin; đất chợ dân sinh, chợ đầu mối; đất khu vui chơi, giải trí công cộng, sinh hoạt cộng đồng;
g) Đất sử dụng cho hoạt động tôn giáo (sau đây gọi là đất tôn giáo); đất sử dụng cho hoạt động tín ngưỡng (sau đây gọi là đất tín ngưỡng);
h) Đất nghĩa trang, nhà tang lễ, cơ sở hỏa táng; đất cơ sở lưu giữ tro cốt;
i) Đất có mặt nước chuyên dùng;
k) Đất phi nông nghiệp khác.
4. Nhóm đất chưa sử dụng là đất chưa xác định mục đích sử dụng và chưa giao, chưa cho thuê.
5. Chính phủ quy định chi tiết các loại đất tại Điều này."""

_LAND = (
    "LOẠI ĐẤT / MỤC ĐÍCH SỬ DỤNG ĐẤT. Tên loại đất phải theo Điều 9 Luật Đất đai 2024 dưới đây:\n"
    + _LAND_ARTICLE
    + "\nLưu ý: luật viết 'hằng năm', biểu mẫu có chỗ viết 'hàng năm'; hai cách viết đều đúng, KHÔNG đề xuất chỉ để đổi 'hàng' thành 'hằng' hay ngược lại."
    "\nMã ký hiệu loại đất (ví dụ ONT, ODT, CLN) không có trong Điều 9: không thêm và không đề xuất thêm mã."
    "\n'Đất vườn' không phải tên loại đất ở Điều 9 (có thể là đất ở có vườn, ao): không tự đổi sang loại khác, trả warning."
)

# Ngưỡng phân cấp theo văn bản của Bộ Xây dựng (TT 06/2021/TT-BXD, sửa bởi TT 02/2025/TT-BXD; có thể đã bị thay bởi TT 34/2026/TT-BXD).
# CHƯA chép ngưỡng; người bổ sung phải đối chiếu vbpl.vn trước.
_CONSTRUCTION = (
    "CÔNG TRÌNH / NHÀ Ở:\n"
    "- Tên cấp công trình viết đúng là: cấp đặc biệt, cấp I, cấp II, cấp III, cấp IV (số La Mã).\n"
    "- Loại nhà của hộ gia đình, cá nhân gọi là 'Nhà ở riêng lẻ'.\n"
    "- Chỉ chuẩn hóa cách viết (ví dụ 'cấp 3' thành 'cấp III'); KHÔNG tự suy ra cấp từ quy mô; thiếu hoặc không rõ cấp thì trả warning."
)

_AUTHORITY = (
    "CƠ QUAN TIẾP NHẬN / KÍNH GỬI:\n"
    "- Chính quyền địa phương có 2 cấp (xã/phường/đặc khu và tỉnh/thành phố); không đòi cấp huyện.\n"
    "- Viết 'Ủy ban nhân dân xã/phường ...' kèm tên.\n"
    "- Không bịa tên xã/phường; thiếu tên thì trả warning."
)

# (regex theo nhãn, regex theo word_key hoặc None, nội dung)
GLOSSARY: list[tuple[re.Pattern, re.Pattern | None, str]] = [
    (re.compile(r"loại đất|mục đích sử dụng", re.I), None, _LAND),
    (re.compile(r"công trình|nhà ở", re.I), None, _CONSTRUCTION),
    (re.compile(r"kính gửi|tiếp nhận|ủy ban nhân dân|ubnd|cơ quan đăng ký", re.I),
     re.compile(r"recipient|receiving|authority", re.I), _AUTHORITY),
]


def select(labels: list[str], word_keys: list[str]) -> str:
    """Các mục thuật ngữ khớp ít nhất một nhãn hoặc word_key; chuỗi rỗng nếu không có."""
    labels_text, keys_text = "\n".join(labels), "\n".join(word_keys)
    return "\n\n".join(
        text for by_label, by_key, text in GLOSSARY
        if by_label.search(labels_text) or (by_key and by_key.search(keys_text))
    )
