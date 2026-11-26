import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from sqlalchemy import URL, create_engine, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from app.core.config import get_settings
from app.core.database import Base
from app.models import DocumentChunk, LegalDocument
from app.services import corpus_version, hybrid, search


MODEL = "p0-embedding"
QUESTION = "hoàn trả tiền đặt cọc"
DOC_NUMBER = "91/2015/QH13"
EXACT_REFS = [{"doc_ref": DOC_NUMBER, "article": "7", "clause": "1"}]


class Embedder:
    model_name = MODEL

    def embed_queries_with_usage(self, texts):
        return [[0.0] for _ in texts], {"prompt_tokens": 20}, 1


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("HYBRID_ENABLED", "true")
    monkeypatch.setenv("QUERY_EXPANSION_ENABLED", "false")
    monkeypatch.setenv("QUERY_SPLIT_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_PRICE_NANO_PER_TOKEN", "150")
    get_settings.cache_clear()
    hybrid.reset_index()
    engine = create_engine(
        URL.create("sqlite", database=str(tmp_path / "rag.db")),
        poolclass=QueuePool, pool_size=3, max_overflow=2, pool_timeout=2,
    )
    Base.metadata.create_all(engine)
    chunk_ids = [uuid.UUID(int=i) for i in range(1, 4)]
    with Session(engine) as db:
        doc = LegalDocument(
            title="Luật kiểm thử", doc_type="luat", doc_number=DOC_NUMBER, content="Toàn văn",
            status="active", is_processed=True, effective_date=date(2026, 10, 10),
        )
        db.add(doc)
        db.flush()
        for i, (dieu, khoan, content) in enumerate([
            ("7", "1", "Điều 7. Hoàn trả tiền đặt cọc. Áp dụng Điều 8 của Luật này."),
            ("7", "2", "Điều 7. Hạn xử lý. Trong bảy ngày."),
            ("8", None, "Điều 8. Thời hạn xử lý."),
        ]):
            db.add(DocumentChunk(
                id=chunk_ids[i], document_id=doc.id, chunk_index=i, content=content,
                section_info={"dieu": dieu, "khoan": khoan}, embedding=[0.0], embedding_model=MODEL,
            ))
        db.commit()
    try:
        yield engine, [str(cid) for cid in chunk_ids]
    finally:
        engine.dispose()
        hybrid.reset_index()


def _vector_rows(db, ids):
    exact, cited = search.document_repo.retrieval_rows(db, MODEL, chunk_ids=[ids[0], ids[2]])
    return [(cited[0], cited[1], 0.1), (exact[0], exact[1], 0.2)]


@pytest.mark.parametrize("requests", [5, 8])
def test_pool_releases_preflight_before_embedding(corpus, monkeypatch, requests):
    engine, ids = corpus
    begin = threading.Barrier(requests, timeout=10)
    embedding = threading.Barrier(requests, timeout=10)
    current = threading.local()
    real_expand = search._expand_locally

    class SlowEmbedder(Embedder):
        def embed_queries_with_usage(self, texts):
            assert not current.db.in_transaction()
            embedding.wait()  # All preflights must finish before checking the whole pool.
            assert engine.pool.checkedout() == 0
            time.sleep(0.15)
            assert engine.pool.checkedout() == 0
            embedding.wait()  # Prevent vector checkout until all embedding checks finish.
            return super().embed_queries_with_usage(texts)

    def semantic(db, *args, **kwargs):
        assert db is current.db
        return _vector_rows(db, ids)

    def expand(db, *args, **kwargs):
        assert db is current.db and db.in_transaction()
        current.expanded = True
        return real_expand(db, *args, **kwargs)

    monkeypatch.setattr(search.document_repo, "semantic_search", semantic)
    monkeypatch.setattr(search, "_expand_locally", expand)

    def run():
        with Session(engine) as db:
            current.db, current.expanded = db, False
            begin.wait()
            result = search.search_chunks(
                db, SlowEmbedder(), QUESTION, planned=True, exact_refs=EXACT_REFS, embedding_token_budget=100,
            )
            assert current.expanded and not db.in_transaction()
            return result

    with ThreadPoolExecutor(max_workers=requests) as pool:
        futures = [pool.submit(run) for _ in range(requests)]
        results = [future.result(timeout=15) for future in futures]

    assert engine.pool.checkedout() == 0
    assert all(result.embedding_usage.skipped_reason is None for result in results)
    assert all(result[0]["rank_sources"] == ["vector", "bm25", "exact"] for result in results)


