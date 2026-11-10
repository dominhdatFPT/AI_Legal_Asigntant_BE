"""Test cổng kiểm tra tài liệu đính kèm (document_gate.check): chạy bằng quy tắc, không gọi mạng, không gọi AI."""
import logging
import re

import pytest

from app.core.config import get_settings
from app.services import contract_review, document_gate, prompts

CONTRACT_TEXT = (
    "HỢP ĐỒNG CHUYỂN NHƯỢNG QUYỀN SỬ DỤNG ĐẤT. Bên A đồng ý chuyển nhượng cho bên B thửa đất số 125, tờ bản đồ số 18, "
    "diện tích 120 mét vuông, tại phường Tân Phú, thành phố Thủ Đức. Giá chuyển nhượng là 3.500.000.000 đồng, bên B "
    "thanh toán thành hai đợt. Hai bên cam kết thực hiện đúng các điều khoản của hợp đồng này."
)
CONTRACT_TEXT_NO_ACCENT = (
    "Hop dong chuyen nhuong quyen su dung dat. Ben A dong y chuyen nhuong cho ben B thua dat so 125, to ban do so 18, "
    "dien tich 120 met vuong, tai phuong Tan Phu, thanh pho Thu Duc. Gia chuyen nhuong la 3.500.000.000 dong, ben B "
    "thanh toan thanh hai dot. Hai ben cam ket thuc hien dung cac dieu khoan cua hop dong nay."
)
GARBAGE_TEXT = "xkqz vbnm qwrt zxcv bnmk lkjh " * 12


def doc(text, kind="pdf_scan"):
    return {"document_id": "d1", "source_kind": kind, "text": text}


def test_fixtures_have_expected_size():
    assert 250 <= len(CONTRACT_TEXT) <= 400 and len(CONTRACT_TEXT_NO_ACCENT) >= 200 and len(GARBAGE_TEXT) >= 200


def test_short_document_is_blocked():
    answer = document_gate.check([doc("x" * 50)])

    assert answer is not None
    assert document_gate.TOO_SHORT_REASON in answer and document_gate.RETRY_HINT in answer
    assert "số" not in answer  # chỉ 1 tài liệu thì không đánh số


def test_empty_and_whitespace_documents_are_blocked():
    assert document_gate.check([doc("")]) is not None
    assert document_gate.check([doc(None)]) is not None
    assert document_gate.check([doc(" \n\t " * 100)]) is not None  # toàn khoảng trắng: sau strip là rỗng


def test_vietnamese_contract_passes():
    assert document_gate.check([doc(CONTRACT_TEXT, "docx")]) is None


def test_vietnamese_without_accents_passes():
    assert document_gate.check([doc(CONTRACT_TEXT_NO_ACCENT)]) is None


def test_long_garbage_is_blocked_as_unreadable():
    answer = document_gate.check([doc(GARBAGE_TEXT)])

    assert answer is not None
    assert document_gate.UNREADABLE_REASON in answer and document_gate.TOO_SHORT_REASON not in answer
    assert "tải lên bản rõ hơn" in answer and "dán nội dung" in answer


def test_second_of_two_documents_broken_names_document_2():
    answer = document_gate.check([doc(CONTRACT_TEXT, "docx"), doc(GARBAGE_TEXT)])

    assert "tài liệu số 2" in answer and "tài liệu số 1" not in answer
    assert document_gate.UNREADABLE_REASON in answer


def test_every_broken_document_is_named_in_order():
    answer = document_gate.check([doc("ngắn"), doc(CONTRACT_TEXT), doc(GARBAGE_TEXT)])

    assert answer.index("tài liệu số 1") < answer.index("tài liệu số 3")
    assert "tài liệu số 2" not in answer
    assert document_gate.TOO_SHORT_REASON in answer and document_gate.UNREADABLE_REASON in answer


def test_all_documents_good_returns_none():
    assert document_gate.check([doc(CONTRACT_TEXT, "docx"), doc(CONTRACT_TEXT_NO_ACCENT, "pdf_text")]) is None


def test_no_documents_returns_none():
    assert document_gate.check([]) is None


def test_ratio_is_measured_on_first_5000_chars_only():
    good_head = (CONTRACT_TEXT + " ") * 20  # hơn 5000 ký tự chữ tốt, phần sau toàn rác
    assert len(good_head) > document_gate.VALID_RATIO_SAMPLE_CHARS

    assert document_gate.check([doc(good_head + GARBAGE_TEXT * 100)]) is None


