"""app/services/cache.py phần Redis: make_key, RedisCache, ResilientCache, make_cache. Redis GIẢ viết ngay trong file (dict có hạn dùng),
thời gian giả lập (monkeypatch time.monotonic), không sleep, không gọi mạng, không dùng thư viện test nào thêm."""
import fnmatch
import logging

import pytest

from app.core.config import get_settings
from app.services import cache
from app.services.cache import RedisCache, ResilientCache, TTLCache, make_cache, make_key

LEAK = "KHONG-LO-RA-LOG"  # đoạn mã giả đóng vai mật khẩu: log tuyệt đối không được chứa
class CommandError(Exception):
    """Lỗi kiểu KHÁC lỗi kết nối (lệnh Redis lỗi): vẫn phải đủ redis_breaker_failures lần liên tiếp mới ngắt mạch."""


URL = "redis://" + "default:" + LEAK + "@" + "redis-test:6379/0"  # URL giả ghép từ nhiều mảnh, không có chuỗi user:mật-khẩu@host nguyên trong file


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


class FakeRedis:
    """Chỉ có các lệnh RedisCache dùng: get, set(ex=), scan_iter, delete. `fail` = lỗi sẽ ném ở mọi lệnh; `delay` = số giây giả lập
    trôi qua trong mỗi lệnh (chậm). `calls` đếm số lệnh được gọi."""

    def __init__(self, clock):
        self.clock = clock
        self.data = {}  # khoá -> (hết hạn lúc, chuỗi)
        self.calls = 0
        self.set_calls = []  # (khoá, chuỗi, ex)
        self.fail = None
        self.delay = 0.0

    def _enter(self):
        self.calls += 1
        self.clock.now += self.delay
        if self.fail is not None:
            raise self.fail

    def _alive(self):
        return {k: v for k, v in self.data.items() if v[0] > self.clock.now}

    def get(self, key):
        self._enter()
        entry = self.data.get(key)
        if entry is None or entry[0] <= self.clock.now:
            return None
        return entry[1]

    def set(self, key, value, ex=None):
        self._enter()
        self.set_calls.append((key, value, ex))
        self.data[key] = (self.clock.now + ex if ex else float("inf"), value)

    def scan_iter(self, match=None, count=None):
        self._enter()
        return iter([k for k in list(self._alive()) if fnmatch.fnmatchcase(k, match or "*")])

    def delete(self, *keys):
        self._enter()
        for k in keys:
            self.data.pop(k, None)


@pytest.fixture
def fake(clock):
    return FakeRedis(clock)


CHUNKS = [
    {"id": "c1", "title": "Điều 45. Quyền của người sử dụng đất", "content": "Người sử dụng đất được tách thửa đất", "score": 0.78},
    {"id": "c2", "title": "Điều 220. Điều kiện chuyển nhượng", "content": "Đất không có tranh chấp", "score": 0.7},
]
ANSWER = {"content": "Theo Điều 45, bạn được tách thửa đất.", "metadata": {"rag_chunk_ids": ["c1"], "model": "m", "tokens_used": 12}}


def make_resilient(fake, *, failures=3, seconds=30.0, ttl=60, max_items=10, namespace="rag"):
    return ResilientCache(RedisCache(namespace, ttl, client=fake), TTLCache(max_items, ttl), failures, seconds)


# ── make_key ──────────────────────────────────────────────────────────────────

def test_cung_khoa_ra_cung_chuoi():
    assert make_key("rag", ("tách thửa đất", 3)) == make_key("rag", ("tách thửa đất", 3))


def test_khac_khoa_ra_khac_chuoi():
    assert make_key("rag", ("tách thửa đất", 3)) != make_key("rag", ("tách thửa đất", 5))
    assert make_key("rag", ("a", 3)) != make_key("rag", ("b", 3))
    assert make_key("rag", ("a", 3)) != make_key("answer", ("a", 3))  # khác vùng
    assert make_key("rag", ("a", 3)) != make_key("rag", ("a", "3"))  # 3 khác "3"


def test_khoa_co_dau_tieng_viet_khac_khong_dau():
    assert make_key("rag", ("đất", 3)) != make_key("rag", ("dat", 3))
    assert make_key("rag", ("tách thửa", 1)) == make_key("rag", ("tách thửa", 1))


def test_khoa_co_tien_to_va_vung_va_khong_chua_cau_hoi():
    k = make_key("rag", ("tách thửa đất cho con", 3))
    assert k.startswith("legal:rag:")
    assert len(k) == len("legal:rag:") + 64  # sha256 hex
    assert "tách" not in k and "thửa" not in k and "con" not in k.split(":", 2)[2]


