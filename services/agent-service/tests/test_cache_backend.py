"""Nối lớp cache Redis vào cache tìm luật (rag_client.search, vùng "rag"), chọn bằng CACHE_BACKEND + REDIS_URL qua cache.make_cache.
Redis GIẢ viết ngay trong file (dict có hạn dùng, ép lỗi được), rag-service giả bằng httpx.MockTransport; không mạng, không sleep,
không thư viện test nào thêm. (Cache lịch sử trò chuyện có test riêng ở test_history_cache.py.)"""
import asyncio
import logging

import httpx
import pytest

from app.core.config import get_settings
from app.services import cache, rag_client

Q = "Sang tên sổ đỏ cần gì?"
Q_AGAIN = "  sang TÊN   sổ đỏ\ncần gì ?! "  # cùng câu sau chuẩn hoá


class FakeRedis:
    """Các lệnh RedisCache dùng: get, set(ex=), scan_iter, delete. `fail` = lỗi ném ở mọi lệnh; `calls` đếm lệnh."""

    def __init__(self):
        self.data = {}
        self.calls = 0
        self.fail = None

    def _enter(self):
        self.calls += 1
        if self.fail is not None:
            raise self.fail

    def get(self, key):
        self._enter()
        entry = self.data.get(key)
        return None if entry is None else entry[1]

    def set(self, key, value, ex=None):
        self._enter()
        self.data[key] = (ex, value)

    def scan_iter(self, match=None, count=None):
        self._enter()
        prefix = (match or "*").rstrip("*")
        return iter([k for k in list(self.data) if k.startswith(prefix)])

    def delete(self, *keys):
        self._enter()
        for k in keys:
            self.data.pop(k, None)


@pytest.fixture
def fake(monkeypatch):
    """Redis giả thay client dùng chung của cache.py (RedisCache không truyền client thì gọi cache._get_client)."""
    f = FakeRedis()
    monkeypatch.setattr(cache, "_get_client", lambda: f)
    cache._warned_backends.clear()
    yield f
    cache._warned_backends.clear()


@pytest.fixture(autouse=True)
def reset_caches():
    rag_client.clear_cache()
    yield
    rag_client.clear_cache()


def configure(monkeypatch, *, backend="redis", url="redis://redis-test:6379/0", rag=False, **env):
    monkeypatch.setenv("CACHE_BACKEND", backend)
    monkeypatch.setenv("REDIS_URL", url)
    monkeypatch.setenv("RAG_CACHE_ENABLED", "true" if rag else "false")
    for name, value in env.items():
        monkeypatch.setenv(name, str(value))
    get_settings.cache_clear()
    rag_client.clear_cache()


# ── Cấu hình ──────────────────────────────────────────────────────────────────

def test_cache_backend_mac_dinh_la_redis_va_hai_co_cache_bat(monkeypatch):
    for name in ["CACHE_BACKEND", "RAG_CACHE_ENABLED", "HISTORY_CACHE_ENABLED"]:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    s = get_settings()
    assert s.cache_backend == "redis"
    assert s.rag_cache_enabled is True and s.history_cache_enabled is True


def test_make_cache_redis_va_co_url_thi_tra_lop_boc(monkeypatch):
    configure(monkeypatch)
    assert isinstance(cache.make_cache("rag", 10, 60), cache.ResilientCache)


def test_make_cache_memory_thi_tra_ttlcache_du_co_redis_url(monkeypatch, fake):
    configure(monkeypatch, backend="memory")
    c = cache.make_cache("rag", 10, 60)
    assert type(c) is cache.TTLCache
    c.set(("q", 1), [1])
    assert c.get(("q", 1)) == [1] and fake.calls == 0


def test_make_cache_redis_nhung_url_rong_thi_tra_ttlcache(monkeypatch, fake):
    configure(monkeypatch, backend="redis", url="")
    c = cache.make_cache("rag", 10, 60)
    assert type(c) is cache.TTLCache and fake.calls == 0


@pytest.mark.parametrize("backend", ["Redis", " REDIS "])
def test_ten_backend_khong_phan_biet_hoa_thuong_va_khoang_trang(monkeypatch, backend):
    configure(monkeypatch, backend=backend)
    assert isinstance(cache.make_cache("rag", 10, 60), cache.ResilientCache)


def test_gia_tri_la_cua_cache_backend_ve_memory_va_canh_bao_mot_lan(monkeypatch, fake, caplog):
    configure(monkeypatch, backend="memcached")
    with caplog.at_level(logging.WARNING, logger="app.services.cache"):
        a = cache.make_cache("rag", 10, 60)
        b = cache.make_cache("history", 10, 60)
    assert type(a) is cache.TTLCache and type(b) is cache.TTLCache
    warnings = [r for r in caplog.records if "CACHE_BACKEND" in r.getMessage()]
    assert len(warnings) == 1
    assert "memcached" not in warnings[0].getMessage() and "redis-test" not in caplog.text


