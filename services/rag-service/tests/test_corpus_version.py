"""app/services/corpus_version.py (tăng số phiên bản kho luật trong Redis) và các chỗ gọi nó: duyệt, thay nội dung, nạp văn bản.
Redis GIẢ thay redis.Redis.from_url, không mạng, không sleep, không thư viện test nào thêm."""
import logging
import uuid

import pytest
import redis
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import LegalDocument
from app.services import corpus_version
from app.services import document_approval as approval
from app.services import document_replace as replace
from app.services.embedding import EmbeddingError, FakeEmbedder
from app.services.ingest import ingest_document, reembed_document

LEAK = "KHONG-LO-RA-LOG"  # đoạn mã giả đóng vai mật khẩu: log không được chứa
URL = "redis://" + "default:" + LEAK + "@" + "redis-test:6379/0"


class FakeClient:
    def __init__(self, fail=None):
        self.fail = fail
        self.incr_calls = []
        self.closed = False

    def incr(self, key):
        self.incr_calls.append(key)
        if self.fail:
            raise self.fail
        return len(self.incr_calls)

    def close(self):
        self.closed = True


@pytest.fixture
def make_redis(monkeypatch):
    """Thay redis.Redis.from_url: trả FakeClient, ghi lại đối số gọi. Trả (client, from_url_calls)."""
    state = {"client": FakeClient(), "calls": []}

    def from_url(url, **kwargs):
        state["calls"].append((url, kwargs))
        if isinstance(state["client"], Exception):
            raise state["client"]
        return state["client"]

    monkeypatch.setattr(redis.Redis, "from_url", from_url)
    return state


def use_url(monkeypatch, url=URL, **env):
    monkeypatch.setenv("REDIS_URL", url)
    for name, value in env.items():
        monkeypatch.setenv(name, str(value))
    get_settings.cache_clear()


# ── cấu hình ──────────────────────────────────────────────────────────────────

