"""Ngắt mạch DÙNG CHUNG (cache.Breaker, cache.shared_breaker): một ngắt mạch cho cả process, mọi vùng cache (rag, history; tên vùng chỉ là nhãn) và việc
đọc số phiên bản kho luật cùng dùng; lỗi kiểu kết nối mở ngay từ lần đầu; hết thời gian chỉ MỘT yêu cầu thử lại. Redis GIẢ + đồng hồ
giả (monkeypatch time.monotonic), không sleep thật, không mạng. Luồng thật chỉ dùng Event để dựng tình huống đồng thời."""
import socket
import threading

import pytest
import redis

from app.core.config import get_settings
from app.services import cache
from app.services.cache import Breaker, is_connection_error, make_cache

VERSION_KEY = "legal:corpus_version"
URL = "redis://redis-test:6379/0"


class CommandError(Exception):
    """Lỗi kiểu KHÁC lỗi kết nối: cần đủ redis_breaker_failures lần liên tiếp mới ngắt mạch."""


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeRedis:
    """get/set/delete/scan_iter. `fail` = lỗi ném ở mọi lệnh; `dns_delay` = số giây giả lập trôi qua TRƯỚC khi ném lỗi (tra tên chậm);
    `gate` = Event: nếu có thì lệnh get chờ trên nó (dựng tình huống một yêu cầu đang thử lại)."""

    def __init__(self, clock):
        self.clock = clock
        self.data = {}
        self.calls = 0
        self.fail = None
        self.dns_delay = 0.0
        self.gate = None
        self.entered = threading.Event()

    def _enter(self):
        self.calls += 1
        if self.fail is not None:
            self.clock.now += self.dns_delay
            raise self.fail

    def get(self, key):
        self._enter()
        if self.gate is not None:
            self.entered.set()
            assert self.gate.wait(10), "gate không được mở"
        entry = self.data.get(key)
        return None if entry is None else entry[1]

    def set(self, key, value, ex=None):
        self._enter()
        self.data[key] = (ex, value)

    def delete(self, *keys):
        self._enter()
        for k in keys:
            self.data.pop(k, None)

    def scan_iter(self, match=None, count=None):
        self._enter()
        return iter([])


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(cache.time, "monotonic", c)
    return c


@pytest.fixture
def fake(monkeypatch, clock):
    f = FakeRedis(clock)
    monkeypatch.setattr(cache, "_get_client", lambda: f)
    cache._warned_backends.clear()
    yield f
    cache._warned_backends.clear()


def configure(monkeypatch, **env):
    monkeypatch.setenv("CACHE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", URL)
    monkeypatch.setenv("CORPUS_VERSION_CACHE_SECONDS", "0")
    for name, value in env.items():
        monkeypatch.setenv(name, str(value))
    get_settings.cache_clear()
    cache.reset_breakers()
    cache.reset_corpus_version()


def three_zones():
    return make_cache("rag", 10, 60), make_cache("answer", 10, 60), make_cache("history", 10, 60)


def touch_everything(zones):
    """Mọi kiểu thao tác của mọi vùng + đọc số phiên bản; trả về giá trị đọc được từ get_corpus_version."""
    for z in zones:
        z.get(("q", 1))
        z.set(("q", 1), [1])
        z.delete(("q", 1))
    return cache.get_corpus_version()


# ── phân loại lỗi ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("error", [
    ConnectionError("x"), ConnectionRefusedError("x"), TimeoutError("x"), socket.gaierror(-2, "Name or service not known"), OSError("x"),
    redis.exceptions.ConnectionError("x"), redis.exceptions.TimeoutError("x"),
])
def test_loi_kieu_ket_noi(error):
    assert is_connection_error(error) is True


@pytest.mark.parametrize("error", [ValueError("x"), RuntimeError("x"), CommandError("x"), redis.exceptions.ResponseError("x"), KeyError("x")])
def test_loi_kieu_khac(error):
    assert is_connection_error(error) is False


# ── mở ngay khi lỗi kết nối, cho mọi vùng ────────────────────────────────────

def test_loi_ket_noi_dau_tien_o_bat_ky_vung_nao_mo_ngat_mach_cho_moi_vung(monkeypatch, fake):
    configure(monkeypatch)
    rag, answer, history = zones = three_zones()
    fake.fail = ConnectionError("không tra được tên")
    rag.get(("q", 1))  # lỗi kết nối đầu tiên, ở vùng rag
    assert fake.calls == 1
    version = touch_everything(zones)
    assert fake.calls == 1  # mọi lệnh sau đó của mọi vùng, kể cả đọc phiên bản, KHÔNG gọi client
    assert version == "0"
    assert answer.get(("q", 1)) is None and history.set(("q", 2), [2]) is False and history.delete(("q", 2)) is False


def test_dung_chung_mot_ngat_mach_theo_redis_url(monkeypatch, fake):
    configure(monkeypatch)
    a, b, c = three_zones()
    assert a._breaker is b._breaker is c._breaker is cache.shared_breaker()
    assert cache.shared_breaker().failures == 3 and cache.shared_breaker().seconds == 30.0


