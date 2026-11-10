"""Thử bốn cờ tài liệu (document_gate) trên chữ mẫu, KHÔNG gọi AI, KHÔNG cần OCR. Chỉ in kết quả, luôn thoát mã 0.
Chạy trong Docker (ở thư mục gốc legal-assistant-ai):
    docker compose run --rm --no-deps @M agent-service python scripts/try_document_gate.py
(@M là các tham số -v mount app và scripts của agent-service, xem hướng dẫn đo.) Hàm check/facts/ask_contract_type tự chạy dù
cờ trong .env đang tắt, vì cờ chỉ được kiểm ở review_agent."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import contract_review, document_gate  # noqa: E402

FULL = (
    "HỢP ĐỒNG MUA BÁN HÀNG HOÁ. Bên A (bên bán): Công ty TNHH An Phát, đại diện ông Nguyễn Văn An. "
    "Bên B (bên mua): Công ty Cổ phần Bình Minh, đại diện bà Trần Thị Bình. "
    "Điều 1. Hàng hoá: 100 máy tính xách tay. Điều 2. Giá và thanh toán: giá trị hợp đồng là 1.500.000.000 đồng, "
    "thanh toán làm hai đợt. Điều 3. Thời hạn giao hàng: trong 30 ngày kể từ ngày hợp đồng có hiệu lực. "
    "Điều 4. Quyền và nghĩa vụ của các bên: bên A giao hàng đúng chất lượng, bên B nhận hàng và thanh toán đúng hạn. "
    "Điều 5. Vi phạm: bên vi phạm chịu phạt 8% giá trị phần nghĩa vụ vi phạm và bồi thường thiệt hại. "
    "Điều 6. Giải quyết tranh chấp: hoà giải, nếu không được thì đưa ra Toà án có thẩm quyền. "
    "Hà Nội, ngày 01 tháng 10 năm 2026. Đại diện bên A ký tên. Đại diện bên B ký tên."
)
NO_DISPUTE = FULL.replace("Điều 6. Giải quyết tranh chấp: hoà giải, nếu không được thì đưa ra Toà án có thẩm quyền. ", "").replace(
    "Điều 5. Vi phạm: bên vi phạm chịu phạt 8% giá trị phần nghĩa vụ vi phạm và bồi thường thiệt hại. ", "")
ENGLISH = (
    "SALES CONTRACT. This agreement is made between Party A, the seller, and Party B, the buyer. Party A agrees to deliver "
    "one hundred laptops to Party B within thirty days. The price is one point five billion dong, payable in two instalments. "
    "Any breach shall be compensated. Disputes shall be settled by the competent court. Signed by the representatives."
)
BLANK = "HỢP ĐỒNG THUÊ NHÀ. Bên cho thuê: ................................. CMND: ................. Địa chỉ: ........................ " \
        "Bên thuê: ................................. CMND: ................. Giá thuê: ................ đồng/tháng. " \
        "Thời hạn: ................ Ký tên: ........................ " + "Nội dung điều khoản chung của hợp đồng thuê nhà. " * 3
TOC = "MỤC LỤC. " + "".join(f"Điều {i}. Nội dung điều {i} ........................ {i}\n" for i in range(1, 8)) + \
      "----------------------------------------\n" + FULL
GARBAGE = "xkqj zzwv ptrl mnbv cxzq wqer tyui opas dfgh jklz xcvb nmqw ertp lkjh gfds aqzx wsxe dcrf vtgb yhnu jmik olp " * 4
SHORT = "Hợp đồng mua bán, bên A bán cho bên B."
LONG = FULL + (" Điều khoản bổ sung về chất lượng và bảo hành sản phẩm." * 400)


def show(title, docs):
    print(f"\n=== {title} ({len(docs[0]['text'])} ký tự)")
    print("  check (cổng đọc được):", document_gate.check(docs) or "ĐẠT")
    for s in document_gate.facts(docs, include_checklist=True) or ["(không có dữ kiện nào)"]:
        print("  facts+checklist:", s)


for title, text in [
    ("Hợp đồng đầy đủ, tiếng Việt (mong đợi: đạt, không báo gì)", FULL),
    ("Thiếu mục vi phạm và tranh chấp (mong đợi: báo 2 mục đó)", NO_DISPUTE),
    ("Mẫu chưa điền nhiều chỗ trống (mong đợi: báo mẫu chưa điền)", BLANK),
    ("Mục lục chấm chấm + đường kẻ (mong đợi: KHÔNG báo mẫu chưa điền; nếu báo là đếm nhầm)", TOC),
    ("Hợp đồng toàn tiếng Anh (mong đợi: nếu bị chặn là chặn nhầm)", ENGLISH),
    ("Chữ rác như OCR hỏng (mong đợi: chặn)", GARBAGE),
    ("Quá ngắn (mong đợi: chặn)", SHORT),
    ("Rất dài (mong đợi: báo chỉ đọc X trên Y ký tự)", LONG),
]:
    show(title, [{"text": text, "source_kind": "test"}])

print("\n=== Hỏi lại loại hợp đồng")
for content in ["xem giúp tôi", "rà soát hợp đồng này", "điều khoản phạt cọc có hợp lý không"]:
    kind = contract_review.detect_contract_type("Thoả thuận giữa hai bên về việc hợp tác làm ăn.")
    answer = document_gate.ask_contract_type(kind, content)
    print(f"  loại={kind!r} câu hỏi={content!r} ->", "HỎI LẠI" if answer else "không hỏi, đi tìm luật")
print("  loại nhận ra từ hợp đồng mua bán mẫu:", contract_review.detect_contract_type(FULL))
