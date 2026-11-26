import unicodedata

import pytest

from app.services.chunking import chunk_legal_text, embedding_text, normalize_text


def test_cat_theo_dieu_va_bo_phan_mo_dau(sample_text):
    chunks = chunk_legal_text(sample_text)
    assert [c.section_info["dieu"] for c in chunks] == ["1", "2", "3", "4", "5", "6"]
    # Phần "Căn cứ Hiến pháp..." trước Điều 1 không thành chunk
    assert all("Căn cứ" not in c.content for c in chunks)
    assert chunks[0].content.startswith("Điều 1. Phạm vi điều chỉnh")


def test_ghi_nhan_chuong(sample_text):
    by_dieu = {c.section_info["dieu"]: c for c in chunk_legal_text(sample_text)}
    assert by_dieu["1"].section_info["chuong"] == "I"
    assert by_dieu["4"].section_info["chuong"] == "II"
    assert by_dieu["6"].section_info["chuong"] == "III"


def test_ten_chuong_va_muc_khong_dinh_vao_dieu_truoc(sample_text):
    by_dieu = {c.section_info["dieu"]: c for c in chunk_legal_text(sample_text)}
    assert "QUYỀN VÀ NGHĨA VỤ" not in by_dieu["2"].content
    assert "Mục 2" not in by_dieu["4"].content
    assert "THỪA KẾ" not in by_dieu["4"].content


def test_khong_nham_dieu_giua_cau(sample_text):
    # "trừ trường hợp quy định tại Điều 5 của Luật này" nằm trong Điều 4, không mở Điều mới
    by_dieu = {c.section_info["dieu"]: c for c in chunk_legal_text(sample_text)}
    assert "quy định tại Điều 5" in by_dieu["4"].content


def test_dieu_dai_tach_theo_khoan_kem_ten_dieu(sample_text):
    chunks = [c for c in chunk_legal_text(sample_text, max_chars=400) if c.section_info["dieu"] == "4"]
    assert len(chunks) > 1
    assert all(c.content.startswith("Điều 4. Điều kiện chuyển nhượng") for c in chunks)
    assert {c.section_info["khoan"] for c in chunks} == {"1", "2", "3"}


def test_khoan_dai_tach_theo_diem_kem_cau_dan(sample_text):
    chunks = [
        c for c in chunk_legal_text(sample_text, max_chars=300)
        if c.section_info["dieu"] == "4" and c.section_info["khoan"] == "1"
    ]
    assert [c.section_info["diem"] for c in chunks] == ["a", "b", "c", "d"]
    for c in chunks:
        assert "có đủ các điều kiện sau đây" in c.content  # câu dẫn của khoản 1
    assert "kê biên" in chunks[2].content


def test_moi_chunk_khong_vuot_max_chars(sample_text):
    for max_chars in (250, 400, 800, 2500):
        for c in chunk_legal_text(sample_text, max_chars=max_chars):
            assert len(c.content) <= max_chars, (max_chars, c.section_info)


def test_dong_qua_dai_bi_cat(sample_text):
    text = "Điều 1. Rất dài\n" + ("chữ " * 2000)
    chunks = chunk_legal_text(text, max_chars=500)
    assert len(chunks) > 1
    assert all(len(c.content) <= 500 and c.content.startswith("Điều 1.") for c in chunks)


def test_van_ban_khong_co_dieu_cat_theo_doan():
    text = "Đoạn một nói về thủ tục.\n\nĐoạn hai nói về lệ phí.\n\n" + "Đoạn ba. " * 100
    chunks = chunk_legal_text(text, max_chars=300)
    assert chunks
    assert all(c.section_info == {"chuong": None, "muc": None, "dieu": None, "khoan": None, "diem": None} for c in chunks)
    assert chunks[0].content.startswith("Đoạn một")


@pytest.mark.parametrize(
    "reference_line",
    [
        "Mục 1 của Phụ lục này quy định về hồ sơ.",
        "mục 12 được áp dụng cho trường hợp này.",
        "MỤC 2 của văn bản này vẫn có hiệu lực.",
        "Mục 1, Mục 2 của Phụ lục này được áp dụng.",
        "Mục 1 và Mục 2 quy định về thủ tục.",
        "Điều 5 của Luật này quy định về hồ sơ.",
    ],
)
def test_cau_tham_chieu_dau_dong_khong_lam_mat_noi_dung_dieu(reference_line):
    first_article = (
        "Điều 1. Phạm vi áp dụng\n"
        "1. Nội dung trước câu tham chiếu.\n"
        f"{reference_line}\n"
        "Nội dung tiếp theo vẫn thuộc khoản 1.\n"
        "2. Nội dung cuối Điều 1."
    )
    second_article = "Điều 2. Hiệu lực\nNội dung Điều 2."
    text = (
        "Chương I\n"
        "Mục 3. QUY ĐỊNH CHUNG\n"
        f"{first_article}\n"
        f"{second_article}"
    )

    chunks = chunk_legal_text(text)

    assert [c.section_info["dieu"] for c in chunks] == ["1", "2"]
    assert [c.content for c in chunks] == [first_article, second_article]
    assert [c.section_info["muc"] for c in chunks] == ["3", "3"]
    assert all(c.section_info["chuong"] == "I" for c in chunks)


