"""Phase 1: ngân sách embedding online, tra cứu chính xác (Điều/Khoản/Điểm/tên Điều), Khoản liền kề, dẫn chiếu chéo.

Dùng SQLite trong RAM (fixture `db`): BM25, exact, liền kề, dẫn chiếu chạy thật trên kho giả; chỉ semantic_search (pgvector)
và nhà cung cấp embedding bị thay bằng bản giả có đếm lần gọi.
"""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.api.v1.internal import embedder_dep
from app.core.config import get_settings
from app.core.database import get_db
from app.main import app
from app.models import DocumentChunk, LegalDocument
from app.schemas.search import SearchResponse
from app.services import embedding, hybrid, legal_refs, search
from app.services.embedding import EmbeddingError, GeminiEmbedder, OpenRouterEmbedder, estimate_query_tokens

NQ, BLDS, LDD, ND = "03/2026/NQ-HĐTP", "91/2015/QH13", "31/2024/QH15", "21/2021/NĐ-CP"


def _info(dieu, khoan=None, diem=None):
    return {"chuong": None, "muc": None, "dieu": dieu, "khoan": khoan, "diem": diem}


def _doc(db, number, title, chunks, effective=None):
    doc = LegalDocument(title=title, doc_type="luat", doc_number=number, content="x", status="active",
                        is_processed=True, effective_date=effective)
    db.add(doc)
    db.flush()
    for i, (content, info) in enumerate(chunks):
        db.add(DocumentChunk(document_id=doc.id, chunk_index=i, content=content, section_info=info,
                             embedding=[0.0], embedding_model="fake"))
    db.commit()
    return doc


@pytest.fixture
def corpus(db):
    _doc(db, NQ, "Nghị quyết 03/2026", [
        ("Điều 3. Giải thích từ ngữ\n1. Đặt cọc là biện pháp bảo đảm.", _info("3")),
        ("Điều 4. Phạt cọc\n1. Bên đặt cọc từ chối giao kết thì mất cọc.\n"
         "2. Bên nhận cọc từ chối thì trả lại cọc và một khoản tương đương.\n3. Các bên có thể thỏa thuận khác.", _info("4")),
        ("Điều 6. Hậu quả của việc đặt cọc vô hiệu\n2. Các bên hoàn trả cho nhau những gì đã nhận.", _info("6", "2")),
        ("Điều 6. Hậu quả của việc đặt cọc vô hiệu\n3. Bên nhận cọc hoàn trả tài sản đặt cọc "
         "theo quy định tại khoản 2 Điều 328 của Bộ luật Dân sự.", _info("6", "3")),
        ("Điều 6. Hậu quả của việc đặt cọc vô hiệu\n4. Lãi trên khoản tiền phải hoàn trả tính theo Điều 357 của Bộ luật Dân sự.",
         _info("6", "4")),
        ("Điều 6. Hậu quả của việc đặt cọc vô hiệu\n5. Bồi thường thiệt hại theo Điều 419 của Bộ luật Dân sự; "
         "thời hiệu theo Điều 8 của Nghị quyết này.", _info("6", "5")),
        ("Điều 8. Thời hiệu khởi kiện\n1. Thời hiệu khởi kiện là ba năm.", _info("8")),
    ], effective=date(2026, 10, 10))
    _doc(db, BLDS, "Bộ luật Dân sự 2015", [
        ("Điều 328. Đặt cọc\n1. Đặt cọc là việc một bên giao cho bên kia một khoản tiền.\n"
         "2. Trường hợp hợp đồng không được giao kết thì tài sản đặt cọc được trả lại. Xem thêm Điều 999 của Bộ luật này.",
         _info("328")),
        ("Điều 357. Trách nhiệm do chậm thực hiện nghĩa vụ trả tiền\n1. Lãi trên số tiền chậm trả.", _info("357")),
        ("Điều 419. Thiệt hại được bồi thường do vi phạm hợp đồng\n1. Thiệt hại thực tế.", _info("419")),
        ("Điều 999. Điều không được kéo theo\n1. Nội dung hop-2.", _info("999")),
    ])
    _doc(db, LDD, "Luật Đất đai 2024", [
        ("Điều 121. Chuyển mục đích sử dụng đất\n1. Các trường hợp phải xin phép.", _info("121")),
        ("Điều 227. Giải quyết tranh chấp đất đai\n1. Hòa giải.", _info("227")),
    ])
    _doc(db, ND, "Nghị định 21/2021", [("Điều 37. Xử lý tài sản bảo đảm\n1. Bán đấu giá.", _info("37"))])
    return db


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("QUERY_EXPANSION_ENABLED", "false")
    monkeypatch.setenv("QUERY_SPLIT_ENABLED", "false")
    get_settings.cache_clear()
    hybrid.reset_index()
    search._embed_cache.clear()
    yield
    hybrid.reset_index()
    search._embed_cache.clear()
    get_settings.cache_clear()


