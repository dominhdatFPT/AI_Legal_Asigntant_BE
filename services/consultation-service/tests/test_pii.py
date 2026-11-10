import pytest

from app.services.pii import mask_pii


@pytest.mark.parametrize("raw, masked", [
    ("CCCD của tôi 012345678901 nhé", "CCCD của tôi [CCCD] nhé"),
    ("CMND 123456789 cấp năm 2010", "CMND [CCCD] cấp năm 2010"),
    ("gọi 0912345678 hoặc 091 234 5678 hoặc 091.234.5678", "gọi [SĐT] hoặc [SĐT] hoặc [SĐT]"),
    ("số +84 912 345 678 và 84912345678", "số [SĐT] và [SĐT]"),
    ("mail a.b+c@gmail.com nhé", "mail [EMAIL] nhé"),
    ("STK: 1234 5678 9012 3 Vietcombank", "STK: [STK] Vietcombank"),
    ("số tài khoản là 19031234567890", "số tài khoản là [STK]"),
])
def test_che_thong_tin_ca_nhan(raw, masked):
    assert mask_pii(raw) == masked


@pytest.mark.parametrize("keep", [
    "mua 120m2 thửa số 45 giá 850 triệu",
    "đã trả 800.000.000 đồng",
    "trả 850000000 đồng",
    "ngày 15/3/2024 theo Điều 167 khoản 2",
    "hợp đồng số 123/2019/HĐ",
])
def test_khong_che_so_lieu_phap_ly(keep):
    assert mask_pii(keep) == keep