# ── rag_client.search ─────────────────────────────────────────────────────────

class Rag:
    """rag-service giả: đếm số lần được gọi."""

    def __init__(self, chunks):
        self.calls = 0
        self.chunks = chunks

    def __call__(self, request: httpx.Request):
        self.calls += 1
        return httpx.Response(200, json={"data": {"chunks": self.chunks}, "error": None, "request_id": "r"})


def search(rag, query=Q, top_k=3):
    return asyncio.run(rag_client.search(query, top_k=top_k, transport=httpx.MockTransport(rag)))


def test_rag_redis_lan_hai_cung_cau_khong_goi_rag_va_du_lieu_o_redis(monkeypatch, fake, chunk):
    configure(monkeypatch, rag=True)
    rag = Rag([chunk])
    first = search(rag)
    second = search(rag, Q_AGAIN)
    assert rag.calls == 1 and first == second == [chunk]
    keys = list(fake.data)
    assert len(keys) == 1 and keys[0].startswith("legal:rag:")
    assert fake.data[keys[0]][0] == 86400  # hạn dùng lấy từ RAG_CACHE_TTL_SECONDS


def test_rag_redis_sau_khi_mat_doi_tuong_cache_van_trung_tu_redis(monkeypatch, fake, chunk):
    """Mô phỏng restart hoặc một bản agent-service khác: bỏ đối tượng cache trong process, dữ liệu chỉ còn ở Redis."""
    configure(monkeypatch, rag=True)
    rag = Rag([chunk])
    search(rag)
    rag_client.clear_cache()
    again = search(rag, Q_AGAIN)
    assert rag.calls == 1 and again == [chunk]


def test_rag_cung_cau_khac_top_k_la_hai_khoa(monkeypatch, fake, chunk):
    configure(monkeypatch, rag=True)
    rag = Rag([chunk])
    search(rag, top_k=3)
    search(rag, top_k=5)
    assert rag.calls == 2 and len(fake.data) == 2


def test_rag_memory_khong_cham_redis(monkeypatch, fake, chunk):
    configure(monkeypatch, backend="memory", rag=True)
    rag = Rag([chunk])
    search(rag)
    search(rag, Q_AGAIN)
    assert rag.calls == 1 and fake.calls == 0 and fake.data == {}


def test_rag_redis_url_rong_giu_cache_ram_nhu_truoc(monkeypatch, fake, chunk):
    configure(monkeypatch, backend="redis", url="", rag=True)
    rag = Rag([chunk])
    search(rag)
    search(rag, Q_AGAIN)
    assert rag.calls == 1 and fake.calls == 0
    assert type(rag_client._get_cache()) is cache.TTLCache


def test_rag_co_tat_thi_khong_cham_redis(monkeypatch, fake, chunk):
    configure(monkeypatch, rag=False)
    rag = Rag([chunk])
    search(rag)
    search(rag)
    assert rag.calls == 2 and fake.calls == 0


def test_rag_khong_cat_danh_sach_rong(monkeypatch, fake):
    configure(monkeypatch, rag=True)
    rag = Rag([])
    assert search(rag) == []
    assert search(rag) == []
    assert rag.calls == 2 and fake.data == {}


def test_rag_redis_loi_van_tim_luat_binh_thuong_va_dung_du_phong(monkeypatch, fake, chunk):
    configure(monkeypatch, rag=True)
    fake.fail = ConnectionError("hong")
    rag = Rag([chunk])
    first = search(rag)
    second = search(rag, Q_AGAIN)  # đọc từ bản dự phòng trong bộ nhớ
    assert first == second == [chunk] and rag.calls == 1


def test_rag_redis_loi_lien_tuc_thi_ngat_mach_khong_goi_redis_nua(monkeypatch, fake, chunk):
    configure(monkeypatch, rag=True, REDIS_BREAKER_FAILURES=2)
    fake.fail = TimeoutError("quá giờ")
    rag = Rag([chunk])
    search(rag, "câu một")
    search(rag, "câu hai")
    n = fake.calls
    search(rag, "câu ba")
    search(rag, "câu bốn")
    assert fake.calls == n  # đang ngắt mạch: không chạm Redis
    assert rag.calls == 4


def test_rag_khong_ghi_url_hay_noi_dung_vao_log(monkeypatch, fake, chunk, caplog):
    leak = "KHONG-LO-RA-LOG"  # đoạn mã giả đóng vai mật khẩu
    url = "redis://" + "default:" + leak + "@" + "redis-test:6379/0"
    configure(monkeypatch, rag=True, REDIS_URL=url)
    fake.fail = ConnectionError("khong ket noi duoc " + url)
    with caplog.at_level(logging.DEBUG):
        search(Rag([chunk]))
    assert leak not in caplog.text and "redis-test" not in caplog.text
    assert chunk["content"] not in caplog.text