# ── RedisCache ────────────────────────────────────────────────────────────────

def test_cat_roi_lay_ra_y_het_list_doan_luat(fake):
    c = RedisCache("rag", 60, client=fake)
    c.set(("tách thửa đất", 3), CHUNKS)
    assert c.get(("tách thửa đất", 3)) == CHUNKS


def test_cat_roi_lay_ra_y_het_dict_cau_tra_loi(fake):
    c = RedisCache("answer", 60, client=fake)
    c.set(("tách thửa đất",), ANSWER)
    assert c.get(("tách thửa đất",)) == ANSWER


def test_gia_tri_luu_dang_json_giu_tieng_viet_co_dau(fake):
    c = RedisCache("rag", 60, client=fake)
    c.set(("q", 1), {"x": "Điều 45"})
    assert "Điều 45" in fake.set_calls[0][1]  # ensure_ascii=False: không thành Đ


def test_khong_co_khoa_tra_none(fake):
    assert RedisCache("rag", 60, client=fake).get(("chưa có", 1)) is None


def test_none_khong_duoc_cat(fake):
    c = RedisCache("rag", 60, client=fake)
    c.set(("q", 1), None)
    assert fake.calls == 0 and fake.data == {}


def test_gia_tri_khong_json_duoc_bo_qua_va_khong_nem_loi(fake, caplog):
    c = RedisCache("rag", 60, client=fake)
    with caplog.at_level(logging.WARNING, logger="app.services.cache"):
        c.set(("q", 1), {"x": object()})  # object() không đổi được sang JSON
    assert fake.calls == 0  # không chạm Redis, không tính là lỗi Redis
    assert any("vùng rag" in r.getMessage() for r in caplog.records)
    assert c.get(("q", 1)) is None


def test_gia_tri_khong_json_duoc_khong_tinh_la_loi_redis(fake):
    r = make_resilient(fake, failures=1)
    r.set(("q", 1), {"x": object()})
    r.set(("q", 2), [1, 2])
    assert r.get(("q", 2)) == [1, 2]
    assert fake.set_calls and r._open_until == 0.0  # failures=1 mà chưa ngắt mạch => lần bỏ qua kia không bị tính lỗi


def test_het_han_thi_lay_ra_none(fake, clock):
    c = RedisCache("rag", 60, client=fake)
    c.set(("q", 1), CHUNKS)
    clock.now += 59
    assert c.get(("q", 1)) == CHUNKS
    clock.now += 2
    assert c.get(("q", 1)) is None


def test_set_la_mot_lenh_kem_han_dung(fake):
    c = RedisCache("rag", 86400, client=fake)
    c.set(("q", 1), CHUNKS)
    assert len(fake.set_calls) == 1 and fake.set_calls[0][2] == 86400
    assert fake.calls == 1  # không có EXPIRE riêng


def test_ttl_le_khong_nguyen_duong_toi_thieu_mot_giay(fake):
    c = RedisCache("rag", 0.4, client=fake)
    c.set(("q", 1), [1])
    assert fake.set_calls[0][2] == 1


@pytest.mark.parametrize("ttl", [0, -5])
def test_ttl_khong_duong_thi_khong_luu(fake, ttl):
    c = RedisCache("rag", ttl, client=fake)
    assert c.enabled is False
    c.set(("q", 1), CHUNKS)
    assert c.get(("q", 1)) is None
    assert fake.calls == 0


def test_clear_chi_xoa_dung_vung(fake):
    rag, ans = RedisCache("rag", 60, client=fake), RedisCache("answer", 60, client=fake)
    rag.set(("q", 1), CHUNKS)
    rag.set(("q", 2), CHUNKS)
    ans.set(("q",), ANSWER)
    fake.data["cua-nguoi-khac"] = (float("inf"), "x")
    fake.data["legal:rag2:abc"] = (float("inf"), "x")  # vùng tên gần giống: không được xoá
    rag.clear()
    assert rag.get(("q", 1)) is None and rag.get(("q", 2)) is None
    assert ans.get(("q",)) == ANSWER
    assert "cua-nguoi-khac" in fake.data and "legal:rag2:abc" in fake.data


def test_clear_xoa_het_khi_nhieu_hon_mot_lo(fake, monkeypatch):
    monkeypatch.setattr(cache, "_SCAN_COUNT", 2)
    c = RedisCache("rag", 60, client=fake)
    for i in range(5):
        c.set(("q", i), [i])
    c.clear()
    assert [k for k in fake.data if k.startswith("legal:rag:")] == []


