"""Số phiên bản kho luật (cache.get_corpus_version) và việc nó nằm trong khoá của cache tìm luật.
Redis GIẢ viết ngay trong file; rag-service giả bằng httpx.MockTransport; thời gian giả lập; không mạng, không sleep, không thư viện thêm."""
import asyncio
import logging

import httpx
import pytest

from app.core.config import get_settings
from app.services import cache, rag_client

VERSION_KEY = "legal:corpus_version"
Q = "Sang tên sổ đỏ cần gì?"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeRedis:
    """get/set(ex=)/scan_iter/delete cho RedisCache và get cho số phiên bản. `fail` = lỗi ném ở mọi lệnh; `calls` đếm lệnh;
    `version_reads` đếm riêng lần đọc khoá phiên bản."""

    def __init__(self):
        self.data = {}
        self.calls = 0
        self.version_reads = 0
        self.fail = None

    def _enter(self):
        self.calls += 1
        if self.fail is not None:
            raise self.fail

    def get(self, key):
        self._enter()
        if key == VERSION_KEY:
            self.version_reads += 1
        entry = self.data.get(key)
        return None if entry is None else entry[1]

    def set(self, key, value, ex=None):
        self._enter()
        self.data[key] = (ex, value)

    def incr(self, key):
        n = int(self.data.get(key, (None, "0"))[1]) + 1
        self.data[key] = (None, str(n))
        return n

    def scan_iter(self, match=None, count=None):
        self._enter()
        prefix = (match or "*").rstrip("*")
        return iter([k for k in list(self.data) if k.startswith(prefix)])

    def delete(self, *keys):
        self._enter()
        for k in keys:
            self.data.pop(k, None)


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(cache.time, "monotonic", fake)
    return fake


@pytest.fixture
def fake(monkeypatch, clock):
    f = FakeRedis()
    monkeypatch.setattr(cache, "_get_client", lambda: f)
    cache._warned_backends.clear()
    cache.reset_corpus_version()
    yield f
    cache._warned_backends.clear()
    cache.reset_corpus_version()


def configure(monkeypatch, *, backend="redis", url="redis://redis-test:6379/0", rag=True, **env):
    monkeypatch.setenv("CACHE_BACKEND", backend)
    monkeypatch.setenv("REDIS_URL", url)
    monkeypatch.setenv("RAG_CACHE_ENABLED", "true" if rag else "false")
    monkeypatch.setenv("CORPUS_VERSION_CACHE_SECONDS", "0")  # mặc định các test không nhớ số phiên bản
    for name, value in env.items():
        monkeypatch.setenv(name, str(value))
    get_settings.cache_clear()
    rag_client.clear_cache()
    cache.reset_corpus_version()


def set_version(fake, n):
    fake.data[VERSION_KEY] = (None, str(n))


# ── get_corpus_version ────────────────────────────────────────────────────────

def test_khoa_chua_co_thi_phien_ban_la_0(monkeypatch, fake):
    configure(monkeypatch)
    assert cache.get_corpus_version() == "0"
    assert fake.version_reads == 1


def test_doc_dung_so_trong_redis_va_dung_ten_khoa_cau_hinh(monkeypatch, fake):
    configure(monkeypatch, CORPUS_VERSION_KEY="khoa:rieng")
    fake.data["khoa:rieng"] = (None, "7")
    fake.data[VERSION_KEY] = (None, "99")
    assert cache.get_corpus_version() == "7"


def test_ten_khoa_mac_dinh_giong_rag_service(monkeypatch):
    monkeypatch.delenv("CORPUS_VERSION_KEY", raising=False)
    get_settings.cache_clear()
    assert get_settings().corpus_version_key == "legal:corpus_version"
    assert get_settings().corpus_version_cache_seconds == 5


def test_redis_loi_thi_phien_ban_la_0_khong_nem_loi(monkeypatch, fake):
    configure(monkeypatch)
    fake.fail = ConnectionError("hong")
    assert cache.get_corpus_version() == "0"