def test_thresholds_come_from_settings(monkeypatch):
    monkeypatch.setenv("DOCUMENT_MIN_CHARS", "10")
    get_settings.cache_clear()
    assert document_gate.check([doc("Bên A bán đất cho bên B.")]) is None  # 24 ký tự, đủ với ngưỡng 10

    monkeypatch.setenv("DOCUMENT_MIN_CHARS", "5000")
    get_settings.cache_clear()
    assert document_gate.check([doc(CONTRACT_TEXT)]) is not None

    monkeypatch.setenv("DOCUMENT_MIN_CHARS", "200")
    monkeypatch.setenv("DOCUMENT_MIN_VALID_RATIO", "1.0")
    get_settings.cache_clear()
    half_garbage = (CONTRACT_TEXT + " " + GARBAGE_TEXT)[:600]
    assert document_gate.check([doc(half_garbage)]) is not None


def test_logs_one_info_line_per_document(caplog):
    caplog.set_level(logging.INFO, logger="app.services.document_gate")

    document_gate.check([doc(CONTRACT_TEXT, "docx"), doc(GARBAGE_TEXT)], request_id="req-gate")

    lines = [r.getMessage() for r in caplog.records if "agent kiểm tra tài liệu" in r.getMessage()]
    assert len(lines) == 2
    assert all("request_id=req-gate" in line for line in lines)
    assert "số=1" in lines[0] and "loại=docx" in lines[0] and "kết_quả=đạt" in lines[0]
    assert f"ký_tự={len(CONTRACT_TEXT)}" in lines[0]
    assert float(lines[0].split("tỷ_lệ_từ_hợp_lệ=")[1].split()[0]) > 0.9  # "B" đứng riêng không phải âm tiết nên chưa tới 1.00
    assert "số=2" in lines[1] and "kết_quả=chữ_rác" in lines[1] and "tỷ_lệ_từ_hợp_lệ=0.00" in lines[1]


def test_short_document_log_says_too_short(caplog):
    caplog.set_level(logging.INFO, logger="app.services.document_gate")

    document_gate.check([doc("quá ngắn")])

    line = next(r.getMessage() for r in caplog.records if "agent kiểm tra tài liệu" in r.getMessage())
    assert "kết_quả=quá_ngắn" in line and "request_id=None" in line


@pytest.mark.parametrize("name,expected", [
    ("document_gate_enabled", False), ("document_min_chars", 200), ("document_min_valid_ratio", 0.5),
])
def test_gate_settings_defaults(name, expected):
    assert getattr(get_settings(), name) == expected


# ── facts: dữ kiện hệ thống tự đếm cho prompt rà soát (DOCUMENT_FACTS_ENABLED) ───

def blank_doc(count, filler="........", kind="docx"):
    """Tài liệu có đúng `count` chỗ trống `filler` (mỗi dòng một chỗ), còn lại là chữ bình thường."""
    return doc("".join(f"Họ và tên người bán: {filler}\n" for _ in range(count)) + CONTRACT_TEXT, kind)


def test_facts_default_settings():
    assert get_settings().document_facts_enabled is False and get_settings().document_placeholder_min == 5


def test_facts_many_placeholders_says_unfilled_template():
    assert document_gate.facts([blank_doc(10)]) == ["Tài liệu có 10 chỗ để trống, nhiều khả năng là mẫu chưa điền."]


def test_facts_few_placeholders_says_nothing():
    assert document_gate.facts([blank_doc(2)]) == []


def test_facts_placeholder_threshold_boundary(monkeypatch):
    assert document_gate.facts([blank_doc(4)]) == []
    assert len(document_gate.facts([blank_doc(5)])) == 1  # mặc định DOCUMENT_PLACEHOLDER_MIN = 5

    monkeypatch.setenv("DOCUMENT_PLACEHOLDER_MIN", "3")
    get_settings.cache_clear()
    assert len(document_gate.facts([blank_doc(3)])) == 1
    assert document_gate.facts([blank_doc(2)]) == []


@pytest.mark.parametrize("filler", ["........", "………", "________", "-----"])
def test_facts_counts_the_kinds_of_blank_that_contract_review_recognises(filler):
    assert document_gate.facts([blank_doc(6, filler)]) == ["Tài liệu có 6 chỗ để trống, nhiều khả năng là mẫu chưa điền."]


