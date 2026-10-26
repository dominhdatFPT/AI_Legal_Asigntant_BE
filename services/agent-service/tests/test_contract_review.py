"""Nhận diện loại hợp đồng, dựng câu tìm luật và gộp kết quả tìm (không gọi mạng, không gọi AI)."""
import pytest

from app.services import contract_review as cr

TITLES = {
    "chuyen_nhuong_dat": ["HỢP ĐỒNG CHUYỂN NHƯỢNG MỘT PHẦN QUYỀN SỬ DỤNG ĐẤT", "hop dong chuyen nhuong quyen su dung dat"],
    "tang_cho_dat": ["HỢP ĐỒNG TẶNG CHO QUYỀN SỬ DỤNG ĐẤT", "hop dong tang cho quyen su dung dat"],
    "thue_dat_nha": ["HỢP ĐỒNG THUÊ NHÀ Ở", "Hop dong cho thue quyen su dung dat"],
    "the_chap": ["HỢP ĐỒNG THẾ CHẤP QUYỀN SỬ DỤNG ĐẤT", "hop dong the chap tai san"],
    "mua_ban_nha": ["HỢP ĐỒNG MUA BÁN NHÀ Ở", "hop dong mua ban can ho chung cu"],
    "mua_ban_hang_hoa": ["HỢP ĐỒNG MUA BÁN HÀNG HÓA", "Hợp đồng mua bán", "hop dong cung ung hang hoa"],
    "dich_vu": ["HỢP ĐỒNG DỊCH VỤ KẾ TOÁN", "hop dong cung cap dich vu van chuyen"],
    "gop_von_doanh_nghiep": ["HỢP ĐỒNG GÓP VỐN THÀNH LẬP CÔNG TY", "hop dong gop von"],
}
CASES = [(title, kind) for kind, titles in TITLES.items() for title in titles]


@pytest.mark.parametrize("title,kind", CASES)
def test_detect_each_type_with_and_without_accents(title, kind):
    body = "\nCỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\nBên A: Nguyễn Văn A. Bên B: Trần Thị B."
    assert cr.detect_contract_type(title + body) == kind
    assert cr.detect_contract_type("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\n" + title) == kind


@pytest.mark.parametrize("text", ["", "BIÊN BẢN HỌP GIA ĐÌNH", "Đơn xin cấp lại sổ đỏ", "Lorem ipsum dolor sit amet"])
def test_unrecognised_text_is_khac(text):
    assert cr.detect_contract_type(text) == "khac"


def test_title_wins_over_later_mentions():
    text = "HỢP ĐỒNG CHUYỂN NHƯỢNG QUYỀN SỬ DỤNG ĐẤT\nThửa đất không thế chấp, không cho thuê nhà, không góp vốn."
    assert cr.detect_contract_type(text) == "chuyen_nhuong_dat"


def test_specific_sale_beats_generic_sale_even_though_generic_is_earlier():
    assert cr.detect_contract_type("Hợp đồng mua bán nhà ở") == "mua_ban_nha"


def test_only_the_first_3000_characters_are_read():
    assert cr.detect_contract_type("x" * 3000 + "hợp đồng chuyển nhượng quyền sử dụng đất") == "khac"
    assert cr.detect_contract_type("x" * 2900 + " hợp đồng góp vốn") == "gop_von_doanh_nghiep"


def test_every_typed_contract_has_3_or_4_aspects():
    assert set(cr.REVIEW_QUERIES) == set(TITLES)
    assert all(3 <= len(q) <= 4 for q in cr.REVIEW_QUERIES.values())


def test_review_queries_generic_question_is_not_added():
    for question in ["kiểm tra giúp tôi", "Xem giúp", "nhận xét hợp đồng này", "Hợp đồng này có hợp lệ không?", "check"]:
        assert cr.review_queries("chuyen_nhuong_dat", question) == cr.REVIEW_QUERIES["chuyen_nhuong_dat"]


def test_review_queries_specific_question_goes_first_and_total_is_capped():
    queries = cr.review_queries("chuyen_nhuong_dat", "Thuế khi chuyển nhượng ai nộp?")
    assert queries[0] == "Thuế khi chuyển nhượng ai nộp?"
    assert queries[1:] == cr.REVIEW_QUERIES["chuyen_nhuong_dat"]
    assert len(queries) == 5


def test_review_queries_khac_uses_document_opening_without_placeholders():
    text = "BIÊN BẢN  thỏa thuận ......... giữa bên A ………… và bên B " + "x" * 500
    [query] = cr.review_queries("khac", "kiểm tra giúp tôi", text)
    assert query.startswith("BIÊN BẢN thỏa thuận giữa bên A và bên B") and len(query) == 300
    assert cr.review_queries("khac", "kiểm tra giúp tôi", "") == []


def test_review_queries_never_exceed_rag_limit():
    [query] = cr.review_queries("khac", "câu hỏi " + "dài " * 1000)[:1]
    assert len(query) <= 1000
    assert all(len(q) <= 1000 for q in cr.review_queries("the_chap", "hỏi " * 600))


def chunks(*ids):
    return [{"id": i, "content": f"nội dung {i}"} for i in ids]


def test_merge_interleaves_and_drops_duplicates():
    merged = cr.merge_chunks([chunks("a1", "a2", "x"), chunks("b1", "x", "b3"), chunks("c1")])
    assert [c["id"] for c in merged] == ["a1", "b1", "c1", "a2", "x", "b3"]


def test_merge_caps_at_limit():
    merged = cr.merge_chunks([chunks(*[f"a{i}" for i in range(8)]), chunks(*[f"b{i}" for i in range(8)])])
    assert len(merged) == cr.MAX_REVIEW_CHUNKS == 10
    assert [c["id"] for c in merged[:4]] == ["a0", "b0", "a1", "b1"]


def test_merge_handles_empty_input_and_chunks_without_id():
    assert cr.merge_chunks([]) == [] and cr.merge_chunks([[], []]) == []
    no_id = [{"content": "giống nhau"}]
    assert len(cr.merge_chunks([no_id, no_id])) == 1
