import pytest

from app.services.query_split import MAX_PARTS, split_question


@pytest.mark.parametrize(
    "query, parts",
    [
        # Câu demo: 3 ý (từ nối "rồi", "và"); "cần điều kiện gì" thiếu chủ đề nên ghép vào ý trước;
        # ý 2 không có từ lĩnh vực nên mượn "(thửa đất)" của câu gốc
        (
            "Tôi muốn tách thửa đất rồi tặng cho con một phần, cần điều kiện gì và thời hạn sử dụng đất thế nào?",
            ["Tôi muốn tách thửa đất", "tặng cho con một phần, cần điều kiện gì (thửa đất)", "thời hạn sử dụng đất thế nào"],
        ),
        # Phẩy sau từ để hỏi ("không") là ranh giới giữa 2 câu hỏi
        (
            "tách sổ đỏ cho con được không, đất nông nghiệp được dùng bao nhiêu năm và sổ đỏ ghi sai tên thì sửa thế nào",
            ["tách sổ đỏ cho con được không", "đất nông nghiệp được dùng bao nhiêu năm", "sổ đỏ ghi sai tên thì sửa thế nào"],
        ),
        (
            "mở công ty TNHH cần hồ sơ gì và đặt tên công ty có quy định gì",
            ["mở công ty TNHH cần hồ sơ gì", "đặt tên công ty có quy định gì"],
        ),
        # Gõ không dấu: "so do", "con" không bị coi là hư từ (đã bỏ các từ không dấu dễ nhầm)
        (
            "tach so do cho con duoc khong, dat nong nghiep dung bao lau",
            ["tach so do cho con duoc khong", "dat nong nghiep dung bao lau"],
        ),
    ],
)
def test_tach_cau_nhieu_y(query, parts):
    assert split_question(query) == parts


@pytest.mark.parametrize(
    "query",
    [
        "sang tên sổ đỏ cần những giấy tờ gì",
        # Phẩy kể bối cảnh (không đứng sau từ để hỏi) -> vẫn 1 ý
        "Đất của tôi ở Bình Dương, diện tích 100m2, muốn tách sổ cho con được không?",
        # Lời chào không phải 1 ý
        "Xin chào, cho tôi hỏi tách sổ đỏ cho con được không?",
        # Ý sau chỉ hỏi khía cạnh chung (lệ phí), không có chủ đề riêng
        "Tách sổ được không? Phí bao nhiêu?",
        # "mua" đứng riêng không đủ nội dung
        "mua và bán đất",
        "",
        "???",
    ],
)
def test_cau_mot_y_giu_nguyen(query):
    assert split_question(query) == [query.strip()]


def test_y_thieu_linh_vuc_muon_tu_cau_goc():
    # Lỗi gặp thật 20/10: "tặng cho con một phần" đứng riêng tìm ra Đ.53 Luật Doanh nghiệp (tặng cho vốn góp)
    assert split_question("đổi địa chỉ công ty phải làm gì, rồi tạm nghỉ kinh doanh một thời gian có phải báo không") == [
        "đổi địa chỉ công ty phải làm gì",
        "tạm nghỉ kinh doanh một thời gian có phải báo không (công ty)",
    ]
    # Ý đã có lĩnh vực riêng thì giữ nguyên, kể cả khi khác lĩnh vực với ý kia
    assert split_question("đóng cửa công ty thì làm sao; muốn lập hợp tác xã thì làm thế nào") == [
        "đóng cửa công ty thì làm sao",
        "muốn lập hợp tác xã thì làm thế nào",
    ]
    # "hộ kinh doanh" là 1 cụm, không bị tính thêm "doanh nghiệp"
    assert split_question("mở hộ kinh doanh cần gì và đóng thuế thế nào") == [
        "mở hộ kinh doanh cần gì",
        "đóng thuế thế nào (hộ kinh doanh)",
    ]


def test_qua_nhieu_y_thi_ghep_phan_thua_vao_y_cuoi():
    query = "tách thửa đất; hợp thửa đất; đăng ký biến động đất đai; thế chấp quyền sử dụng đất"
    parts = split_question(query)
    assert len(parts) == MAX_PARTS
    assert parts[-1] == "đăng ký biến động đất đai thế chấp quyền sử dụng đất"
    assert all("đất" in p for p in parts)  # ý nào cũng có lĩnh vực riêng nên không bị thêm ngoặc