class SpyEmbedder:
    """Nhà cung cấp embedding giả, đếm lần gọi và chuỗi thật sự được gửi đi."""

    model_name = "fake"

    def __init__(self):
        self.texts = []

    def embed_query(self, text):
        self.texts.append(text)
        return [0.0]


@pytest.fixture
def vector_spy(monkeypatch):
    """semantic_search (pgvector) giả: đếm số lần gọi, mặc định không trả gì."""
    calls = []

    def fake(db, vec, top_k, **kw):
        calls.append(top_k)
        return []

    monkeypatch.setattr(search.document_repo, "semantic_search", fake)
    return calls


@pytest.fixture
def no_lexical(monkeypatch):
    """Tắt nhánh BM25 để chỉ còn exact / liền kề / dẫn chiếu: BM25 trên kho nhỏ này khớp gần như mọi đoạn nên làm mờ phép thử."""
    monkeypatch.setattr(hybrid, "independent_rankings", lambda *a, **k: ([], []))


def _ids(out):
    return [(c["document"]["doc_number"], c["section_info"]["dieu"], c["section_info"]["khoan"]) for c in out]


# ---------------------------------------------------------------- 1. embedding_token_budget = 0


def test_budget_zero_makes_no_provider_call_but_exact_and_lexical_still_return(corpus, vector_spy):
    emb = SpyEmbedder()
    out = search.search_chunks(corpus, emb, "Điều 328 BLDS đặt cọc", top_k=5, embedding_token_budget=0,
                               request_turn_id="turn-7")
    usage = out.embedding_usage
    assert emb.texts == [] and vector_spy == []
    assert (usage.attempts, usage.prompt_tokens, usage.cost_nano_usd) == (0, 0, 0)
    assert usage.skipped_reason == "budget_zero" and usage.request_turn_id == "turn-7"
    assert _ids(out)[0] == (BLDS, "328", None)  # exact vẫn trả được
    assert "exact" in out[0]["rank_sources"]
    # lexical thuần (không có tham chiếu) cũng vẫn trả
    lexical_only = search.search_chunks(corpus, emb, "hoàn trả tài sản đặt cọc", top_k=5, embedding_token_budget=0)
    assert lexical_only and lexical_only.embedding_usage.skipped_reason == "budget_zero" and emb.texts == []
    assert any("bm25" in c["rank_sources"] for c in lexical_only)


# ---------------------------------------------------------------- 2. ngân sách nhỏ: cắt chuỗi SAU mở rộng


def test_small_budget_trims_expanded_query_within_budget(corpus, vector_spy, monkeypatch):
    emb = SpyEmbedder()
    question = "tôi muốn sang tên sổ đỏ rồi tách đất tặng cho con một phần thì cần giấy tờ gì"
    expanded = search.expand(question)
    assert expanded != question
    monkeypatch.setenv("QUERY_EXPANSION_ENABLED", "true")
    get_settings.cache_clear()
    out = search.search_chunks(corpus, emb, question, top_k=5, embedding_token_budget=20)
    usage = out.embedding_usage
    (sent,) = emb.texts
    assert estimate_query_tokens(sent) <= 20 and usage.prompt_tokens <= 20
    assert usage.budget_capped is True
    assert usage.cost_nano_usd == usage.prompt_tokens * 150 <= 20 * 150
    assert expanded.startswith(sent) and expanded[len(sent)].isspace()  # cắt đúng ranh giới từ, từ phần đuôi (mở rộng) trở lại
    assert vector_spy  # vẫn có vector cho phần đã giữ