def test_doc_phien_ban_khi_ngat_mach_tra_0_ngay_khong_cho_va_khong_goi(monkeypatch, fake):
    configure(monkeypatch)
    fake.data[VERSION_KEY] = (None, "7")
    assert cache.get_corpus_version() == "7"
    calls = fake.calls
    rag = make_cache("rag", 10, 60)
    fake.fail = ConnectionError("hong")
    rag.get(("q", 1))  # mở ngắt mạch
    assert cache.get_corpus_version() == "0" and fake.calls == calls + 1  # chỉ lần get của vùng rag, không có lần đọc phiên bản


def test_doc_phien_ban_loi_ket_noi_mo_ngat_mach_cho_cac_vung(monkeypatch, fake):
    configure(monkeypatch)
    rag = make_cache("rag", 10, 60)
    fake.fail = ConnectionError("hong")
    assert cache.get_corpus_version() == "0" and fake.calls == 1
    rag.get(("q", 1)); rag.set(("q", 1), [1])
    assert fake.calls == 1


# ── lỗi kiểu khác: vẫn theo luật cũ ──────────────────────────────────────────

def test_loi_kieu_khac_van_can_du_so_lan_lien_tiep_tinh_chung_moi_vung(monkeypatch, fake):
    configure(monkeypatch, REDIS_BREAKER_FAILURES=3)
    rag, answer, history = three_zones()
    fake.fail = CommandError("lỗi lệnh")
    rag.get(("q", 1)); answer.get(("q", 1))
    assert fake.calls == 2  # mới 2 lần lỗi: chưa ngắt
    history.get(("q", 1))
    assert fake.calls == 3  # lần thứ ba (ở vùng khác) mở ngắt mạch chung
    touch_everything((rag, answer, history))
    assert fake.calls == 3


def test_thanh_cong_xen_giua_dua_so_lan_loi_ve_0(monkeypatch, fake):
    configure(monkeypatch, REDIS_BREAKER_FAILURES=3)
    rag, answer, _ = three_zones()
    fake.fail = CommandError("x")
    rag.get(("q", 1)); answer.get(("q", 1))
    fake.fail = None
    rag.get(("q", 2))  # thành công ở vùng khác cũng đưa bộ đếm chung về 0
    fake.fail = CommandError("x")
    rag.get(("q", 3)); answer.get(("q", 3))
    n = fake.calls
    rag.get(("q", 4))
    assert fake.calls == n + 1  # lỗi thứ ba kể từ lần thành công: đã gọi
    rag.get(("q", 5))
    assert fake.calls == n + 1  # giờ mới ngắt


# ── hết thời gian ngắt: đúng một yêu cầu thử lại ──────────────────────────────

def test_het_thoi_gian_ngat_thu_lai_loi_thi_mo_tiep(monkeypatch, fake, clock):
    configure(monkeypatch, REDIS_BREAKER_SECONDS=30)
    zones = three_zones()
    fake.fail = ConnectionError("hong")
    zones[0].get(("q", 1))
    clock.now += 29
    touch_everything(zones)
    assert fake.calls == 1  # chưa hết ngắt
    clock.now += 2  # hết 30 giây
    zones[1].get(("q", 1))  # lần thử lại duy nhất, lỗi
    assert fake.calls == 2
    touch_everything(zones)
    assert fake.calls == 2  # mở tiếp 30 giây nữa
    clock.now += 31
    zones[2].get(("q", 1))
    assert fake.calls == 3


def test_thu_lai_thanh_cong_thi_dong_va_cac_vung_dung_lai_redis(monkeypatch, fake, clock):
    configure(monkeypatch, REDIS_BREAKER_SECONDS=30)
    rag, answer, history = zones = three_zones()
    fake.fail = ConnectionError("hong")
    rag.get(("q", 1))
    fake.fail = None
    clock.now += 31
    answer.set(("q", 9), ["ok"])  # lần thử lại thành công ở vùng khác
    assert fake.calls == 2
    assert rag.get(("q", 9)) is None and history.get(("q", 9)) is None  # mọi vùng nói chuyện với Redis trở lại
    assert fake.calls == 4
    assert cache.get_corpus_version() == "0" and fake.calls == 5
    assert cache.shared_breaker().open_until == 0.0


def test_thu_lai_loi_kieu_khac_cung_mo_tiep_ngay(monkeypatch, fake, clock):
    configure(monkeypatch, REDIS_BREAKER_FAILURES=3, REDIS_BREAKER_SECONDS=30)
    rag, answer, _ = three_zones()
    fake.fail = CommandError("x")
    rag.get(("q", 1)); rag.get(("q", 2)); rag.get(("q", 3))  # đủ 3 lần: mở
    clock.now += 31
    answer.get(("q", 1))  # thử lại, lỗi kiểu khác: vẫn mở tiếp ngay (không cần đủ 3 lần nữa)
    assert fake.calls == 4
    rag.get(("q", 4))
    assert fake.calls == 4


# ── đa luồng ─────────────────────────────────────────────────────────────────