def test_facts_placeholder_count_matches_contract_review_pattern():
    text = "Họ tên: .... ... .. ………\n" * 3 + CONTRACT_TEXT
    count = len(contract_review._PLACEHOLDER.findall(text))

    assert count == 9  # mỗi dòng ".... ... .. ………" có 3 chỗ: "....", "...", "………"; ".." quá ngắn nên không tính
    assert document_gate.facts([doc(text)]) == ["Tài liệu có 9 chỗ để trống, nhiều khả năng là mẫu chưa điền."]


def test_facts_short_document_has_no_truncation_sentence():
    assert document_gate.facts([doc(CONTRACT_TEXT)]) == []
    assert document_gate.facts([doc("a" * prompts.MAX_DOCUMENT_CHARS)]) == []  # đúng bằng giới hạn: chưa bị cắt


def test_facts_long_document_says_how_much_was_read():
    text = "abcd " * 4000  # sau strip còn 19999 ký tự; ký tự thứ 12000 là dấu cách nên phần giữ lại bị rstrip còn 11999

    assert document_gate.facts([doc(text)]) == [
        "Hệ thống chỉ đọc 11999 trên 19999 ký tự của tài liệu, phần còn lại không được xem xét.",
    ]


def test_facts_long_document_without_trailing_space_reads_exactly_the_limit():
    assert document_gate.facts([doc("a" * 15000)]) == [
        "Hệ thống chỉ đọc 12000 trên 15000 ký tự của tài liệu, phần còn lại không được xem xét.",
    ]


def test_facts_two_documents_share_the_limit_and_name_the_document():
    half = prompts.MAX_DOCUMENT_CHARS // 2
    docs = [blank_doc(10), doc("a" * (half + 100))]

    assert document_gate.facts(docs) == [
        "Tài liệu số 1 có 10 chỗ để trống, nhiều khả năng là mẫu chưa điền.",
        f"Hệ thống chỉ đọc {half} trên {half + 100} ký tự của tài liệu số 2, phần còn lại không được xem xét.",
    ]


def test_facts_document_between_half_and_full_limit_is_cut_only_when_two_documents():
    text = "a" * 8000  # dưới 12000 nhưng trên một nửa

    assert document_gate.facts([doc(text)]) == []
    assert document_gate.facts([doc(text), doc(CONTRACT_TEXT)]) == [
        "Hệ thống chỉ đọc 6000 trên 8000 ký tự của tài liệu số 1, phần còn lại không được xem xét.",
    ]


def test_facts_without_documents_or_text_is_empty():
    assert document_gate.facts([]) == []
    assert document_gate.facts([doc(None), doc("")]) == []


@pytest.mark.parametrize("count", [1, 2, 3])
def test_facts_chars_read_equal_what_format_documents_keeps(count):
    texts = ["abcd " * 4000, "x" * 20000, ("Điều khoản " * 900) + "   \n\n" + "y" * 5000]
    docs = [doc(texts[i % len(texts)], "docx") for i in range(count)]

    block = prompts.format_documents(docs)
    sentences = document_gate.facts(docs)

    reads = {}
    for sentence in sentences:
        match = re.search(r"đọc (\d+) trên (\d+) ký tự của tài liệu( số (\d+))?", sentence)
        reads[int(match.group(4) or 1)] = (int(match.group(1)), int(match.group(2)))
    for number, document in enumerate(docs, start=1):
        shown = re.search(
            rf"<<<TÀI LIỆU {number} \(.*?\)>>>\n(.*?)\n<<<HẾT TÀI LIỆU {number}>>>", block, re.DOTALL,
        ).group(1)
        total = len(document["text"].strip())
        if shown.endswith(prompts.TRUNCATED_MARK):
            kept = shown[: -len(prompts.TRUNCATED_MARK) - 1]
            assert reads[number] == (len(kept), total)
        else:
            assert number not in reads and len(shown) == total