def test_budget_is_shared_deduplicated_and_cached(vector_spy):
    emb = SpyEmbedder()
    outcome = embedding.embed_queries_budgeted(emb, ["alpha beta", "alpha beta", "gamma delta " * 40], budget=30)
    assert emb.texts[0] == "alpha beta" and len(emb.texts) == 2  # chuỗi trùng chỉ gửi 1 lần
    assert outcome.vectors[0] == outcome.vectors[1] is not None
    assert outcome.usage.prompt_tokens <= 30 and outcome.usage.budget_capped
    assert outcome.truncated == [False, False, True]
    # lần hai cùng truy vấn không bị cắt: lấy từ cache, không tốn token
    emb2 = SpyEmbedder()
    first = search.search_chunks(None, emb2, "câu hỏi lặp lại", top_k=3, embedding_token_budget=500)
    second = search.search_chunks(None, emb2, "câu hỏi lặp lại", top_k=3, embedding_token_budget=500)
    assert len(emb2.texts) == 1
    assert first.embedding_usage.prompt_tokens > 0 and first.embedding_usage.cached_tokens == 0
    assert (second.embedding_usage.attempts, second.embedding_usage.cost_nano_usd) == (0, 0)
    assert second.embedding_usage.cached_tokens > 0


def test_default_budget_comes_from_settings(monkeypatch, vector_spy):
    monkeypatch.setenv("HYBRID_ENABLED", "false")
    monkeypatch.setenv("EMBEDDING_DEFAULT_TOKEN_BUDGET", "12")
    get_settings.cache_clear()
    emb = SpyEmbedder()
    out = search.search_chunks(None, emb, "một câu hỏi khá dài về đặt cọc và chuyển mục đích sử dụng đất", top_k=3)
    assert out.embedding_usage.budget_capped and estimate_query_tokens(emb.texts[0]) <= 12


# ---------------------------------------------------------------- 3. online retry = 0, ingest giữ retry


class _Emb:
    def __init__(self, values):
        self.values = values


class _GeminiModels:
    def __init__(self, dim, fail):
        self.calls, self.dim, self.fail = [], dim, list(fail)

    def embed_content(self, *, model, contents, config):
        from google.genai import errors

        self.calls.append(list(contents))
        if self.fail:
            raise errors.APIError(self.fail.pop(0), {"error": {"message": "lỗi giả", "status": "X"}})
        return type("R", (), {"embeddings": [_Emb([1.0] + [0.0] * (self.dim - 1)) for _ in contents]})()


def _gemini(fail):
    models = _GeminiModels(8, fail)
    sleeps = []
    client = type("C", (), {"models": models})()
    return GeminiEmbedder(api_key="", client=client, dim=8, sleep=sleeps.append), models, sleeps


def test_online_query_embedding_never_retries_but_ingest_still_does():
    e, models, sleeps = _gemini([429, 429, 429])
    with pytest.raises(EmbeddingError) as err:
        e.embed_query("đặt cọc")
    assert len(models.calls) == 1 and sleeps == [] and err.value.attempts == 1  # trước đây 3 lần
    with pytest.raises(EmbeddingError):
        e.embed_queries(["a", "b"])
    assert len(models.calls) == 2  # 1 lần nữa, vẫn không retry

    ingest, ingest_models, ingest_sleeps = _gemini([429, 503])
    assert len(ingest.embed_documents(["điều luật"])) == 1
    assert len(ingest_models.calls) == 3 and ingest_sleeps == [5, 15]  # retry nạp văn bản giữ nguyên


def test_search_reports_failed_attempt_and_still_returns_lexical_results(corpus, vector_spy):
    e, models, _ = _gemini([503, 503, 503])
    e.model_name = "fake"
    out = search.search_chunks(corpus, e, "hoàn trả tài sản đặt cọc", top_k=5)
    assert len(models.calls) == 1
    assert out and out.embedding_usage.skipped_reason == "embedding_failed"
    assert out.embedding_usage.attempts == 1 and out.embedding_usage.cost_nano_usd == 0
    # không có gì để trả thì vẫn ném lỗi như trước (API → 503)
    e2, _, _ = _gemini([503])
    e2.model_name = "fake"
    with pytest.raises(EmbeddingError):
        search.search_chunks(corpus, e2, "zzzqqq xxxyyy", top_k=5)


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


class _Http:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), 0

    def post(self, url, headers=None, json=None):
        self.calls += 1
        return self.responses.pop(0)