def test_gia_tri_khong_phai_so_thi_la_0(monkeypatch, fake):
    configure(monkeypatch)
    fake.data[VERSION_KEY] = (None, "abc")
    assert cache.get_corpus_version() == "0"


def test_memory_hoac_url_rong_thi_la_0_va_khong_cham_redis(monkeypatch, fake):
    configure(monkeypatch, backend="memory")
    set_version(fake, 5)
    assert cache.get_corpus_version() == "0"
    configure(monkeypatch, backend="redis", url="")
    assert cache.get_corpus_version() == "0"
    assert fake.calls == 0


def test_canh_bao_loi_doc_phien_ban_toi_da_mot_lan_moi_30_giay_va_khong_lo_url(monkeypatch, fake, clock, caplog):
    url = "redis://" + "default:" + "KHONG-LO-RA-LOG" + "@" + "redis-test:6379/0"
    configure(monkeypatch, url=url)
    fake.fail = ConnectionError("khong ket noi duoc " + url)
    with caplog.at_level(logging.DEBUG, logger="app.services.cache"):
        for _ in range(5):
            cache.get_corpus_version()
            clock.now += 1
        assert len([r for r in caplog.records if "số phiên bản" in r.getMessage()]) == 1
        clock.now += 30
        cache.get_corpus_version()
        assert len([r for r in caplog.records if "số phiên bản" in r.getMessage()]) == 2
    assert "KHONG-LO-RA-LOG" not in caplog.text and "redis-test" not in caplog.text


def test_bo_nho_dem_vai_giay_doc_redis_dung_so_lan(monkeypatch, fake, clock):
    configure(monkeypatch, CORPUS_VERSION_CACHE_SECONDS=5)
    set_version(fake, 1)
    assert cache.get_corpus_version() == "1"
    set_version(fake, 2)  # rag-service vừa đổi luật
    clock.now += 4.9
    assert cache.get_corpus_version() == "1" and fake.version_reads == 1  # còn trong thời gian nhớ
    clock.now += 0.2
    assert cache.get_corpus_version() == "2" and fake.version_reads == 2  # hết nhớ: đọc lại


def test_dat_0_thi_khong_nho(monkeypatch, fake, clock):
    configure(monkeypatch, CORPUS_VERSION_CACHE_SECONDS=0)
    set_version(fake, 1)
    assert cache.get_corpus_version() == "1"
    set_version(fake, 2)
    assert cache.get_corpus_version() == "2" and fake.version_reads == 2


# ── khoá cache tìm luật ───────────────────────────────────────────────────────

class Rag:
    def __init__(self, chunks):
        self.calls = 0
        self.chunks = chunks

    def __call__(self, request: httpx.Request):
        self.calls += 1
        return httpx.Response(200, json={"data": {"chunks": self.chunks}, "error": None, "request_id": "r"})


def search(rag, query=Q, top_k=3):
    return asyncio.run(rag_client.search(query, top_k=top_k, transport=httpx.MockTransport(rag)))


def test_tim_luat_doi_phien_ban_thi_truot_cache_phien_ban_cu_thi_trung(monkeypatch, fake, chunk):
    configure(monkeypatch)
    set_version(fake, 1)
    rag = Rag([chunk])
    search(rag)
    search(rag)
    assert rag.calls == 1  # phiên bản 1: trúng
    set_version(fake, 2)  # rag-service duyệt/thay luật xong
    search(rag)
    assert rag.calls == 2  # phiên bản mới: trượt, gọi rag lại
    search(rag)
    assert rag.calls == 2  # cất dưới khoá mới nên lần sau trúng
    set_version(fake, 1)  # về phiên bản cũ: mục cũ vẫn còn trong Redis tới hết TTL nên trúng
    search(rag)
    assert rag.calls == 2
    legal = sorted(k for k in fake.data if k.startswith("legal:rag:"))
    assert len(legal) == 2  # hai khoá: phiên bản 1 và phiên bản 2