@pytest.mark.parametrize("fail_first", [False, True])
def test_cold_bm25_single_flight_and_retry(corpus, monkeypatch, fail_first):
    engine, ids = corpus
    count = 10
    begin = threading.Barrier(count, timeout=10)
    all_waiting = threading.Event()
    calls_lock = threading.Lock()
    loads = 0
    waiters = 0
    new_build = hybrid._LexicalBuild
    load = hybrid.document_repo.lexical_corpus

    class BuildFailed(RuntimeError):
        pass

    def tracked_build():
        build = new_build()
        wait = build.done.wait

        def tracked_wait():
            nonlocal waiters
            with calls_lock:
                waiters += 1
                if waiters == count - 1:
                    all_waiting.set()
            assert wait(timeout=10), "BM25 builder did not wake its waiters"

        build.done.wait = tracked_wait
        return build

    def corpus_load(*args, **kwargs):
        nonlocal loads
        with calls_lock:
            loads += 1
            attempt = loads
        if attempt == 1:
            assert all_waiting.wait(timeout=10)
            if fail_first:
                raise BuildFailed
        return load(*args, **kwargs)

    monkeypatch.setattr(hybrid, "_LexicalBuild", tracked_build)
    monkeypatch.setattr(hybrid.document_repo, "lexical_corpus", corpus_load)

    def rank():
        begin.wait()
        try:
            with Session(engine) as db:
                lexical, _ = hybrid.independent_rankings(db, QUESTION, MODEL, None, 5)
            return [str(row[0].id) for row in lexical]
        except RuntimeError as exc:
            if isinstance(exc, BuildFailed) or isinstance(exc.__cause__, BuildFailed):
                return None
            raise

    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(rank) for _ in range(count)]
        results = [future.result(timeout=15) for future in futures]

    assert results == ([None] if fail_first else [[ids[0]]]) * count
    assert loads == 1
    with Session(engine) as db:
        assert hybrid.warmup_lexical(db, MODEL).n == 3
    assert loads == 1 + int(fail_first)


def test_corpus_version_invalidates_cached_bm25_even_with_same_fingerprint(corpus, monkeypatch):
    engine, ids = corpus
    monkeypatch.setenv("REDIS_URL", "")
    get_settings.cache_clear()
    with Session(engine) as db:
        original = hybrid.warmup_lexical(db, MODEL)
        db.execute(
            update(DocumentChunk).where(DocumentChunk.id == uuid.UUID(ids[0])).values(content="Văn bản mới")
        )
        db.commit()
        assert hybrid.warmup_lexical(db, MODEL) is original
        corpus_version.bump_corpus_version()
        updated = hybrid.warmup_lexical(db, MODEL)
    assert updated.fingerprint == original.fingerprint
    assert updated.top_k_ids(hybrid.query_terms(QUESTION), 5) == []


@pytest.mark.parametrize("planned", [True, False])
def test_retrieval_matches_baseline(corpus, monkeypatch, planned):
    engine, ids = corpus
    sessions = []

    def semantic(db, *args, **kwargs):
        assert engine.pool.checkedout() == 0
        rows = _vector_rows(db, ids)
        assert engine.pool.checkedout() == 1
        sessions.append(db)
        return rows

    monkeypatch.setattr(search.document_repo, "semantic_search", semantic)
    monkeypatch.setattr(search, "split_question", lambda q: ["biện pháp khác", "thẩm quyền tòa án"])
    with Session(engine) as db:
        result = search.search_chunks(
            db, Embedder(), QUESTION, planned=planned, exact_refs=EXACT_REFS,
            embedding_token_budget=100, request_turn_id="p0-parity",
        )
        assert len(sessions) == (1 if planned else 3)
        assert (sessions[0] is db) == planned
        assert engine.pool.checkedout() == 0

    # Golden output of vector + BM25 + structured exact, then adjacent/citation protection.
    assert [(row["id"], row["rank_sources"], row["rrf_rank"], row["effective_date"]) for row in result] == [
        (ids[0], ["vector", "bm25", "exact"], 1, "2026-10-10"),
        (ids[2], ["vector", "citation"], 2, "2026-10-10"),
        (ids[1], ["adjacent"], 3, "2026-10-10"),
    ]
    assert all(row["document"]["effective_date"] == "2026-10-10" for row in result)
    assert result.embedding_usage.model_dump() == {
        "prompt_tokens": 20, "cached_tokens": 0, "attempts": 1, "cost_nano_usd": 3000,
        "budget_capped": False, "skipped_reason": None, "model": MODEL, "request_turn_id": "p0-parity",
    }