def test_facts_logs_one_info_line_per_document(caplog):
    caplog.set_level(logging.INFO, logger="app.services.document_gate")

    document_gate.facts([blank_doc(7), doc("a" * 20000)], request_id="req-facts")

    lines = [r.getMessage() for r in caplog.records if "agent dữ kiện tài liệu" in r.getMessage()]
    assert len(lines) == 2 and all("request_id=req-facts" in line for line in lines)
    assert "số=1" in lines[0] and "chỗ_trống=7" in lines[0]
    assert f"ký_tự_đọc={len(blank_doc(7)['text'].strip())}" in lines[0]
    assert "số=2" in lines[1] and "chỗ_trống=0" in lines[1] and "ký_tự_đọc=6000 tổng=20000" in lines[1]


# ── ask_contract_type: hỏi lại khi không nhận ra loại hợp đồng (DOCUMENT_ASK_TYPE_ENABLED) ──

@pytest.mark.parametrize("contract_type", list(contract_review.CONTRACT_PATTERNS))
def test_ask_contract_type_recognised_type_returns_none(contract_type):
    assert document_gate.ask_contract_type(contract_type, "kiểm tra giúp tôi") is None


@pytest.mark.parametrize("question", ["kiểm tra giúp tôi", "xem giúp", "check", "nhận xét hợp đồng này"])
def test_ask_contract_type_unknown_type_and_generic_question_asks_back(question):
    answer = document_gate.ask_contract_type("khac", question)

    assert answer == document_gate.ASK_TYPE_ANSWER
    assert "chưa nhận ra" in answer and "loại hợp đồng" in answer and "hỏi cụ thể" in answer


def test_ask_contract_type_lists_every_supported_type():
    answer = document_gate.ASK_TYPE_ANSWER

    for label in (
        "chuyển nhượng quyền sử dụng đất", "tặng cho đất", "thế chấp", "thuê đất hoặc nhà", "mua bán nhà",
        "mua bán hàng hoá", "hợp đồng dịch vụ", "góp vốn thành lập doanh nghiệp",
    ):
        assert label in answer
    assert len(contract_review.CONTRACT_PATTERNS) == 8  # đổi CONTRACT_PATTERNS thì phải đổi ASK_TYPE_ANSWER


@pytest.mark.parametrize("question", ["điều khoản phạt cọc có hợp lý không", "đây là hợp đồng thuê nhà"])
def test_ask_contract_type_specific_question_returns_none(question):
    assert document_gate.ask_contract_type("khac", question) is None


def test_ask_contract_type_uses_is_specific_of_contract_review(monkeypatch):
    monkeypatch.setattr(contract_review, "_is_specific", lambda question: True)
    assert document_gate.ask_contract_type("khac", "kiểm tra giúp tôi") is None


def test_ask_contract_type_logs_only_when_asking(caplog):
    caplog.set_level(logging.INFO, logger="app.services.document_gate")

    document_gate.ask_contract_type("chuyen_nhuong_dat", "kiểm tra giúp tôi", request_id="req-a")
    document_gate.ask_contract_type("khac", "điều khoản phạt cọc có hợp lý không", request_id="req-a")
    assert not [r for r in caplog.records if "hỏi lại loại hợp đồng" in r.getMessage()]

    document_gate.ask_contract_type("khac", "kiểm tra giúp tôi", request_id="req-a")
    lines = [r.getMessage() for r in caplog.records if "hỏi lại loại hợp đồng" in r.getMessage()]
    assert len(lines) == 1 and "request_id=req-a" in lines[0]


def test_ask_type_setting_default_off():
    assert get_settings().document_ask_type_enabled is False


# ── facts với include_checklist: mục thường gặp của hợp đồng chưa thấy từ khoá (DOCUMENT_CHECKLIST_ENABLED) ──