def test_khi_mot_yeu_cau_dang_thu_lai_cac_yeu_cau_dong_thoi_khac_bo_qua_redis(monkeypatch, fake, clock):
    configure(monkeypatch, REDIS_BREAKER_SECONDS=30)
    rag, answer, history = zones = three_zones()
    fake.fail = ConnectionError("hong")
    rag.get(("q", 1))
    fake.fail = None
    clock.now += 31  # hết ngắt mạch
    fake.gate = threading.Event()
    result = {}
    probe = threading.Thread(target=lambda: result.setdefault("v", answer.get(("q", 7))))
    probe.start()
    try:
        assert fake.entered.wait(10), "yêu cầu thử lại không vào được Redis"
        calls_during_probe = fake.calls
        # trong lúc lần thử lại còn chờ Redis: mọi yêu cầu đồng thời khác bỏ qua Redis, không cùng chờ
        assert rag.get(("q", 7)) is None
        assert history.set(("q", 7), [1]) is False
        assert cache.get_corpus_version() == "0"
        assert fake.calls == calls_during_probe
    finally:
        fake.gate.set()
        probe.join(10)
    assert not probe.is_alive()
    fake.gate = None
    n = fake.calls
    rag.get(("q", 8))
    assert fake.calls == n + 1  # lần thử lại thành công đã đóng ngắt mạch


def test_nhieu_luong_cung_luc_chi_mot_luong_thu_lai(monkeypatch, fake, clock):
    configure(monkeypatch, REDIS_BREAKER_SECONDS=30)
    rag = make_cache("rag", 10, 60)
    fake.fail = ConnectionError("hong")
    rag.get(("q", 1))
    clock.now += 31
    fake.gate = threading.Event()
    n0 = fake.calls
    barrier = threading.Barrier(6)

    def worker(i):
        barrier.wait(10)
        rag.get(("q", 100 + i))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    fake.fail = None
    for t in threads:
        t.start()
    try:
        assert fake.entered.wait(10)
        for _ in range(100):
            threading.Event().wait(0.01)  # cho các luồng còn lại thời gian chạy (không phụ thuộc đồng hồ giả)
            if fake.calls - n0 >= 1:
                break
        assert fake.calls - n0 == 1  # đúng MỘT luồng vào Redis, 5 luồng kia bỏ qua
    finally:
        fake.gate.set()
        for t in threads:
            t.join(10)


# ── tra tên chậm ─────────────────────────────────────────────────────────────

def test_tra_ten_cham_chi_yeu_cau_dau_tien_chiu_khoang_cho(monkeypatch, fake, clock):
    """Mô phỏng container redis dừng: tra tên mất 4 giây rồi mới lỗi kết nối. Chỉ yêu cầu ĐẦU TIÊN chịu 4 giây."""
    configure(monkeypatch)
    zones = three_zones()
    fake.fail, fake.dns_delay = ConnectionError("Temporary failure in name resolution"), 4.0
    t0 = clock.now
    # yêu cầu 1: đọc phiên bản (lỗi, mở ngắt mạch) rồi mọi thao tác cache của yêu cầu đó
    cache.get_corpus_version()
    for z in zones:
        z.get(("q", 1)); z.set(("q", 1), [1])
    assert clock.now - t0 == 4.0 and fake.calls == 1
    for _ in range(10):  # các yêu cầu sau: không chờ gì, không gọi client
        t1 = clock.now
        touch_everything(zones)
        assert clock.now == t1
    assert fake.calls == 1


def test_loi_dau_tien_la_doc_phien_ban_hay_get_cache_deu_chi_ton_mot_lan_cho(monkeypatch, fake, clock):
    configure(monkeypatch)
    rag = make_cache("rag", 10, 60)
    fake.fail, fake.dns_delay = ConnectionError("x"), 4.0
    t0 = clock.now
    rag.get(("q", 1))
    cache.get_corpus_version()
    rag.get(("q", 2))
    assert clock.now - t0 == 4.0


# ── Breaker đơn lẻ ───────────────────────────────────────────────────────────

def test_breaker_tren_do_thi_dong_hoan_toan(clock):
    b = Breaker(failures=2, seconds=10)
    assert b.allow()
    b.failure(CommandError("x"))
    assert b.allow() and b.open_until == 0.0
    assert b.failure(CommandError("x")) is True and not b.allow()
    clock.now += 11
    assert b.allow() is True  # lần thử lại
    assert b.allow() is False  # người thứ hai bị chặn khi người đầu chưa báo kết quả
    b.success()
    assert b.allow() and b.allow() and b.fails == 0 and b.open_until == 0.0


def test_lan_thu_lai_khong_bao_ket_qua_thi_sau_thoi_han_cho_nguoi_khac_thu(clock):
    b = Breaker(failures=3, seconds=10)
    b.failure(ConnectionError("x"))
    clock.now += 11
    assert b.allow() is True
    clock.now += 5
    assert b.allow() is False  # người thử đầu vẫn còn trong thời hạn chờ
    clock.now += 6  # quá _PROBE_TIMEOUT (10 giây) kể từ lần thử đầu
    assert b.allow() is True
