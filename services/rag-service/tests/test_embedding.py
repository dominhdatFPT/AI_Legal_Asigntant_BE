import math

import pytest
from google.genai import errors

from app.services.embedding import EmbeddingError, FakeEmbedder, GeminiEmbedder, get_embedder


def _cos(a, b):
    return sum(x * y for x, y in zip(a, b))


class _Emb:
    def __init__(self, values):
        self.values = values


class _Resp:
    def __init__(self, n, dim):
        self.embeddings = [_Emb([3.0] + [0.0] * (dim - 1)) for _ in range(n)]


class FakeModels:
    """Giả lập client.models của google-genai. `fail` là danh sách mã lỗi trả trước khi thành công."""

    def __init__(self, dim=768, fail=(), wrong_dim=False):
        self.calls = []
        self.dim = dim
        self.fail = list(fail)
        self.wrong_dim = wrong_dim

    def embed_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": list(contents), "config": config})
        if self.fail:
            code = self.fail.pop(0)
            raise errors.APIError(code, {"error": {"message": "lỗi giả", "status": "X"}})
        return _Resp(len(contents), self.dim - 1 if self.wrong_dim else self.dim)


class FakeClient:
    def __init__(self, **kw):
        self.models = FakeModels(**kw)


def _embedder(**kw):
    client = FakeClient(**kw)
    sleeps = []
    e = GeminiEmbedder(api_key="", client=client, batch_size=2, sleep=sleeps.append)
    return e, client.models, sleeps


def test_gemini_chia_batch_va_dung_task_type_document():
    e, models, _ = _embedder()
    vecs = e.embed_documents(["a", "b", "c", "d", "e"])
    assert len(vecs) == 5
    assert [len(c["contents"]) for c in models.calls] == [2, 2, 1]
    cfg = models.calls[0]["config"]
    assert cfg.task_type == "RETRIEVAL_DOCUMENT"
    assert cfg.output_dimensionality == 768
    assert models.calls[0]["model"] == "gemini-embedding-001"


def test_gemini_query_dung_task_type_query_va_chuan_hoa():
    e, models, _ = _embedder()
    v = e.embed_query("sổ đỏ")
    assert models.calls[0]["config"].task_type == "RETRIEVAL_QUERY"
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0)
    assert v[0] == pytest.approx(1.0)  # [3, 0, 0...] chuẩn hóa thành [1, 0, 0...]


def test_gemini_thu_lai_khi_429_roi_thanh_cong():
    e, models, sleeps = _embedder(fail=[429, 503])
    assert len(e.embed_documents(["a"])) == 1
    assert len(models.calls) == 3
    assert sleeps == [5, 15]


def test_gemini_query_het_luot_thu_thi_bao_loi():
    e, models, sleeps = _embedder(fail=[429, 429, 429])
    with pytest.raises(EmbeddingError):
        e.embed_query("x")
    assert sleeps == [1, 2]


def test_gemini_loi_400_khong_thu_lai():
    e, models, sleeps = _embedder(fail=[400])
    with pytest.raises(EmbeddingError):
        e.embed_query("x")
    assert len(models.calls) == 1 and sleeps == []


def test_gemini_sai_so_chieu_bao_loi():
    e, _, _ = _embedder(wrong_dim=True)
    with pytest.raises(EmbeddingError, match="chiều"):
        e.embed_query("x")


def test_gemini_khong_co_key_bao_loi():
    with pytest.raises(EmbeddingError, match="GEMINI_API_KEY"):
        GeminiEmbedder(api_key="")


def test_fake_embedder_on_dinh_chuan_hoa_va_cau_giong_thi_gan():
    f = FakeEmbedder(768)
    a = f.embed_query("điều kiện chuyển nhượng quyền sử dụng đất")
    assert a == f.embed_query("điều kiện chuyển nhượng quyền sử dụng đất")
    assert len(a) == 768 and math.isclose(math.sqrt(sum(x * x for x in a)), 1.0)
    gan = f.embed_documents(["Điều kiện chuyển nhượng quyền sử dụng đất gồm có giấy chứng nhận"])[0]
    xa = f.embed_documents(["Luật này có hiệu lực thi hành từ năm 2099"])[0]
    assert _cos(a, gan) > _cos(a, xa)


def test_get_embedder_theo_cau_hinh(monkeypatch):
    assert isinstance(get_embedder(), FakeEmbedder)  # conftest đặt EMBEDDING_PROVIDER=fake


def test_get_embedder_sai_so_chieu(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("EMBEDDING_DIM", "1536")
    get_settings.cache_clear()
    get_embedder.cache_clear()
    with pytest.raises(EmbeddingError, match="không khớp"):
        get_embedder()


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _throttled(limit, clock):
    client = FakeClient()
    sleeps = []

    def sleep(s):
        sleeps.append(round(s, 1))
        clock.t += s

    e = GeminiEmbedder(api_key="", client=client, batch_size=1, sleep=sleep,
                       tokens_per_minute=limit, clock=clock)
    return e, sleeps


def test_giam_toc_khi_gan_cham_gioi_han_token_moi_phut():
    clock = _Clock()
    e, sleeps = _throttled(limit=100, clock=clock)
    text = "x" * 150  # ~51 token mỗi đoạn
    e.embed_documents([text, text])   # 51 + 51 > 100 → đoạn 2 phải chờ
    assert len(sleeps) == 1 and 59 <= sleeps[0] <= 61


def test_khong_giam_toc_khi_con_du_quota():
    clock = _Clock()
    e, sleeps = _throttled(limit=1000, clock=clock)
    e.embed_documents(["x" * 150] * 5)
    assert sleeps == []


def test_sau_mot_phut_thi_quota_hoi_lai():
    clock = _Clock()
    e, sleeps = _throttled(limit=100, clock=clock)
    e.embed_documents(["x" * 150])
    clock.t += 61
    e.embed_documents(["x" * 150])
    assert sleeps == []


def test_tat_giam_toc_khi_gioi_han_bang_0():
    clock = _Clock()
    e, sleeps = _throttled(limit=0, clock=clock)
    e.embed_documents(["x" * 3000] * 3)
    assert sleeps == []