def test_gia_tri_mac_dinh_cua_hai_truong_moi(monkeypatch):
    monkeypatch.delenv("REDIS_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("CORPUS_VERSION_KEY", raising=False)
    get_settings.cache_clear()
    s = get_settings()
    assert s.redis_timeout_seconds == 0.2 and s.corpus_version_key == "legal:corpus_version"


# ── bump_corpus_version ───────────────────────────────────────────────────────

def test_redis_url_rong_khong_lam_gi(monkeypatch, make_redis):
    monkeypatch.setenv("REDIS_URL", "")
    get_settings.cache_clear()
    assert corpus_version.bump_corpus_version() is None
    assert make_redis["calls"] == []


def test_tang_dung_mot_lan_voi_dung_ten_khoa_cau_hinh(monkeypatch, make_redis):
    use_url(monkeypatch, CORPUS_VERSION_KEY="khoa:rieng")
    assert corpus_version.bump_corpus_version() == 1
    assert make_redis["client"].incr_calls == ["khoa:rieng"]
    assert corpus_version.bump_corpus_version() == 2
    assert make_redis["client"].incr_calls == ["khoa:rieng", "khoa:rieng"]


def test_ten_khoa_mac_dinh(monkeypatch, make_redis):
    monkeypatch.delenv("CORPUS_VERSION_KEY", raising=False)
    use_url(monkeypatch)
    corpus_version.bump_corpus_version()
    assert make_redis["client"].incr_calls == ["legal:corpus_version"]


def test_client_co_timeout_ngan_retry_khong_va_duoc_dong(monkeypatch, make_redis):
    use_url(monkeypatch, REDIS_TIMEOUT_SECONDS="0.15")
    corpus_version.bump_corpus_version()
    (url, kwargs), = make_redis["calls"]
    assert url == URL
    assert kwargs["socket_timeout"] == 0.15 and kwargs["socket_connect_timeout"] == 0.15
    assert kwargs["decode_responses"] is True
    assert kwargs["retry"]._retries == 0
    assert make_redis["client"].closed is True


def test_loi_redis_duoc_bat_tra_none_va_canh_bao_khong_lo_url(monkeypatch, make_redis, caplog):
    use_url(monkeypatch)
    make_redis["client"] = FakeClient(fail=ConnectionError("khong ket noi duoc " + URL))
    with caplog.at_level(logging.DEBUG):
        assert corpus_version.bump_corpus_version() is None
    assert "ConnectionError" in caplog.text and "TTL" in caplog.text
    assert LEAK not in caplog.text and "redis-test" not in caplog.text


def test_loi_khi_tao_client_cung_duoc_bat(monkeypatch, make_redis):
    use_url(monkeypatch)
    make_redis["client"] = ValueError("URL hỏng")
    assert corpus_version.bump_corpus_version() is None


def test_het_gio_duoc_bat(monkeypatch, make_redis):
    use_url(monkeypatch)
    make_redis["client"] = FakeClient(fail=redis.exceptions.TimeoutError("quá giờ"))
    assert corpus_version.bump_corpus_version() is None


# ── các chỗ gọi ───────────────────────────────────────────────────────────────

@pytest.fixture
def bumps(monkeypatch):
    seen = []
    monkeypatch.setattr(corpus_version, "bump_corpus_version", lambda: seen.append(1))
    return seen


@pytest.fixture
def opened(db, monkeypatch):
    monkeypatch.setattr(approval, "_open_session", lambda: Session(db.get_bind(), expire_on_commit=False))
    monkeypatch.setattr(approval, "get_embedder", lambda: FakeEmbedder(768))
    monkeypatch.setattr(replace, "get_embedder", lambda: FakeEmbedder(768))


CONTENT = "Điều 1. A\nNội dung một.\nĐiều 2. B\nNội dung hai."


def add_doc(db, ingest_status="processing", status="draft", content=CONTENT, **kw) -> uuid.UUID:
    doc = LegalDocument(title="Luật A", doc_type="luat", content=content, status=status, ingest_status=ingest_status, **kw)
    db.add(doc)
    db.commit()
    return doc.id


def test_duyet_thanh_cong_tang_dung_mot_lan(db, opened, bumps):
    approval.approve_document_background(add_doc(db))
    assert bumps == [1]


def test_duyet_that_bai_khong_tang(db, opened, bumps, monkeypatch):
    def boom():
        raise RuntimeError("hỏng")

    monkeypatch.setattr(approval, "get_embedder", boom)
    approval.approve_document_background(add_doc(db))
    assert bumps == []


def test_duyet_van_ban_rong_khong_tang(db, opened, bumps):
    approval.approve_document_background(add_doc(db, content="   "))
    assert bumps == []


def test_duyet_khong_tim_thay_van_ban_khong_tang(db, opened, bumps):
    approval.approve_document_background(uuid.uuid4())
    assert bumps == []


def test_tang_chi_sau_khi_da_commit(db, opened, monkeypatch):
    """Lúc gọi bump, văn bản đã là active/done trong DB (đã commit)."""
    document_id = add_doc(db)
    seen = {}

    def spy():
        db.expire_all()
        d = db.get(LegalDocument, document_id)
        seen["state"] = (d.status, d.ingest_status)

    monkeypatch.setattr(corpus_version, "bump_corpus_version", spy)
    approval.approve_document_background(document_id)
    assert seen["state"] == ("active", "done")


def test_duyet_van_chay_khi_redis_loi_that(db, opened, monkeypatch, make_redis):
    """Không giả bump: Redis lỗi thật (FakeClient ném lỗi) mà duyệt vẫn thành công."""
    use_url(monkeypatch)
    make_redis["client"] = FakeClient(fail=ConnectionError("hong"))
    document_id = add_doc(db)
    approval.approve_document_background(document_id)  # không ném
    db.expire_all()
    d = db.get(LegalDocument, document_id)
    assert (d.status, d.ingest_status) == ("active", "done")
    assert make_redis["client"].incr_calls == ["legal:corpus_version"]


OLD = "Điều 1. Phạm vi\nNội dung điều một.\nĐiều 2. Đối tượng\nNội dung điều hai."
NEW = OLD.replace("Nội dung điều hai.", "Nội dung điều hai đã sửa.")


def add_active(db) -> uuid.UUID:
    doc = ingest_document(db, FakeEmbedder(768), title="Luật Mẫu", doc_type="luat", content=OLD, doc_number="1/2020/QH")
    return doc.id


def claim(db, document_id):
    doc = db.get(LegalDocument, document_id)
    doc.ingest_status = "processing"
    db.commit()


def test_thay_thanh_cong_tang_dung_mot_lan(db, opened, monkeypatch):
    document_id = add_active(db)
    claim(db, document_id)
    seen = []
    monkeypatch.setattr(corpus_version, "bump_corpus_version", lambda: seen.append(1))
    replace.replace_document_background(document_id, NEW, "admin-1", "admin")
    assert seen == [1]


def test_thay_that_bai_khong_tang(db, opened, monkeypatch):
    document_id = add_active(db)
    claim(db, document_id)
    seen = []
    monkeypatch.setattr(corpus_version, "bump_corpus_version", lambda: seen.append(1))

    class Boom(FakeEmbedder):
        def embed_documents(self, texts):
            raise EmbeddingError("hết quota")

    monkeypatch.setattr(replace, "get_embedder", lambda: Boom(768))
    replace.replace_document_background(document_id, NEW)
    assert seen == []


def test_nap_van_ban_active_tang_ban_nhap_khong_tang(db, bumps):
    ingest_document(db, FakeEmbedder(768), title="Luật A", doc_type="luat", content=OLD, doc_number="1/2020/QH")
    assert bumps == [1]
    ingest_document(db, FakeEmbedder(768), title="Luật B", doc_type="luat", content=OLD, doc_number="2/2020/QH", status="draft")
    assert bumps == [1]


def test_nap_that_bai_khong_tang(db, bumps):
    class Boom(FakeEmbedder):
        def embed_documents(self, texts):
            raise EmbeddingError("hết quota")

    with pytest.raises(EmbeddingError):
        ingest_document(db, Boom(768), title="Luật A", doc_type="luat", content=OLD, doc_number="1/2020/QH")
    assert bumps == []


def test_nap_lai_embedding_tang_mot_lan(db, bumps):
    doc = ingest_document(db, FakeEmbedder(768), title="Luật A", doc_type="luat", content=OLD, doc_number="1/2020/QH")
    bumps.clear()
    reembed_document(db, FakeEmbedder(768), doc)
    assert bumps == [1]
