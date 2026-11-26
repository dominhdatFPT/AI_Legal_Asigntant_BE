"""BM25 helpers and independent hybrid RRF; no network or production database."""
from datetime import datetime, timedelta, timezone

from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.models import DocumentChunk, LegalDocument
from app.services import hybrid, search


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """Mỗi test bắt đầu với chỉ mục trống và các cờ hybrid/tách câu/mở rộng đã biết."""
    for k in ("HYBRID_ENABLED", "HYBRID_ALPHA", "HYBRID_POOL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("QUERY_EXPANSION_ENABLED", "false")
    monkeypatch.setenv("QUERY_SPLIT_ENABLED", "false")
    get_settings.cache_clear()
    hybrid.reset_index()
    yield
    hybrid.reset_index()
    get_settings.cache_clear()


def _set(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    get_settings.cache_clear()


class OneVectorEmbedder:
    model_name = "fake"

    def embed_query(self, text):
        return [0.0]


def _row(cid, content, distance, dieu="1", doc_id="d1"):
    chunk = SimpleNamespace(id=cid, content=content, section_info={"dieu": dieu, "khoan": None})
    doc = SimpleNamespace(id=doc_id, title="Luật Mẫu", doc_number="01/2099/QH-MAU", doc_type="luat")
    return chunk, doc, distance


def _index(rows):
    return hybrid.Bm25Index([(str(c.id), c.content) for c, _d, _x in rows])


# ---------------------------------------------------------------- H. Tách từ


def test_H_tach_tu_giu_dau_ra_don_le_va_bigram():
    toks = hybrid.tokenize("Tách thửa đất, phần vốn góp!")
    assert {"tách", "thửa", "đất", "phần", "vốn", "góp"} <= set(toks)  # giữ nguyên dấu tiếng Việt
    assert {"tách thửa", "thửa đất", "phần vốn", "vốn góp"} <= set(toks)
    assert "đất phần" not in toks  # bigram không bắc qua dấu phẩy
    assert "thua" not in toks and "tach thua" not in toks  # không bỏ dấu


def test_H_bo_tu_dung_va_bigram_khong_chua_tu_dung():
    toks = hybrid.tokenize("Quyền của người sử dụng đất và các nghĩa vụ")
    for stop in ("của", "và", "các"):
        assert stop not in toks
        assert not any(stop in t.split() for t in toks)
    assert "quyền người" not in toks  # "của" bị bỏ thì không nối hai bên thành bigram giả
    assert "sử dụng" in toks and "người" in toks


def test_H_viet_hoa_va_nfc():
    decomposed = "Tách thửa".replace("ử", "ử")  # 'ử' dạng tổ hợp
    assert hybrid.tokenize(decomposed.upper()) == hybrid.tokenize("tách thửa")


# ---------------------------------------------------------------- C. IDF


def test_C_tu_hiem_co_trong_so_cao_hon_tu_pho_bien():
    docs = [(f"c{i}", "đất đai quy hoạch") for i in range(9)] + [("c9", "đất đai hợp tác xã")]
    idx = hybrid.Bm25Index(docs)
    assert idx.idf("xã") > idx.idf("đất")
    # cùng một lần xuất hiện: đoạn chứa từ hiếm điểm cao hơn đoạn chứa từ phổ biến
    assert idx.score(["xã"], "c9") > idx.score(["đất"], "c9")
    assert idx.idf("không-có-trong-chỉ-mục") > idx.idf("xã")


# ---------------------------------------------------------------- D. Bigram


def test_D_bigram_dua_doan_co_dung_cum_len_truoc(monkeypatch):
    # A chỉ có từng âm tiết rời nhau; B có đúng cụm "tách thửa". Vector đang xếp A trên B.
    a = _row("A", "tách riêng hồ sơ. nộp thửa đất ở xã", 0.30)
    b = _row("B", "điều kiện tách thửa đất nông nghiệp", 0.31)
    rows = [a, b]
    out = hybrid.rerank(rows, "tách thửa", _index(rows), alpha=0.05)
    assert [r[0].id for r in out] == ["B", "A"]


# ---------------------------------------------------------------- E, F. Công thức


def test_E_khong_khop_tu_nao_thi_giu_thu_tu_vector():
    rows = [_row("a", "thế chấp quyền sử dụng đất", 0.2), _row("b", "giải thể doanh nghiệp", 0.3), _row("c", "x", 0.4)]
    trace = []
    out = hybrid.rerank(rows, "zzz qqq", _index(rows), alpha=100.0, trace=trace)
    assert [r[0].id for r in out] == ["a", "b", "c"]
    assert all(t["bm25_raw"] == 0 and t["bm25_norm"] == 0 for t in trace)


def _tap_ung_vien():
    return [
        _row("v1", "quy định chung về đất đai", 0.20),
        _row("v2", "hợp tác xã rút vốn góp", 0.21),
        _row("v3", "thành viên hợp tác xã rút vốn khỏi hợp tác xã", 0.30),
        _row("v4", "giải thể", 0.35),
    ]


def test_F_alpha_0_la_vector_thuan_va_alpha_lon_theo_bm25_nhung_tap_khong_doi():
    rows = _tap_ung_vien()
    idx = _index(rows)
    q = "hợp tác xã rút vốn"
    assert [r[0].id for r in hybrid.rerank(rows, q, idx, alpha=0.0)] == ["v1", "v2", "v3", "v4"]

    out = hybrid.rerank(rows, q, idx, alpha=1000.0)
    raw = {r[0].id: idx.score(hybrid.query_terms(q), str(r[0].id)) for r in rows}
    assert [raw[r[0].id] for r in out] == sorted(raw.values(), reverse=True)  # theo BM25
    assert out[0][0].id != "v1"
    assert sorted(r[0].id for r in out) == ["v1", "v2", "v3", "v4"]  # cùng tập
    assert {id(r[0]) for r in out} == {id(r[0]) for r in rows}  # cùng đúng các đoạn
    assert all(tuple(r)[:3] == tuple(o)[:3] for r in rows for o in out if o[0] is r[0])  # (đoạn, văn bản, khoảng cách) nguyên vẹn


def test_chuan_hoa_theo_tung_cau_va_cong_thuc():
    rows = _tap_ung_vien()
    idx = _index(rows)
    trace = []
    out = hybrid.rerank(rows, "hợp tác xã rút vốn", idx, alpha=0.05, trace=trace)
    by_id = {t["id"]: t for t in trace}
    top = max(t["bm25_raw"] for t in trace)
    assert max(t["bm25_norm"] for t in trace) == pytest.approx(1.0)
    for t, (_c, _d, dist) in zip(trace, rows):
        assert t["bm25_norm"] == pytest.approx(t["bm25_raw"] / top)
        assert t["final"] == pytest.approx((1 - dist) + 0.05 * t["bm25_norm"])
    assert [by_id[r[0].id]["rank_after"] for r in out] == [1, 2, 3, 4]
    assert all(by_id[i]["rank_before"] == n for n, i in enumerate(["v1", "v2", "v3", "v4"], 1))


def test_pool_chi_cham_diem_va_sap_lai_phan_dau():
    rows = _tap_ung_vien()
    idx = _index(rows)
    out = hybrid.rerank(rows, "giải thể", idx, alpha=1000.0, pool=2)
    assert [r[0].id for r in out][2:] == ["v3", "v4"]  # phần ngoài pool giữ nguyên, đứng sau
    assert sorted(r[0].id for r in out) == ["v1", "v2", "v3", "v4"]


# ---------------------------------------------------------------- A, B. search_chunks


def _fake_search(monkeypatch, rows_by_call):
    seen = []

    def fake_semantic_search(db, vec, top_k, **kw):
        seen.append(top_k)
        return list(rows_by_call)

    monkeypatch.setattr(search.document_repo, "semantic_search", fake_semantic_search)
    return seen


def _reference_old_pipeline(row_lists, top_k):
    """Đúng thân hàm cũ (trước khi có hybrid) từ semantic_search trở đi."""
    rows = search._diversify(search._interleave(row_lists), top_k)
    return [
        {
            "id": str(chunk.id),
            "content": chunk.content,
            "score": round(max(0.0, min(1.0, 1.0 - distance)), 4),
            "section_info": chunk.section_info,
            "document": {
                "id": str(doc.id), "title": doc.title, "doc_number": doc.doc_number, "doc_type": doc.doc_type,
            },
        }
        for chunk, doc, distance in rows
    ]


def _many_rows():
    contents = ["tách thửa đất", "hợp tác xã rút vốn", "giải thể doanh nghiệp", "thế chấp đất", "tặng cho đất"]
    return [
        _row(f"c{i}", contents[i % 5] + f" số {i}", 0.10 + i * 0.013, dieu=str(i // 3), doc_id=f"d{i % 2}")
        for i in range(30)
    ]


@pytest.mark.parametrize("top_k", [1, 4, 7])
def test_A_tat_cong_tac_giong_het_ban_cu(monkeypatch, top_k):
    _set(monkeypatch, HYBRID_ENABLED="false")
    rows = _many_rows()
    _fake_search(monkeypatch, rows)
    # Công tắc tắt: không được đụng tới hybrid (db=None cũng không sao)
    monkeypatch.setattr(hybrid, "rerank_with_db", lambda *a, **k: pytest.fail("hybrid được gọi khi đã tắt"))
    monkeypatch.setattr(hybrid, "get_index", lambda *a, **k: pytest.fail("chỉ mục được dựng khi đã tắt"))
    assert get_settings().hybrid_enabled is False
    out = search.search_chunks(None, OneVectorEmbedder(), "tách thửa đất", top_k=top_k)
    assert out == _reference_old_pipeline([rows], top_k)


def test_A_tat_cong_tac_giong_het_voi_cau_nhieu_y(monkeypatch):
    _set(monkeypatch, HYBRID_ENABLED="false", QUERY_SPLIT_ENABLED="true")
    rows = _many_rows()
    _fake_search(monkeypatch, rows)
    monkeypatch.setattr(hybrid, "rerank_with_db", lambda *a, **k: pytest.fail("hybrid được gọi khi đã tắt"))
    q = "Tôi muốn tách thửa đất rồi tặng cho con một phần, cần điều kiện gì và thời hạn sử dụng đất thế nào?"
    out = search.search_chunks(None, OneVectorEmbedder(), q, top_k=4)
    assert out == _reference_old_pipeline([rows] * 4, 4)  # câu gốc + 3 ý, mỗi lần tìm cùng danh sách giả


def _lexical_stub(monkeypatch, corpus):
    index = _index(corpus)

    def independent(db, query, embedding_model, doc_types, top_k):
        terms = hybrid.query_terms(query)
        ranked = sorted(corpus, key=lambda r: -index.score(terms, str(r[0].id)))
        return [r for r in ranked[:top_k] if index.score(terms, str(r[0].id)) > 0], []

    monkeypatch.setattr(hybrid, "independent_rankings", independent)


def test_independent_lexical_adds_candidate_absent_from_vector(monkeypatch):
    _set(monkeypatch, HYBRID_ENABLED="true")
    rows = _many_rows()
    missing = _row("lexical-only", "rarekeyword rarekeyword", 1.0, dieu="99")
    _fake_search(monkeypatch, rows)
    _lexical_stub(monkeypatch, rows + [missing])
    out = search.search_chunks(None, OneVectorEmbedder(), "rarekeyword", top_k=6)
    item = next(r for r in out if r["id"] == "lexical-only")
    assert item["score"] == 0.0
    assert item["retrieval_sources"] == [{"source": "lexical", "rank": 1}]
    distances = {r[0].id: r[2] for r in rows}
    for row in out:
        if row["id"] in distances:
            assert row["score"] == round(1.0 - distances[row["id"]], 4)


def test_rrf_uses_ranks_independent_of_legacy_alpha(monkeypatch):
    _set(monkeypatch, HYBRID_ENABLED="true", HYBRID_ALPHA="0")
    rows = _many_rows()
    _fake_search(monkeypatch, rows)
    _lexical_stub(monkeypatch, rows)
    out = search.search_chunks(None, OneVectorEmbedder(), "hợp tác xã rút vốn", top_k=5)
    assert out[0]["id"] == "c1"
    assert len(out[0]["retrieval_sources"]) == 2


def test_hybrid_runs_each_expanded_subquery(monkeypatch):
    _set(monkeypatch, HYBRID_ENABLED="true", QUERY_SPLIT_ENABLED="true", QUERY_EXPANSION_ENABLED="true")
    _fake_search(monkeypatch, _many_rows())
    queries = []

    def spy(db, query, embedding_model, doc_types, top_k):
        queries.append(query)
        return [], []

    monkeypatch.setattr(hybrid, "independent_rankings", spy)
    q = "Tôi muốn tách sổ đỏ rồi tặng cho con một phần, cần điều kiện gì và thời hạn sử dụng đất thế nào?"
    search.search_chunks(None, OneVectorEmbedder(), q, top_k=4)
    assert len(queries) == 4
    assert queries[0] == search.expand(q) and queries[0] != q


def test_rrf_trace_does_not_change_results(monkeypatch):
    _set(monkeypatch, HYBRID_ENABLED="true")
    rows = _many_rows()
    _fake_search(monkeypatch, rows)
    _lexical_stub(monkeypatch, rows)
    plain = search.search_chunks(None, OneVectorEmbedder(), "tách thửa", top_k=5)
    trace = []
    traced = search.search_chunks(None, OneVectorEmbedder(), "tách thửa", top_k=5, trace=trace)
    assert traced == plain
    candidates = trace[0]["candidates"]
    assert len(candidates) == 30 and {"id", "sources", "rrf"} <= candidates[0].keys()
    assert [r["id"] for r in trace[-1]["final"]] == [r["id"] for r in traced]


def test_independent_branch_failure_preserves_vector(monkeypatch):
    _set(monkeypatch, HYBRID_ENABLED="true")
    rows = _many_rows()
    _fake_search(monkeypatch, rows)

    def boom(*args):
        raise RuntimeError("lexical database unavailable")

    monkeypatch.setattr(hybrid, "independent_rankings", boom)
    out = search.search_chunks(None, OneVectorEmbedder(), "query", top_k=5)
    assert out == _reference_old_pipeline([rows], 5)


def test_rrf_consensus_and_deduplication():
    a, b, c = [_row(i, i, 0.1, dieu=i) for i in ("a", "b", "c")]
    fused = hybrid.reciprocal_rank_fusion([[a, b, b], [c, b], [b]])
    assert [r[0].id for r in fused] == ["b", "a", "c"]
    assert len(fused[0].retrieval_sources) == 3
    assert 1.0 - fused[0].rank_distance == pytest.approx(1 / 62 + 1 / 62 + 1 / 61)


def test_rrf_diversification_still_limits_article(monkeypatch):
    _set(monkeypatch, HYBRID_ENABLED="true")
    rows = [_row(str(i), "rarekeyword", 0.1 + i / 100, dieu="1" if i < 4 else str(i)) for i in range(8)]
    _fake_search(monkeypatch, rows)
    _lexical_stub(monkeypatch, rows)
    out = search.search_chunks(None, OneVectorEmbedder(), "rarekeyword", top_k=4)
    assert len(out) == 4
    assert sum(r["section_info"]["dieu"] == "1" for r in out) == 2


# ---------------------------------------------------------------- G. Làm mới chỉ mục


def _add_doc(db, doc_number, texts, status="active", created=None):
    doc = LegalDocument(
        title="Luật Mẫu", doc_type="luat", doc_number=doc_number, content="x", status=status, is_processed=True
    )
    db.add(doc)
    db.flush()
    for i, text in enumerate(texts):
        db.add(
            DocumentChunk(
                document_id=doc.id, chunk_index=i, content=text, section_info={"dieu": str(i)},
                embedding=[0.0], embedding_model="fake",
                **({"created_at": created} if created else {}),
            )
        )
    db.commit()
    return doc


def _builds(monkeypatch):
    calls = {"texts": 0}
    real = hybrid.document_repo.active_chunk_texts

    def counting(db):
        calls["texts"] += 1
        return real(db)

    monkeypatch.setattr(hybrid.document_repo, "active_chunk_texts", counting)
    return calls


def test_G_khong_dung_lai_moi_cau_va_tu_dung_lai_khi_du_lieu_doi(db, monkeypatch):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _add_doc(db, "01/2099/QH-MAU", ["tách thửa đất", "hợp tác xã"], created=t0)
    builds = _builds(monkeypatch)

    first = hybrid.get_index(db)
    assert first.n == 2 and builds["texts"] == 1
    assert hybrid.get_index(db) is first and builds["texts"] == 1  # câu sau dùng lại, không dựng lại

    # trong hạn TTL: dữ liệu đổi nhưng chưa đối chiếu -> vẫn dùng bản cũ (không truy vấn thêm)
    _add_doc(db, "02/2099/QH-MAU", ["giải thể"], created=t0 + timedelta(days=1))
    assert hybrid.get_index(db) is first

    # hết hạn TTL: đối chiếu dấu vân tay thấy số đoạn đổi -> dựng lại
    monkeypatch.setattr(hybrid, "FINGERPRINT_TTL_SECONDS", 0.0)
    second = hybrid.get_index(db)
    assert second is not first and second.n == 3 and builds["texts"] == 2
    # dữ liệu không đổi thì đối chiếu xong vẫn giữ nguyên bản
    assert hybrid.get_index(db) is second and builds["texts"] == 2


def test_G_thay_noi_dung_cung_so_doan_van_dung_lai(db, monkeypatch):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    doc = _add_doc(db, "01/2099/QH-MAU", ["tách thửa đất", "hợp tác xã"], created=t0)
    monkeypatch.setattr(hybrid, "FINGERPRINT_TTL_SECONDS", 0.0)
    first = hybrid.get_index(db)
    # giống replace_chunks: xoá hết rồi tạo lại (cùng số đoạn, created_at mới)
    from app.repositories import document as repo

    repo.replace_chunks(
        db, doc, [{"content": "nội dung mới hoàn toàn", "section_info": {}, "embedding": [0.0]}] * 2, "fake"
    )
    for c in db.query(DocumentChunk).all():
        c.created_at = t0 + timedelta(days=2)
    db.commit()
    second = hybrid.get_index(db)
    assert second is not first and second.n == 2
    assert "mới" in second.df and "mới" not in first.df


def test_G_van_ban_het_active_thi_dung_lai(db, monkeypatch):
    doc = _add_doc(db, "01/2099/QH-MAU", ["tách thửa đất"])
    monkeypatch.setattr(hybrid, "FINGERPRINT_TTL_SECONDS", 0.0)
    assert hybrid.get_index(db).n == 1
    doc.status = "superseded"
    db.commit()
    assert hybrid.get_index(db).n == 0


def test_G_ung_vien_chua_co_trong_chi_muc_thi_doi_chieu_ngay(db, monkeypatch):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _add_doc(db, "01/2099/QH-MAU", ["tách thửa đất"], created=t0)
    _set(monkeypatch, HYBRID_ENABLED="true", HYBRID_ALPHA="1000")
    hybrid.get_index(db)  # chỉ mục có 1 đoạn, TTL còn dài
    new_doc = _add_doc(db, "02/2099/QH-MAU", ["tách thửa tách thửa tách thửa"], created=t0 + timedelta(days=1))
    new_chunk = db.query(DocumentChunk).filter_by(document_id=new_doc.id).one()
    old_chunk = db.query(DocumentChunk).filter(DocumentChunk.document_id != new_doc.id).one()
    rows = [
        (old_chunk, SimpleNamespace(doc_number="a"), 0.30),
        (new_chunk, SimpleNamespace(doc_number="b"), 0.31),
    ]
    out = hybrid.rerank_with_db(db, rows, "tách thửa", alpha=1000.0)
    assert hybrid.get_index(db).n == 2  # đã dựng lại dù chưa hết TTL
    assert [r[0].id for r in out] == [new_chunk.id, old_chunk.id]


def test_chi_muc_tren_sqlite_chi_doc_id_va_content(db):
    _add_doc(db, "01/2099/QH-MAU", ["a b", "c d"])
    from app.repositories import document as repo

    rows = repo.active_chunk_texts(db)
    assert [t for _i, t in rows] == ["a b", "c d"]
    count, newest = repo.active_chunks_fingerprint(db)
    assert count == 2 and newest is not None


def test_sql_khong_keo_embedding_va_toan_van():
    from sqlalchemy.dialects import postgresql

    from app.repositories import document as repo

    sql = str(repo._active_chunks_stmt(repo.DocumentChunk.id, repo.DocumentChunk.content).compile(dialect=postgresql.dialect()))
    assert "embedding" not in sql
    assert "legal_documents.content" not in sql
    assert "status" in sql and "is_processed" in sql


# ---------------------------------------------------------------- Cấu hình


def test_mac_dinh_cau_hinh():
    s = get_settings()
    assert s.hybrid_enabled is True and s.hybrid_alpha == 0.05 and s.hybrid_pool == 0


def test_doi_cau_hinh_bang_bien_moi_truong(monkeypatch):
    _set(monkeypatch, HYBRID_ENABLED="false", HYBRID_ALPHA="0.2", HYBRID_POOL="10")
    s = get_settings()
    assert s.hybrid_enabled is False and s.hybrid_alpha == 0.2 and s.hybrid_pool == 10
