import copy
import unicodedata

import pytest

from app.services.transcript_validator import SCHEMA_VERSION, SummaryInvalid, validate_summary

MESSAGES = [
    {"id": "m101", "content": "Chào luật sư, năm 2019 tôi mua mảnh đất 120m2 thửa số 45 của ông Trần Văn Bình giá 850 triệu, chỉ viết giấy tay. Giờ ông ấy đòi lại đất."},
    {"id": "m102", "content": "Giấy tay đó có công chứng hay chứng thực không anh? Theo Điều 167 Luật Đất đai thì hợp đồng chuyển nhượng phải công chứng hoặc chứng thực. Trường hợp của anh có thể vẫn được công nhận nếu đã trả trên 2/3 tiền, nhưng cần xem giấy tờ mới nói chắc được."},
    {"id": "m103", "content": "Không công chứng ạ. Tôi trả đủ 800 triệu rồi."},
    {"id": "m104", "content": "Anh chụp giúp tôi giấy tay và biên nhận tiền gửi qua đây nhé."},
]

VALID = {
    "has_substance": True,
    "legal_domains": ["dat_dai", "dan_su"],
    "overview": "Người dân cho biết đã mua đất bằng giấy tay không công chứng năm 2019, nay bên bán đòi lại đất.",
    "citizen_issues": [{"content": "Bên bán đòi lại mảnh đất đã mua bằng giấy tay.", "source_ids": ["m101"]}],
    "facts": [
        {"type": "date", "value": "năm 2019", "context": "Thời điểm mua đất", "stated_by": "citizen", "source_ids": ["m101"]},
        {"type": "area", "value": "120m2", "context": "Diện tích", "stated_by": "citizen", "source_ids": ["m101"]},
        {"type": "person", "value": "ông Trần Văn Bình", "context": "Bên bán", "stated_by": "citizen", "source_ids": ["m101"]},
        {"type": "amount", "value": "800 triệu", "context": "Đã trả", "stated_by": "citizen", "source_ids": ["m103"]},
    ],
    "lawyer_advice": [{"content": "Hợp đồng chuyển nhượng đất phải công chứng hoặc chứng thực.", "source_ids": ["m102"]}],
    "legal_references": [
        {"document": "Luật Đất đai", "article": "167", "clause": None, "point": None,
         "raw_text": "Điều 167 Luật Đất đai", "cited_by": "lawyer", "source_ids": ["m102"]}],
    "deadlines": [],
    "next_steps": [{"actor": "citizen", "content": "Chụp và gửi giấy tay cùng biên nhận.", "source_ids": ["m104"]}],
    "conflicts": [],
    "open_questions": [{"content": "Giá 850 triệu nhưng trả đủ 800 triệu, chưa rõ.", "source_ids": ["m101", "m103"]}],
}

EMPTY = {"has_substance": False, "legal_domains": [], "overview": None, "citizen_issues": [], "facts": [],
         "lawyer_advice": [], "legal_references": [], "deadlines": [], "next_steps": [], "conflicts": [],
         "open_questions": []}


def case(**changes):
    data = copy.deepcopy(VALID)
    data.update(changes)
    return data


def test_hop_le_va_moi_gia_tri_nguyen_van_deu_verified():
    out = validate_summary(VALID, MESSAGES)
    assert SCHEMA_VERSION == 1
    assert all(f["verified"] for f in out["facts"])
    assert out["legal_references"][0]["verified"] is True
    assert "verified" not in out["lawyer_advice"][0]


def test_gia_tri_sai_chu_thi_verified_false():
    data = case()
    data["facts"][3]["value"] = "800.000.000"  # tự đổi định dạng, không có trong tin nguồn
    data["legal_references"][0]["raw_text"] = "Điều 168 Luật Đất đai"
    out = validate_summary(data, MESSAGES)
    assert [f["verified"] for f in out["facts"]] == [True, True, True, False]
    assert out["legal_references"][0]["verified"] is False


def test_gia_tri_dung_nhung_sai_tin_nguon_thi_verified_false():
    data = case()
    data["facts"][3]["source_ids"] = ["m104"]  # "800 triệu" nằm ở m103, không phải m104
    assert validate_summary(data, MESSAGES)["facts"][3]["verified"] is False


def test_nfd_va_nfc_van_khop():
    nfd = [{"id": "m1", "content": unicodedata.normalize("NFD", "Theo Điều 167  Luật Đất đai")}]
    data = copy.deepcopy(EMPTY)
    data.update(has_substance=True, overview="x", legal_references=[
        {"document": None, "article": None, "clause": None, "point": None,
         "raw_text": "điều 167 luật đất đai", "cited_by": "lawyer", "source_ids": ["m1"]}])
    assert validate_summary(data, nfd)["legal_references"][0]["verified"] is True


def test_source_id_bia_bi_tu_choi():
    data = case()
    data["lawyer_advice"][0]["source_ids"] = ["m999"]
    with pytest.raises(SummaryInvalid):
        validate_summary(data, MESSAGES)


def test_khong_co_noi_dung_phap_ly_hop_le():
    assert validate_summary(EMPTY, MESSAGES)["has_substance"] is False


def test_has_substance_false_ma_con_du_lieu_bi_tu_choi():
    for extra in ({"overview": "còn"}, {"legal_domains": ["khac"]}, {"citizen_issues": VALID["citizen_issues"]}):
        with pytest.raises(SummaryInvalid):
            validate_summary({**EMPTY, **extra}, MESSAGES)


@pytest.mark.parametrize("change", [
    {"extra": 1},                                  # trường thừa
    {"legal_domains": ["khong_co"]},               # sai enum
    {"overview": "x" * 601},                       # quá dài
    {"has_substance": "true"},                     # sai kiểu
    {"citizen_issues": [{"content": "a", "source_ids": []}]},   # thiếu source_ids
    {"conflicts": [{"topic": "t", "citizen_statement": "a", "lawyer_statement": "b", "source_ids": ["m101"]}]},
    {"next_steps": VALID["next_steps"] * 16},      # quá 15 phần tử
])
def test_sai_cau_truc_bi_tu_choi(change):
    with pytest.raises(SummaryInvalid):
        validate_summary({**VALID, **change}, MESSAGES)


def test_thieu_truong_bi_tu_choi():
    data = case()
    del data["deadlines"]
    with pytest.raises(SummaryInvalid):
        validate_summary(data, MESSAGES)