def test_vung_khong_hop_le_bi_tu_choi(fake):
    for bad in ["", "rag:x", "r*g", "rag "]:
        with pytest.raises(ValueError):
            RedisCache(bad, 60, client=fake)


def test_redis_loi_thi_redis_cache_nem_loi_ra_ngoai(fake):
    fake.fail = ConnectionError("hong")
    with pytest.raises(ConnectionError):
        RedisCache("rag", 60, client=fake).get(("q", 1))


def test_muc_hong_khong_phai_json_coi_nhu_khong_co(fake):
    c = RedisCache("rag", 60, client=fake)
    fake.data[make_key("rag", ("q", 1))] = (float("inf"), "{khong phai json")
    assert c.get(("q", 1)) is None


# ── ResilientCache ────────────────────────────────────────────────────────────

def test_redis_tot_thi_chi_noi_chuyen_voi_redis(fake):
    r = make_resilient(fake)
    r.set(("q", 1), CHUNKS)
    assert r.get(("q", 1)) == CHUNKS
    assert len(r._fallback) == 0  # không ghi dự phòng
    fake.data.clear()
    assert r.get(("q", 1)) is None  # không đọc dự phòng


def test_redis_loi_thi_dung_du_phong_va_set_doc_lai_duoc(fake):
    r = make_resilient(fake)
    fake.fail = ConnectionError("hong")
    assert r.get(("q", 1)) is None  # không ném lỗi
    r.set(("q", 1), CHUNKS)
    assert r.get(("q", 1)) == CHUNKS  # đọc lại từ dự phòng


def test_redis_cham_vuot_timeout_cung_dung_du_phong(fake, clock):
    r = make_resilient(fake)
    fake.delay, fake.fail = 0.5, TimeoutError("quá giờ")
    r.set(("q", 1), ANSWER)
    assert r.get(("q", 1)) == ANSWER


def test_du_so_lan_loi_thi_ngat_mach_khong_goi_redis(fake, clock):
    r = make_resilient(fake, failures=3, seconds=30)
    fake.fail = CommandError("loi lenh")
    for i in range(3):
        r.get(("q", i))
    assert fake.calls == 3
    for i in range(10):  # đang ngắt: không chạm Redis
        r.set(("q", i), [i])
        assert r.get(("q", i)) == [i]
    clock.now += 29
    r.get(("q", 0))
    assert fake.calls == 3


def test_chua_du_so_lan_loi_thi_van_thu_redis(fake):
    r = make_resilient(fake, failures=3)
    fake.fail = CommandError("loi lenh")
    r.get(("q", 1))
    r.get(("q", 2))
    assert fake.calls == 2
    r.get(("q", 3))
    assert fake.calls == 3


def test_thanh_cong_dua_so_lan_loi_ve_khong(fake):
    r = make_resilient(fake, failures=3)
    fake.fail = CommandError("loi lenh")
    r.get(("q", 1))
    r.get(("q", 2))
    fake.fail = None
    r.get(("q", 3))  # thành công: đếm lại từ 0
    fake.fail = CommandError("loi lenh")
    r.get(("q", 4))
    r.get(("q", 5))
    n = fake.calls
    r.get(("q", 6))  # mới 3 lỗi tính từ lần thành công => lần này là lỗi thứ ba, vẫn đã gọi Redis
    assert fake.calls == n + 1
    r.get(("q", 7))  # giờ mới ngắt
    assert fake.calls == n + 1


def test_het_thoi_gian_ngat_thi_thu_lai_va_quay_ve_redis(fake, clock):
    r = make_resilient(fake, failures=2, seconds=30)
    fake.fail = CommandError("loi lenh")
    r.get(("q", 1))
    r.get(("q", 2))  # ngắt mạch
    r.set(("q", 9), ["dự phòng"])
    assert fake.calls == 2
    fake.fail = None
    clock.now += 31
    r.set(("q", 10), CHUNKS)  # hết thời gian ngắt: thử lại, thành công
    assert fake.calls == 3
    assert r.get(("q", 10)) == CHUNKS
    assert r.get(("q", 9)) is None  # quay về Redis, không còn đọc dự phòng
    assert fake.calls == 5


def test_het_thoi_gian_ngat_ma_van_loi_thi_ngat_tiep_ngay(fake, clock):
    r = make_resilient(fake, failures=2, seconds=30)
    fake.fail = CommandError("loi lenh")
    r.get(("q", 1))
    r.get(("q", 2))
    clock.now += 31
    r.get(("q", 3))  # thử lại, lỗi
    assert fake.calls == 3
    r.get(("q", 4))  # ngắt ngay, không gọi
    assert fake.calls == 3