CHECKLIST_PARTIES = "HỢP ĐỒNG CHUYỂN NHƯỢNG QUYỀN SỬ DỤNG ĐẤT\nBên A (bên bán): Nguyễn Văn An. Bên B (bên mua): Trần Thị Bình.\n"
CHECKLIST_PRICE = "Điều 1. Giá chuyển nhượng là 3.500.000.000 đồng, thanh toán thành hai đợt.\n"
CHECKLIST_TERM = "Điều 2. Thời hạn thực hiện hợp đồng là 60 ngày, hợp đồng có hiệu lực kể từ ngày ký.\n"
CHECKLIST_RIGHTS = "Điều 3. Quyền và nghĩa vụ của các bên được thực hiện theo thỏa thuận dưới đây.\n"
CHECKLIST_VIOLATION = "Điều 4. Bên nào vi phạm hợp đồng thì chịu phạt và bồi thường thiệt hại.\n"
CHECKLIST_DISPUTE = "Điều 5. Tranh chấp được hòa giải, nếu không được thì đưa ra Tòa án có thẩm quyền.\n"
CHECKLIST_SIGNATURE = "Đại diện các bên ký tên. Ngày 10 tháng 3 năm 2025.\n"
FULL_CONTRACT = (
    CHECKLIST_PARTIES + CHECKLIST_PRICE + CHECKLIST_TERM + CHECKLIST_RIGHTS + CHECKLIST_VIOLATION
    + CHECKLIST_DISPUTE + CHECKLIST_SIGNATURE
)
FULL_CONTRACT_NO_ACCENT = (
    "HOP DONG CHUYEN NHUONG QUYEN SU DUNG DAT\nBen A (ben ban): Nguyen Van An. Ben B (ben mua): Tran Thi Binh.\n"
    "Dieu 1. Gia chuyen nhuong la 3.500.000.000 dong, thanh toan thanh hai dot.\n"
    "Dieu 2. Thoi han thuc hien hop dong la 60 ngay, hop dong co hieu luc ke tu ngay ky.\n"
    "Dieu 3. Quyen va nghia vu cua cac ben duoc thuc hien theo thoa thuan duoi day.\n"
    "Dieu 4. Ben nao vi pham hop dong thi chiu phat va boi thuong thiet hai.\n"
    "Dieu 5. Tranh chap duoc hoa giai, neu khong duoc thi dua ra Toa an co tham quyen.\n"
    "Dai dien cac ben ky ten. Ngay 10 thang 3 nam 2025.\n"
)
CHECKLIST_NAMES = [name for name, _ in document_gate.CHECKLIST]
NOT_SEEN = "Kiểm bằng từ khoá, chưa thấy nhắc tới: {}. Đây là kiểm tra tự động có thể sót, hãy đọc lại tài liệu để xác nhận trước khi kết luận là thiếu."


def test_checklist_has_the_seven_expected_items():
    assert CHECKLIST_NAMES == [
        "Thông tin các bên", "Giá trị và phương thức thanh toán", "Thời hạn, thời gian thực hiện hoặc hiệu lực",
        "Quyền và nghĩa vụ của các bên", "Trách nhiệm khi vi phạm", "Giải quyết tranh chấp",
        "Chữ ký, đại diện các bên và ngày ký",
    ]


def test_checklist_full_contract_has_no_not_seen_sentence():
    assert document_gate.facts([doc(FULL_CONTRACT)], include_checklist=True) == []


def test_checklist_missing_dispute_and_violation_names_exactly_those_two():
    text = CHECKLIST_PARTIES + CHECKLIST_PRICE + CHECKLIST_TERM + CHECKLIST_RIGHTS + CHECKLIST_SIGNATURE

    assert document_gate.facts([doc(text)], include_checklist=True) == [
        NOT_SEEN.format("Trách nhiệm khi vi phạm, Giải quyết tranh chấp"),
    ]


@pytest.mark.parametrize("index", range(7))
def test_checklist_each_item_is_reported_when_it_alone_is_missing(index):
    parts = [CHECKLIST_PARTIES, CHECKLIST_PRICE, CHECKLIST_TERM, CHECKLIST_RIGHTS, CHECKLIST_VIOLATION,
             CHECKLIST_DISPUTE, CHECKLIST_SIGNATURE]
    text = "".join(part for i, part in enumerate(parts) if i != index)

    assert document_gate.facts([doc(text)], include_checklist=True) == [NOT_SEEN.format(CHECKLIST_NAMES[index])]


def test_checklist_text_without_accents_still_matches():
    assert document_gate.facts([doc(FULL_CONTRACT_NO_ACCENT)], include_checklist=True) == []


def test_checklist_money_word_dong_does_not_match_the_word_hop_dong():
    # "hợp đồng" bỏ dấu thành "hop dong": không được tính là có giá; chỉ "đồng" đứng ngay sau số mới tính
    text = "Hợp đồng này lập tại phường Tân Phú. Hai bên đồng ý các điều khoản."

    assert "Giá trị và phương thức thanh toán" in document_gate.facts([doc(text)], include_checklist=True)[0]
    assert "Giá trị và phương thức thanh toán" not in document_gate.facts([doc(text + " Trả 5 đồng.")], include_checklist=True)[0]