@pytest.mark.parametrize("previous_muc", [None, "3"])
@pytest.mark.parametrize(
    "reference_line",
    [
        "Mục 1 của Phụ lục này được áp dụng.",
        "Mục 1.1 của Phụ lục này được áp dụng.",
        "Mục 2.3",
        "Mục 2.3 của Phụ lục này được áp dụng.",
        "Mục 12.34.5 của Phụ lục này được áp dụng.",
        "  mỤc 2.3 của Phụ lục này được áp dụng.",
        "Mục 2.",
        "Mục 2:",
        "Mục 2.QUY ĐỊNH CHUNG",
        "Mục 2:QUY ĐỊNH CHUNG",
    ],
)
def test_khong_nham_muc_tham_chieu_la_tieu_de(reference_line, previous_muc):
    prefix = f"Mục {previous_muc}\n" if previous_muc is not None else ""
    first_article = (
        "Điều 1. Quy định\n"
        f"{reference_line}\n"
        "Nội dung tiếp theo."
    )
    second_article = "Điều 2. Hiệu lực\nNội dung Điều 2."

    chunks = chunk_legal_text(f"{prefix}{first_article}\n{second_article}")

    assert [c.section_info["dieu"] for c in chunks] == ["1", "2"]
    assert [c.content for c in chunks] == [first_article, second_article]
    assert [c.section_info["muc"] for c in chunks] == [
        previous_muc,
        previous_muc,
    ]


@pytest.mark.parametrize(
    "muc_heading",
    [
        "Mục 2",
        "Mục 2. QUY ĐỊNH RIÊNG",
        "Mục 2: QUY ĐỊNH RIÊNG",
        "Mục 2.\tQUY ĐỊNH RIÊNG",
        "  MỤC 2 : QUY ĐỊNH RIÊNG  ",
    ],
)
def test_tieu_de_muc_that_ket_thuc_dieu_truoc_va_cap_nhat_metadata(muc_heading):
    first_article = "Điều 1. Phạm vi\nNội dung Điều 1."
    second_article = "Điều 2. Thủ tục\nNội dung Điều 2."
    text = (
        "Chương I\n"
        "Mục 1. QUY ĐỊNH CHUNG\n"
        f"{first_article}\n"
        f"{muc_heading}\n"
        "TIÊU ĐỀ MỤC MỚI\n"
        f"{second_article}"
    )

    chunks = chunk_legal_text(text)

    assert [c.section_info["dieu"] for c in chunks] == ["1", "2"]
    assert [c.content for c in chunks] == [first_article, second_article]
    assert [c.section_info["muc"] for c in chunks] == ["1", "2"]
    assert all(c.section_info["chuong"] == "I" for c in chunks)


def test_van_ban_rong():
    assert chunk_legal_text("   \n\n  ") == []


def test_chuan_hoa_unicode_to_hop():
    # "Điều" gõ kiểu tổ hợp (NFD) vẫn nhận ra
    text = unicodedata.normalize("NFD", "Điều 7. Thử\nNội dung.")
    chunks = chunk_legal_text(text)
    assert chunks[0].section_info["dieu"] == "7"
    assert normalize_text("a\r\n\r\n\r\nb") == "a\n\nb"


def test_chu_hoa_va_dau_hai_cham():
    chunks = chunk_legal_text("CHƯƠNG IV\nĐIỀU 12: Thử nghiệm\nNội dung.")
    assert chunks[0].section_info["chuong"] == "IV"
    assert chunks[0].section_info["dieu"] == "12"


def test_embedding_text_them_ten_van_ban(sample_text):
    c = chunk_legal_text(sample_text)[3]
    t = embedding_text("Luật Mẫu", c)
    assert t.startswith("[Văn bản: Luật Mẫu > Chương II > Mục 1 > Điều 4]\nĐiều 4.")


def test_muc_duoc_ke_thua_va_reset_khi_sang_chuong(sample_text):
    by_dieu = {c.section_info["dieu"]: c for c in chunk_legal_text(sample_text)}
    assert by_dieu["1"].section_info["muc"] is None
    assert by_dieu["3"].section_info["muc"] == "1"
    assert by_dieu["4"].section_info["muc"] == "1"
    assert by_dieu["5"].section_info["muc"] == "2"
    assert by_dieu["6"].section_info["muc"] is None
    for limit in (250, 300, 400, 2500):
        chunks = chunk_legal_text(sample_text, max_chars=limit)
        assert all(c.section_info["muc"] == "1" for c in chunks if c.section_info["dieu"] == "4")


def test_embedding_breadcrumb_day_du_va_bo_cap_thieu():
    from app.schemas.search import SectionInfo
    from app.services.chunking import Chunk

    chunk = Chunk("Nội dung.", {"chuong": "II", "muc": "1", "dieu": "4", "khoan": "2", "diem": "a"})
    assert embedding_text("Luật Mẫu", chunk) == (
        "[Văn bản: Luật Mẫu > Chương II > Mục 1 > Điều 4 > Khoản 2 > Điểm a]\nNội dung."
    )
    assert embedding_text("Luật Mẫu", Chunk("Đoạn văn.")) == "[Văn bản: Luật Mẫu]\nĐoạn văn."
    assert embedding_text("Luật Mẫu", Chunk("Điều 7.", {"dieu": "7", "muc": "  "})) == (
        "[Văn bản: Luật Mẫu > Điều 7]\nĐiều 7."
    )
    assert SectionInfo.model_validate(chunk.section_info).model_dump()["muc"] == "1"
