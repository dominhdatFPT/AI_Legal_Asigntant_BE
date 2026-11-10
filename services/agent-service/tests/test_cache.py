"""app/services/cache.py: normalize và TTLCache. Thời gian giả lập (monkeypatch time.monotonic), không sleep, không gọi mạng."""
import pytest

from app.services import cache
from app.services.cache import TTLCache, normalize


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(cache.time, "monotonic", fake)
    return fake


# ── normalize ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("Điều Kiện TÁCH Thửa", "điều kiện tách thửa"),  # hoa/thường
    ("tách   thửa \t đất", "tách thửa đất"),  # nhiều khoảng trắng, tab
    ("tách thửa\nđất\r\n\r\ncho con", "tách thửa đất cho con"),  # xuống dòng
    ("   tách thửa đất   ", "tách thửa đất"),  # đầu cuối
    ("tách thửa đất?", "tách thửa đất"),  # dấu ? cuối
    ("tách thửa đất?!", "tách thửa đất"),  # "?!" cuối
    ("tách thửa đất ?  ", "tách thửa đất"),  # khoảng trắng trước và sau dấu
    ("tách thửa đất...", "tách thửa đất"),
    ("tách thửa đất…", "tách thửa đất"),
    ("Tách thửa đất. ", "tách thửa đất"),
    ("tách thửa, đất? cho con", "tách thửa, đất? cho con"),  # dấu ở giữa câu giữ nguyên
    ("Nghị định 101/2024/NĐ-CP.", "nghị định 101/2024/nđ-cp"),
    ("đất đai", "đất đai"),  # giữ dấu tiếng Việt
    ("", ""),
    ("   ?!  ", ""),
])
def test_normalize(text, expected):
    assert normalize(text) == expected


def test_normalize_khong_bo_dau_tieng_viet():
    assert normalize("Đất") == "đất"
    assert normalize("đất") != normalize("dat")


def test_normalize_hai_cau_cung_y_cung_khoa():
    assert normalize("Sang tên sổ đỏ cần giấy tờ gì?") == normalize("  sang  tên sổ đỏ\ncần giấy tờ gì ")


# ── TTLCache ───────────────────────────────────────────────────────────────────

def test_get_set_co_ban(clock):
    c = TTLCache(max_items=10, ttl_seconds=60)
    assert c.get("a") is None
    assert len(c) == 0
    c.set("a", {"x": 1})
    assert c.get("a") == {"x": 1}
    assert len(c) == 1
    c.set("a", {"x": 2})  # ghi đè cùng khoá
    assert c.get("a") == {"x": 2}
    assert len(c) == 1


def test_clear(clock):
    c = TTLCache(10, 60)
    c.set("a", 1)
    c.set("b", 2)
    c.clear()
    assert len(c) == 0 and c.get("a") is None


def test_het_han_thi_xoa_va_tra_none(clock):
    c = TTLCache(10, 60)
    c.set("a", "giá trị")
    clock.now += 59.9
    assert c.get("a") == "giá trị"
    clock.now += 0.1  # đúng 60 giây sau lúc set
    assert c.get("a") is None
    assert len(c) == 0  # mục hết hạn bị dọn khi get


def test_get_khong_gia_han_them(clock):
    c = TTLCache(10, 60)
    c.set("a", 1)
    clock.now += 40
    assert c.get("a") == 1
    clock.now += 21  # tổng 61 giây từ lúc set, dù vừa get ở giây 40
    assert c.get("a") is None


def test_set_lai_tinh_lai_han(clock):
    c = TTLCache(10, 60)
    c.set("a", 1)
    clock.now += 50
    c.set("a", 2)
    clock.now += 50
    assert c.get("a") == 2


def test_day_thi_bo_muc_dung_lau_nhat(clock):
    c = TTLCache(max_items=2, ttl_seconds=60)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)  # đầy: bỏ "a" (cũ nhất)
    assert (c.get("a"), c.get("b"), c.get("c")) == (None, 2, 3)
    assert len(c) == 2


def test_muc_vua_get_khong_bi_bo(clock):
    c = TTLCache(max_items=2, ttl_seconds=60)
    c.set("a", 1)
    c.set("b", 2)
    assert c.get("a") == 1  # "a" thành mới dùng gần nhất, "b" thành cũ nhất
    c.set("c", 3)
    assert (c.get("a"), c.get("b"), c.get("c")) == (1, None, 3)


def test_ghi_de_muc_cu_dua_len_cuoi(clock):
    c = TTLCache(max_items=2, ttl_seconds=60)
    c.set("a", 1)
    c.set("b", 2)
    c.set("a", 10)  # "a" mới nhất, "b" cũ nhất
    c.set("c", 3)
    assert (c.get("a"), c.get("b"), c.get("c")) == (10, None, 3)


def test_sua_gia_tri_lay_ra_khong_lam_doi_cache(clock):
    c = TTLCache(10, 60)
    c.set("a", {"ids": [1, 2], "nested": {"k": ["x"]}})
    got = c.get("a")
    got["ids"].append(3)
    got["nested"]["k"].append("y")
    got["moi"] = True
    assert c.get("a") == {"ids": [1, 2], "nested": {"k": ["x"]}}


def test_sua_gia_tri_goc_sau_khi_set_khong_lam_doi_cache(clock):
    c = TTLCache(10, 60)
    original = [{"id": 1}]
    c.set("a", original)
    original.append({"id": 2})
    original[0]["id"] = 99
    assert c.get("a") == [{"id": 1}]


@pytest.mark.parametrize("max_items, ttl", [(0, 60), (-1, 60), (10, 0), (10, -5), (0, 0)])
def test_tat_khi_max_items_hoac_ttl_khong_duong(clock, max_items, ttl):
    c = TTLCache(max_items, ttl)
    c.set("a", 1)
    assert c.get("a") is None
    assert len(c) == 0
    assert c.enabled is False


def test_bat_khi_ca_hai_duong(clock):
    assert TTLCache(1, 1).enabled is True


def test_khoa_tuple_dung_duoc(clock):
    c = TTLCache(10, 60)
    c.set(("tìm", "tách thửa đất", 4), ["đoạn 1"])
    assert c.get(("tìm", "tách thửa đất", 4)) == ["đoạn 1"]
    assert c.get(("tìm", "tách thửa đất", 5)) is None