def test_checklist_keyword_only_in_the_cut_part_counts_as_not_seen():
    head = FULL_CONTRACT.replace(CHECKLIST_DISPUTE, "")
    filler = "a " * (prompts.MAX_DOCUMENT_CHARS // 2)  # đẩy câu tranh chấp ra ngoài phần AI được đọc
    cut_text = head + filler + "\n" + CHECKLIST_DISPUTE
    assert len(cut_text) > prompts.MAX_DOCUMENT_CHARS

    sentences = document_gate.facts([doc(cut_text)], include_checklist=True)

    not_seen = [s for s in sentences if s.startswith("Kiểm bằng từ khoá")]
    assert not_seen == [NOT_SEEN.format("Giải quyết tranh chấp")]
    assert any(s.startswith("Hệ thống chỉ đọc") for s in sentences)  # câu báo bị cắt vẫn có
    # cùng nội dung nhưng câu tranh chấp nằm trong phần được đọc thì không báo
    assert document_gate.facts([doc(FULL_CONTRACT + filler)], include_checklist=True)[0].startswith("Hệ thống chỉ đọc")
    assert [s for s in document_gate.facts([doc(FULL_CONTRACT + filler)], include_checklist=True)
            if s.startswith("Kiểm bằng từ khoá")] == []


def test_checklist_two_documents_names_the_document_and_uses_half_the_limit():
    sentences = document_gate.facts([doc(FULL_CONTRACT), doc("Bản ghi chú vắn tắt.")], include_checklist=True)

    assert sentences == [
        f"Kiểm bằng từ khoá, ở tài liệu số 2 chưa thấy nhắc tới: {', '.join(CHECKLIST_NAMES)}. "
        "Đây là kiểm tra tự động có thể sót, hãy đọc lại tài liệu để xác nhận trước khi kết luận là thiếu."
    ]


def test_checklist_unfilled_template_is_skipped():
    sentences = document_gate.facts([blank_doc(10)], include_checklist=True)

    assert sentences == ["Tài liệu có 10 chỗ để trống, nhiều khả năng là mẫu chưa điền."]


def test_checklist_template_with_few_blanks_is_still_checked():
    sentences = document_gate.facts([blank_doc(4)], include_checklist=True)  # dưới ngưỡng 5 nên chưa coi là mẫu trống

    assert len(sentences) == 1 and sentences[0].startswith("Kiểm bằng từ khoá")


def test_checklist_skips_only_the_blank_template_among_documents():
    sentences = document_gate.facts([blank_doc(10), doc("Bản ghi chú vắn tắt.")], include_checklist=True)

    assert sentences[0] == "Tài liệu số 1 có 10 chỗ để trống, nhiều khả năng là mẫu chưa điền."
    assert len(sentences) == 2 and "ở tài liệu số 2" in sentences[1] and "tài liệu số 1 chưa" not in sentences[1]


def test_facts_without_include_checklist_is_exactly_as_before():
    documents = [blank_doc(10), doc("a" * 8000), doc("Bản ghi chú vắn tắt.")]

    assert document_gate.facts(documents) == document_gate.facts(documents, include_checklist=False)
    assert not [s for s in document_gate.facts(documents) if "từ khoá" in s]


def test_checklist_logs_result_per_document(caplog):
    caplog.set_level(logging.INFO, logger="app.services.document_gate")

    document_gate.facts([doc(FULL_CONTRACT), blank_doc(10), doc("Bản ghi chú vắn tắt.")], request_id="req-c", include_checklist=True)

    lines = [r.getMessage() for r in caplog.records if "agent kiểm mục hợp đồng" in r.getMessage()]
    assert len(lines) == 3 and all("request_id=req-c" in line for line in lines)
    assert "số=1" in lines[0] and "chưa_thấy=0/7" in lines[0]
    assert "số=2" in lines[1] and "bỏ_qua_mẫu_trống" in lines[1]
    assert "số=3" in lines[2] and "chưa_thấy=7/7" in lines[2]
    # dòng log dữ kiện cũ không đổi: vẫn đúng 1 dòng mỗi tài liệu
    assert len([r for r in caplog.records if "agent dữ kiện tài liệu" in r.getMessage()]) == 3


def test_checklist_setting_default_off():
    assert get_settings().document_checklist_enabled is False