def test_khong_co_redis_thi_chi_dung_du_phong(clock):
    r = ResilientCache(None, TTLCache(10, 60))
    r.set(("q", 1), CHUNKS)
    assert r.get(("q", 1)) == CHUNKS
    r.clear()
    assert r.get(("q", 1)) is None


def test_ttl_le_khong_thi_resilient_khong_luu(fake):
    r = make_resilient(fake, ttl=0)
    assert r.enabled is False
    r.set(("q", 1), CHUNKS)
    assert r.get(("q", 1)) is None
    assert fake.calls == 0


def test_clear_xoa_ca_du_phong_va_vung_trong_redis(fake):
    r = make_resilient(fake)
    other = RedisCache("answer", 60, client=fake)
    other.set(("q",), ANSWER)
    r.set(("q", 1), CHUNKS)
    r._fallback.set(("q", 2), [2])
    r.clear()
    assert r.get(("q", 1)) is None and r._fallback.get(("q", 2)) is None
    assert other.get(("q",)) == ANSWER


def test_clear_khi_redis_loi_van_xoa_du_phong(fake):
    r = make_resilient(fake)
    r._fallback.set(("q", 2), [2])
    fake.fail = ConnectionError("hong")
    r.clear()
    assert r._fallback.get(("q", 2)) is None


def test_log_khong_chua_url_hay_gia_tri(fake, caplog):
    r = make_resilient(fake, failures=2)
    fake.fail = ConnectionError(f"khong ket noi duoc {URL}")  # thông điệp lỗi chứa URL: log chỉ được ghi loại lỗi
    with caplog.at_level(logging.DEBUG, logger="app.services.cache"):
        r.set(("câu hỏi riêng tư", 3), {"content": "nội dung bí mật 123"})
        r.get(("câu hỏi riêng tư", 3))
        r.get(("câu hỏi riêng tư", 3))
    text = caplog.text
    assert "ConnectionError" in text and "vùng rag" in text
    for secret in [LEAK, "redis-test", "redis://", "câu hỏi riêng tư", "nội dung bí mật"]:
        assert secret not in text


# ── make_cache ────────────────────────────────────────────────────────────────

def test_redis_url_rong_thi_factory_tra_ttlcache_va_khong_ket_noi(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr(cache, "_get_client", lambda: pytest.fail("không được tạo client Redis khi REDIS_URL rỗng"))
    c = make_cache("rag", 100, 60)
    assert type(c) is TTLCache and c.max_items == 100 and c.ttl_seconds == 60
    c.set(("q", 1), CHUNKS)
    assert c.get(("q", 1)) == CHUNKS


def test_co_redis_url_thi_factory_tra_lop_boc_va_chua_ket_noi_ngay(monkeypatch):
    monkeypatch.setenv("REDIS_URL", URL)
    monkeypatch.setenv("REDIS_BREAKER_FAILURES", "5")
    monkeypatch.setenv("REDIS_BREAKER_SECONDS", "7")
    get_settings.cache_clear()
    monkeypatch.setattr(cache, "_get_client", lambda: pytest.fail("kết nối phải tạo lười, không phải lúc dựng cache"))
    c = make_cache("answer", 50, 3600)
    assert isinstance(c, ResilientCache)
    assert c._primary.namespace == "answer" and c._primary.ttl_seconds == 3600
    assert c._fallback.max_items == 50
    assert c._breaker_failures == 5 and c._breaker_seconds == 7


def test_gia_tri_mac_dinh_cua_ba_bien_moi(monkeypatch):
    for name in ["REDIS_TIMEOUT_SECONDS", "REDIS_BREAKER_FAILURES", "REDIS_BREAKER_SECONDS"]:
        monkeypatch.delenv(name, raising=False)
    s = get_settings()
    assert (s.redis_timeout_seconds, s.redis_breaker_failures, s.redis_breaker_seconds) == (0.2, 3, 30.0)


def test_client_dung_chung_dat_timeout_theo_cau_hinh(monkeypatch):
    monkeypatch.setenv("REDIS_URL", URL)
    monkeypatch.setenv("REDIS_TIMEOUT_SECONDS", "0.15")
    get_settings.cache_clear()
    cache.reset_client()
    try:
        client = cache._get_client()  # tạo lười, chưa mở kết nối
        assert cache._get_client() is client  # dùng chung một client
        kwargs = client.connection_pool.connection_kwargs
        assert kwargs["socket_timeout"] == 0.15 and kwargs["socket_connect_timeout"] == 0.15
        assert kwargs["decode_responses"] is True
    finally:
        cache.reset_client()