def test_openrouter_online_one_call_and_provider_usage_is_billed():
    http = _Http([_Resp(429, {}), _Resp(429, {}), _Resp(429, {})])
    e = OpenRouterEmbedder(api_key="k", dim=4, client=http, sleep=lambda s: None)
    outcome = embedding.embed_queries_budgeted(e, ["đặt cọc"], budget=100)
    assert http.calls == 1 and outcome.error is not None and outcome.usage.attempts == 1

    ok = _Resp(200, {"data": [{"index": 0, "embedding": [1, 0, 0, 0]}], "usage": {"prompt_tokens": 7, "total_tokens": 7}})
    e = OpenRouterEmbedder(api_key="k", dim=4, client=_Http([ok]), sleep=lambda s: None)
    vec, usage = embedding.embed_query_with_usage(e, "đặt cọc", budget=100, request_turn_id="t1")
    assert vec == [1.0, 0.0, 0.0, 0.0]
    assert (usage.prompt_tokens, usage.cost_nano_usd, usage.attempts, usage.request_turn_id) == (7, 1050, 1, "t1")


# ---------------------------------------------------------------- 4. tra cứu chính xác (văn bản, Điều, Khoản, Điểm)


@pytest.mark.parametrize("query, expected", [
    ("khoản 2 Điều 328 BLDS", (BLDS, "328", None)),  # Điều ngắn lưu nguyên điều: khoản nằm trong chunk cha
    ("Điều 328 Bộ luật Dân sự 2015", (BLDS, "328", None)),
    ("Điều 121 LĐĐ", (LDD, "121", None)),
    ("Điều 121 Luật Đất đai", (LDD, "121", None)),
    ("điều 121 luật số 31/2024/QH15", (LDD, "121", None)),
    ("khoản 3 Điều 6 NQ 03/2026", (NQ, "6", "3")),
    ("K.3 Đ.6 NQ 03/2026", (NQ, "6", "3")),
    ("Điều 8 Nghị quyết 03", (NQ, "8", None)),
    ("Điều 37 NĐ 21/2021", (ND, "37", None)),
    ("khoản 3 Điều 4 NQ 03/2026", (NQ, "4", None)),  # Điều 4 không tách khoản
])
def test_exact_lookup_resolves_abbreviation_and_clause(corpus, vector_spy, query, expected):
    out = search.search_chunks(corpus, SpyEmbedder(), query, top_k=5, embedding_token_budget=0,
                               expand_adjacent_clauses=False, follow_citations=False)
    assert _ids(out)[0] == expected
    assert "exact" in out[0]["rank_sources"]


def test_clause_that_does_not_exist_is_not_a_verified_match(corpus):
    specs = legal_refs.parse_query_refs("khoản 9 Điều 4 NQ 03/2026")
    found = legal_refs.lookup(corpus, "fake", None, specs)
    assert found[specs[0]] == []
    specs = legal_refs.parse_query_refs("điểm a khoản 2 Điều 6 NQ 03/2026")
    assert specs == [legal_refs.RefSpec(NQ, "6", "2", "a")]
    assert legal_refs.parse_query_refs("Điều 6 không rõ văn bản nào") == []  # không đoán văn bản


def test_article_title_lookup_via_exact_refs(corpus):
    refs = [{"doc_ref": "LĐĐ", "title": "chuyển mục đích sử dụng đất"}, {"doc_ref": "BLDS", "article": "Điều 357"},
            {"doc_ref": "NQ 03/2026", "title": "Thời hiệu khởi kiện"}]
    out = search.search_chunks(corpus, SpyEmbedder(), "câu hỏi tự do", top_k=10, embedding_token_budget=0,
                               exact_refs=refs, expand_adjacent_clauses=False, follow_citations=False)
    assert {(d, a) for d, a, _ in _ids(out)} >= {(LDD, "121"), (BLDS, "357"), (NQ, "8")}


# ---------------------------------------------------------------- 5. Khoản liền kề


def _count_selects(db):
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    return statements, lambda: event.remove(engine, "before_cursor_execute", capture)


def _run(corpus, query, top_k=12, **kw):
    emb = SpyEmbedder()
    return search.search_chunks(corpus, emb, query, top_k=top_k, embedding_token_budget=500, **kw), emb


QUERY_K3 = "lãi và bồi thường khi đặt cọc vô hiệu, khoản 3 Điều 6 NQ 03/2026"


def test_adjacent_clauses_are_pulled_with_zero_extra_embedding_calls(corpus, vector_spy, no_lexical):
    base, base_emb = _run(corpus, QUERY_K3, expand_adjacent_clauses=False, follow_citations=False)
    statements, stop = _count_selects(corpus)
    try:
        out, emb = _run(corpus, QUERY_K3, follow_citations=False)
    finally:
        stop()
    ids = _ids(out)
    assert (NQ, "6", "3") in ids and (NQ, "6", "4") in ids and (NQ, "6", "5") in ids and (NQ, "6", "2") in ids
    assert not any("citation" in c["rank_sources"] for c in out)  # follow_citations=False
    assert (NQ, "6", "4") not in _ids(base)
    adjacent = [c for c in out if c["section_info"]["khoan"] in ("4", "5")]
    assert adjacent and all("adjacent" in c["rank_sources"] for c in adjacent)
    assert len(emb.texts) == len(base_emb.texts) == 1  # liền kề không tốn thêm embedding
    # theo lô, không N+1: đúng 1 truy vấn exact + 1 truy vấn liền kề cho mọi hạt giống (BM25 đã được thay thế ở test này)
    assert sum(s.lstrip().upper().startswith("SELECT") for s in statements) == 2


# ---------------------------------------------------------------- 6. dẫn chiếu chéo (1 bước)


def test_cross_reference_following_adds_cited_provisions_without_embedding(corpus, vector_spy, no_lexical):
    out, emb = _run(corpus, QUERY_K3)
    ids = _ids(out)
    assert (BLDS, "328", None) in ids  # K.3 → khoản 2 Điều 328 của Bộ luật Dân sự
    assert (BLDS, "357", None) in ids  # K.4 → Điều 357
    assert (BLDS, "419", None) in ids  # K.5 → Điều 419
    assert (NQ, "8", None) in ids  # K.5 → "Điều 8 của Nghị quyết này" = cùng văn bản
    assert all("citation" in c["rank_sources"] for c in out if c["section_info"]["dieu"] in ("328", "357", "419"))
    assert not any(i[1] == "999" for i in ids)  # Điều 328 dẫn tiếp Điều 999: hop 2, KHÔNG đi theo
    assert len(emb.texts) == 1  # đúng 1 lần embedding cho cả câu

    off, _ = _run(corpus, QUERY_K3, follow_citations=False)
    assert not any(i[0] == BLDS for i in _ids(off))  # không còn nguồn nào khác đưa BLDS vào


def test_citation_from_exact_seed_is_hop_one_and_resolves_this_law(corpus, vector_spy, no_lexical):
    # Điều 328 là hạt giống (exact) nên dẫn chiếu "Điều 999 của Bộ luật này" trong nó được đi theo, nhưng chỉ 1 bước
    out, _ = _run(corpus, "Điều 328 BLDS")
    assert (BLDS, "999", None) in _ids(out)
    assert [c["rank_sources"] for c in out if c["section_info"]["dieu"] == "999"] == [["citation"]]


def test_followed_references_are_bounded(corpus, vector_spy, monkeypatch, no_lexical):
    monkeypatch.setattr(search, "MAX_CITATION_REFS", 1)
    out, _ = _run(corpus, QUERY_K3)
    cited = [c for c in out if "citation" in c["rank_sources"] and c["document"]["doc_number"] == BLDS]
    assert len(cited) == 1


# ---------------------------------------------------------------- 7. giữ chỗ cho căn cứ đã kiểm chứng


def _crowd(corpus, monkeypatch):
    """Vector trả 14 đoạn rất gần thuộc 14 Điều khác nhau của một văn bản nhiễu: không đoạn nào bị MAX_PER_DIEU loại."""
    monkeypatch.setattr(hybrid, "independent_rankings", lambda *a, **k: ([], []))
    noise = _doc(corpus, "99/2099/QH-NHIEU", "Luật nhiễu",
                 [(f"Điều {i}. Đặt cọc nhiễu\n1. đặt cọc đặt cọc {i}", _info(str(i))) for i in range(1, 15)])
    rows = [(c, noise, 0.01 + i / 1000) for i, c in enumerate(noise.chunks)]
    monkeypatch.setattr(search.document_repo, "semantic_search", lambda *a, **k: list(rows))


def test_verified_slots_survive_rrf_and_per_article_caps(corpus, monkeypatch):
    _crowd(corpus, monkeypatch)
    out, _ = _run(corpus, QUERY_K3, top_k=12)
    ids = _ids(out)
    for needed in [(NQ, "6", "3"), (NQ, "6", "4"), (NQ, "6", "5"), (BLDS, "328", None), (BLDS, "357", None),
                   (BLDS, "419", None), (NQ, "8", None)]:
        assert needed in ids
    assert len(out) <= 12
    assert [c["rrf_rank"] for c in out] == list(range(1, len(out) + 1))
    by_id = dict(zip(ids, out))
    assert "exact" in by_id[(NQ, "6", "3")]["rank_sources"]
    assert "citation" in by_id[(BLDS, "328", None)]["rank_sources"]
    noise = [c for c in out if c["document"]["doc_number"] == "99/2099/QH-NHIEU"]
    assert noise and all("vector" in c["rank_sources"] for c in noise)
    assert by_id[(NQ, "8", None)]["effective_date"] == "2026-10-10"


def test_small_top_k_reserves_slots_but_not_everything(corpus, monkeypatch):
    _crowd(corpus, monkeypatch)
    out, _ = _run(corpus, QUERY_K3, top_k=5)
    ids = _ids(out)
    assert len(out) == 5
    assert ids[0] == (NQ, "6", "3") and (BLDS, "328", None) in ids  # exact đứng đầu, dẫn chiếu quan trọng nhất còn chỗ
    assert any(i[0] == "99/2099/QH-NHIEU" for i in ids)  # ≥ 40% top_k vẫn dành cho kết quả xếp hạng tự nhiên


def test_legacy_requests_without_new_fields_keep_old_shape(db, monkeypatch):
    """Không có tham chiếu và không có dòng hybrid: kết quả giống hệt trước đây (không thêm khoá mới)."""
    _doc(db, "98/2098/QH-X", "Luật X", [("Điều 1. Một\n1. tách thửa đất", _info("1"))])
    monkeypatch.setattr(search.document_repo, "semantic_search", lambda *a, **k: [])
    monkeypatch.setenv("HYBRID_ENABLED", "false")
    get_settings.cache_clear()
    out = search.search_chunks(db, SpyEmbedder(), "tách thửa đất", top_k=3)
    assert out == [] and out.embedding_usage.attempts == 1


# ---------------------------------------------------------------- 8. endpoint nội bộ

URL = "/internal/v1/rag/search"
TOKEN = {"X-Internal-Token": "test-internal-token"}


@pytest.fixture
def client(corpus, vector_spy, monkeypatch):
    emb = SpyEmbedder()
    real = search.search_chunks
    monkeypatch.setattr("app.api.v1.internal.search_chunks", lambda _db, e, *a, **k: real(corpus, e, *a, **k))
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[embedder_dep] = lambda: emb
    c = TestClient(app, raise_server_exceptions=False)
    c.emb = emb
    yield c
    app.dependency_overrides.clear()


def test_endpoint_returns_embedding_usage_and_new_chunk_metadata(client):
    r = client.post(URL, json={"query": "Điều 328 BLDS", "embedding_token_budget": 0, "request_turn_id": "turn-1",
                               "exact_refs": [{"doc_ref": "LĐĐ", "article": "121"}], "top_k": 5},
                    headers={**TOKEN, "X-Request-ID": "req-1"})
    assert r.status_code == 200
    body = r.json()
    data = SearchResponse.model_validate(body["data"])  # đúng schema đã công bố cho agent-service
    assert body["request_id"] == "req-1"
    assert data.embedding_usage.skipped_reason == "budget_zero" and data.embedding_usage.cost_nano_usd == 0
    assert data.embedding_usage.request_turn_id == "turn-1" and client.emb.texts == []
    assert {c.document["doc_number"] for c in data.chunks} >= {BLDS, LDD}
    assert all(c.rank_sources and c.rrf_rank for c in data.chunks)


def test_endpoint_is_backward_compatible_when_optional_fields_are_omitted(client):
    r = client.post(URL, json={"query": "hoàn trả tài sản đặt cọc"}, headers=TOKEN)
    assert r.status_code == 200
    data = r.json()["data"]
    assert {"id", "content", "score", "section_info", "document"} <= data["chunks"][0].keys()
    usage = data["embedding_usage"]
    assert usage["attempts"] == 1 and usage["prompt_tokens"] > 0 and usage["cost_nano_usd"] == usage["prompt_tokens"] * 150
    assert len(client.emb.texts) == 1
    r = client.post(URL, json={"query": "x", "embedding_token_budget": -1}, headers=TOKEN)
    assert r.status_code == 422
