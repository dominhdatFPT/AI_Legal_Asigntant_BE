"""Tìm kiếm ngữ nghĩa: embedding câu hỏi → tìm chunk gần nhất trong pgvector."""

import asyncio
import logging
import math
import threading
import time
import uuid
from collections import Counter, OrderedDict
from dataclasses import dataclass
from itertools import zip_longest

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_session
from app.repositories import document as document_repo
from app.schemas.search import (
    BatchSearchItem, BatchSearchItemResult, BatchSearchRequest, BatchSearchResponse, EmbeddingUsage, SearchRequest,
)
from app.services import hybrid, legal_refs
from app.services.db_scheduler import BatchDBUnavailable, FairDBScheduler
from app.services.embedding import (
    Embedder, EmbeddingError, budgeted_inputs, embed_batch_async, embed_queries_budgeted, estimate_query_tokens,
)
from app.services.query_expansion import expand
from app.services.query_split import split_question

logger = logging.getLogger(__name__)

# Lấy dư rồi lọc: mỗi Điều giữ tối đa MAX_PER_DIEU đoạn để top_k đoạn bao được nhiều Điều hơn.
# semantic_search đặt hnsw.ef_search = max(64, k * 2) khi k > 20 trên PostgreSQL;
# k là ngân sách sau overfetch, không phải top_k của API.
OVERFETCH = 5
MAX_PER_DIEU = 2

# Mở rộng cục bộ (đọc kho, 0 token embedding): chặn số chunk dùng làm "hạt giống" và số chunk kéo thêm để độ trễ không phình ra
ADJACENT_SEEDS = 3  # chỉ top-3 chunk trúng một Khoản mới kéo Khoản liền kề
MAX_ADJACENT = 6
CITATION_SEEDS = 5
MAX_CITATION_REFS = 8  # dẫn chiếu theo 1 bước, tối đa ngần này tham chiếu mỗi lần search
PROTECTED_SHARE = 0.6  # tối đa 60% top_k dành cho căn cứ exact / liền kề / dẫn chiếu đã kiểm chứng
_SOURCE_NAMES = {"lexical": "bm25"}

_EMBED_CACHE_MAX = 1024
_embed_lock = threading.Lock()
_embed_cache: OrderedDict[tuple[Embedder, str], tuple[float, ...]] = OrderedDict()


def _cache_get(embedder: Embedder, texts: list[str]) -> dict[int, tuple[float, ...]]:
    hit: dict[int, tuple[float, ...]] = {}
    with _embed_lock:
        for i, q in enumerate(texts):
            val = _embed_cache.get((embedder, q))
            if val is not None:
                _embed_cache.move_to_end((embedder, q))
                hit[i] = val
    return hit


def _cache_put(embedder: Embedder, text: str, vec: tuple[float, ...]) -> None:
    # shortcut: cache riêng từng tiến trình, mất khi restart; chuyển sang Redis nếu chạy nhiều bản rag-service
    with _embed_lock:
        _embed_cache[(embedder, text)] = vec
        _embed_cache.move_to_end((embedder, text))
        while len(_embed_cache) > _EMBED_CACHE_MAX:
            _embed_cache.popitem(last=False)


class SearchResults(list):
    """Danh sách chunk (hợp đồng cũ) kèm embedding_usage của chính request này; code cũ vẫn coi nó là list."""

    embedding_usage: EmbeddingUsage | None = None


def _diversify(rows: list, top_k: int) -> list:
    """Ưu tiên đa dạng: mỗi (văn bản, Điều) tối đa MAX_PER_DIEU đoạn, xét theo thứ tự ưu tiên của rows.
    Không đủ top_k thì lấy thêm các đoạn đã bỏ qua (theo thứ tự gần nhất) để không thiếu ngữ cảnh.
    Đoạn không có số Điều (văn bản không chia Điều) thì không giới hạn."""
    seen: Counter = Counter()
    kept, skipped = [], []
    for row in rows:
        chunk, doc, _ = row
        dieu = (chunk.section_info or {}).get("dieu")
        if dieu is not None:
            key = (doc.id, dieu)
            if seen[key] >= MAX_PER_DIEU:
                skipped.append(row)
                continue
            seen[key] += 1
        kept.append(row)
    kept = kept[:top_k] + skipped[: max(0, top_k - len(kept))]
    # Đoạn gần nhất đứng đầu; dòng đã qua hybrid thì xếp theo điểm hybrid (rank_distance), còn lại theo khoảng cách vector
    return sorted(kept, key=hybrid.rank_distance)


def _interleave(row_lists: list[list]) -> list:
    """Xếp xen kẽ kết quả của câu gốc và từng ý (1 đoạn câu gốc, 1 đoạn ý 1, 1 đoạn ý 2...), bỏ đoạn trùng.
    Dùng ngân sách ít nhất bằng số câu tìm trước khi đa dạng hoá để tránh cắt mất ý cuối chỉ vì top_k nhỏ.
    1 danh sách thì giữ nguyên; các đoạn trùng và quota theo Điều vẫn có thể ảnh hưởng độ phủ."""
    seen, out = set(), []
    for group in zip_longest(*row_lists):
        for row in group:
            if row is not None and row[0].id not in seen:
                seen.add(row[0].id)
                out.append(row)
    return out


def _fuse_branch(q: str, rows_q: list, rankings, structured: list, trace: list | None) -> list:
    """Chạy RRF cho 1 sub-query và giữ fallback vector khi nhánh lexical/exact lỗi.
    rankings = (lexical, exact) hoặc Exception đã bắt khi chạy cục bộ; structured = dòng tra cứu (Điều, Khoản) đã kiểm chứng."""
    candidates = [] if trace is not None else None
    out = rows_q
    try:
        if isinstance(rankings, Exception):
            if not structured:
                raise rankings
            logger.warning("Independent hybrid unavailable; using structured lookup only", exc_info=rankings)
            lexical, exact = [], []
        else:
            lexical, exact = rankings
        fused = hybrid.reciprocal_rank_fusion([rows_q, lexical, structured + exact])
        out = fused
        if candidates is not None:
            candidates.extend(
                {"id": str(r[0].id), "sources": r.retrieval_sources, "rrf": 1.0 - r.rank_distance}
                for r in fused
            )
    except Exception:
        logger.warning("Independent hybrid unavailable; preserving vector candidates", exc_info=True)
    if trace is not None:
        trace.append({"query": q, "candidates": candidates})
    return out


def _local_rankings(db, model: str, queries: list[str], doc_types, k: int) -> list:
    """BM25 + exact của từng truy vấn, chạy TRƯỚC embedding (rẻ, không tốn tiền). Lỗi được trả như giá trị, không ném."""

    def run(q_text: str):
        try:
            return hybrid.independent_rankings(db, q_text, model, doc_types, k)
        except Exception as e:
            return e

    # Each independent ranking owns its reads. Run the legacy parts serially so
    # they never compete for a second connection within the same request.
    return [run(q_text) for q_text in queries]


def _structured_lookup(db, model: str, doc_types, raw_queries: list[str], exact_refs):
    """(specs của từng truy vấn, {spec: dòng khớp đúng khoá}). Lỗi DB thì coi như không có tham chiếu."""
    per_query = [legal_refs.parse_query_refs(q, exact_refs if i == 0 else None) for i, q in enumerate(raw_queries)]
    specs = list(dict.fromkeys(s for group in per_query for s in group))
    if not specs:
        return per_query, {}, False
    try:
        return per_query, legal_refs.lookup(db, model, doc_types, specs), False
    except Exception:
        logger.warning("Structured lookup failed; continuing without it", exc_info=True)
        return [[] for _ in raw_queries], {}, True


def _sibling_specs(seed_rows: list) -> tuple[list[legal_refs.RefSpec], list[legal_refs.RefSpec]]:
    """Khoản liền kề của các chunk trúng một Khoản, cùng (văn bản, Điều): (k+1, k+2) rồi (k-1) tách riêng vì ít giá trị hơn."""
    forward: list[legal_refs.RefSpec] = []
    backward: list[legal_refs.RefSpec] = []
    for row in seed_rows:
        info = row[0].section_info or {}
        khoan, dieu, number = str(info.get("khoan") or ""), info.get("dieu"), row[1].doc_number
        if not (number and dieu and khoan.isdigit()):
            continue
        for delta, bucket in ((1, forward), (2, forward), (-1, backward)):
            if int(khoan) + delta >= 1:
                bucket.append(legal_refs.RefSpec(number, str(dieu).lower(), str(int(khoan) + delta)))
    return list(dict.fromkeys(forward)), list(dict.fromkeys(backward))


def _expand_locally(db, model, doc_types, rows, exact_rows, adjacent: bool, citations: bool):
    """Điều/Khoản liền kề và căn cứ được dẫn chiếu (1 bước), đều đọc từ kho: 0 token embedding.
    Mỗi bước dùng đúng 1 lô truy vấn. Trả về (dòng liền kề, dòng dẫn chiếu, dòng Khoản đứng trước)."""
    adjacent_rows: list = []
    earlier_rows: list = []
    cited_rows: list = []
    if adjacent:
        forward, backward = _sibling_specs(exact_rows + rows[:ADJACENT_SEEDS])
        found = legal_refs.lookup(db, model, doc_types, forward + backward, per_ref=2)  # 1 lô cho mọi hạt giống
        adjacent_rows = list(_unique(r for s in forward for r in found.get(s, [])))[:MAX_ADJACENT]
        earlier_rows = list(_unique(r for s in backward for r in found.get(s, [])))[: MAX_ADJACENT - len(adjacent_rows)]
    if citations:
        refs: list[legal_refs.RefSpec] = []
        for row in exact_rows + rows[:CITATION_SEEDS] + adjacent_rows + earlier_rows:  # chỉ 1 bước: dẫn chiếu của chunk được dẫn chiếu không đi tiếp
            info = row[0].section_info or {}
            refs.extend(legal_refs.extract_citations(row[0].content, row[1].doc_number, info.get("dieu")))
        refs = list(dict.fromkeys(refs))[:MAX_CITATION_REFS]
        if refs:
            found = legal_refs.lookup(db, model, doc_types, refs, per_ref=2)
            cited_rows = [r for spec in refs for r in found.get(spec, [])]
    # chunk đã có sẵn trong kết quả vẫn được giữ lại để đánh dấu nguồn và giữ chỗ, nên chỉ loại trùng trong từng nhóm
    return adjacent_rows, list(_unique(cited_rows)), earlier_rows


def _unique(rows):
    ids: set[str] = set()
    for r in rows:
        if str(r[0].id) not in ids:
            ids.add(str(r[0].id))
            yield r


def _protect(rows: list, exact_rows: list, adjacent_rows: list, cited_rows: list, earlier_rows: list, top_k: int) -> tuple[list, dict]:
    """Giữ chỗ trong top_k cho căn cứ khớp chính xác đã kiểm chứng, rồi xen kẽ liền kề / dẫn chiếu, để RRF và giới hạn
    MAX_PER_DIEU không đẩy mất chúng (Khoản đứng trước xếp cuối). Trả về (dòng cuối cùng, {chunk_id: [nguồn]})."""
    by_id = {str(r[0].id): r for r in rows}
    protected: dict[str, tuple] = {}
    tags: dict[str, list[str]] = {}

    def mark(row, tag: str) -> None:
        cid = str(row[0].id)
        if cid not in protected:
            # dòng nằm ngoài RRF: xếp ngay sau nhóm hạng cao của RRF
            fallback = hybrid.ScoredRow(row, 1.0 - 1.0 / (hybrid.RRF_K + 2 * top_k + len(protected)))
            protected[cid] = by_id.get(cid, fallback)
        if tag not in tags.setdefault(cid, []):
            tags[cid].append(tag)

    for r in exact_rows:
        mark(r, "exact")
    for pair in zip_longest(adjacent_rows, cited_rows):
        for r, tag in zip(pair, ("adjacent", "citation")):
            if r is not None:
                mark(r, tag)
    for r in earlier_rows:
        mark(r, "adjacent")
    reserve = max(1, math.ceil(top_k * PROTECTED_SHARE))
    kept = list(protected.items())[:reserve]
    kept_ids = {cid for cid, _ in kept}
    tags = {cid: t for cid, t in tags.items() if cid in kept_ids}
    rest = [r for r in rows if str(r[0].id) not in kept_ids][: max(0, top_k - len(kept))]
    final = [r for _, r in kept] + rest
    final.sort(key=lambda r: ("exact" not in tags.get(str(r[0].id), ()), hybrid.rank_distance(r)))
    return final, tags


@dataclass
class SearchPreflight:
    queries: list[str]
    top_k: int
    rankings: list
    structured: list[list]
    exact_rows: list
    exact_sufficient: bool
    degraded: bool


def _preflight(db, model, query, top_k, doc_types, planned, exact_refs) -> SearchPreflight:
    settings = get_settings()
    raw_queries = [query]
    if settings.query_split_enabled and not planned:
        parts = split_question(query)
        if len(parts) > 1:
            # Câu gốc đứng đầu để giữ ngữ cảnh chung; mỗi ý tìm thêm căn cứ riêng (xem query_split.py)
            raw_queries += parts
    queries = [expand(q) for q in raw_queries] if settings.query_expansion_enabled else list(raw_queries)
    effective_top_k = max(top_k, len(queries)) if len(queries) > 1 else top_k
    overfetch_k = effective_top_k * OVERFETCH
    rankings = (
        _local_rankings(db, model, queries, doc_types, overfetch_k)
        if settings.hybrid_enabled else [None] * len(queries)
    )
    structured = [[] for _ in queries]
    exact_sufficient = False
    degraded = any(isinstance(ranking, Exception) for ranking in rankings)
    if db is not None and settings.hybrid_enabled:
        specs_by_query, found, lookup_failed = _structured_lookup(db, model, doc_types, raw_queries, exact_refs)
        # Every field used in ranking and output is loaded before the session closes.
        structured = [[row for spec in specs for row in found.get(spec, [])] for specs in specs_by_query]
        exact_sufficient = all(
            specs and legal_refs.is_pure_reference(q) and all(found.get(spec) for spec in specs)
            for q, specs in zip(raw_queries, specs_by_query)
        )
        degraded |= lookup_failed
    exact_rows = list(_unique(row for group in structured for row in group))
    return SearchPreflight(queries, effective_top_k, rankings, structured, exact_rows, exact_sufficient, degraded)


@dataclass
class RankedSearch:
    rows: list
    tags: dict
    degraded: bool
    vector_seconds: float
    expand_seconds: float
    hybrid_seconds: float | None


def _rank_search(
    db, model, doc_types, preflight, vectors, expand_adjacent_clauses, follow_citations, *, trace=None, split_bind=None
) -> RankedSearch:
    settings = get_settings()
    queries, effective_top_k = preflight.queries, preflight.top_k
    overfetch_k = effective_top_k * OVERFETCH
    rankings, structured, exact_rows = preflight.rankings, preflight.structured, preflight.exact_rows
    local = db is not None and settings.hybrid_enabled
    degraded = preflight.degraded
    hybrid_seconds = None

    # 3. Vector (chỉ cho truy vấn đã có vector), rồi hợp nhất RRF với BM25 + exact
    def _semantic(session, vec):
        return document_repo.semantic_search(session, list(vec), overfetch_k, embedding_model=model, doc_types=doc_types)

    todo = [i for i, v in enumerate(vectors) if v is not None]
    row_lists: list[list] = [[] for _ in queries]
    expand_seconds = 0.0
    vector_started = time.perf_counter()
    if split_bind is not None and len(todo) > 1:
        # Legacy split: the parent has no checkout until expansion. Close each
        # branch before starting the next so one request uses at most one connection.
        for i in todo:
            with Session(bind=split_bind, expire_on_commit=False) as vec_db:
                row_lists[i] = _semantic(vec_db, vectors[i])
    else:
        for i in todo:
            row_lists[i] = _semantic(db, vectors[i])
    vector_seconds = time.perf_counter() - vector_started if todo else 0.0
    if settings.hybrid_enabled:
        hybrid_started = time.perf_counter()
        row_lists = [
            _fuse_branch(q_text, rows_q, ranking, struct, trace)
            for q_text, rows_q, ranking, struct in zip(queries, row_lists, rankings, structured)
        ]
        hybrid_seconds = time.perf_counter() - hybrid_started

    rows = _diversify(_interleave(row_lists), effective_top_k)

    # Điều/Khoản liền kề và dẫn chiếu dùng cùng session với vector ở nhánh một vector.
    tags: dict[str, list[str]] = {}
    if local and (exact_rows or (rows and (expand_adjacent_clauses or follow_citations))):
        expand_started = time.perf_counter()
        try:
            adjacent_rows, cited_rows, earlier_rows = _expand_locally(
                db, model, doc_types, rows, exact_rows, expand_adjacent_clauses, follow_citations
            )
            if exact_rows or adjacent_rows or cited_rows or earlier_rows:
                rows, tags = _protect(rows, exact_rows, adjacent_rows, cited_rows, earlier_rows, effective_top_k)
        except Exception:
            degraded = True
            logger.warning("Adjacent/citation expansion failed; returning ranked rows", exc_info=True)
        finally:
            expand_seconds = time.perf_counter() - expand_started

    if trace is not None and settings.hybrid_enabled:
        # Thứ tự cuối cùng (sau xen kẽ / đa dạng hoá) kèm điểm vector và điểm hybrid, để --explain thấy được
        trace.append(
            {
                "final": [
                    {
                        "rank": rank,
                        "id": str(row[0].id),
                        "doc_number": getattr(row[1], "doc_number", None),
                        "dieu": (row[0].section_info or {}).get("dieu"),
                        "khoan": (row[0].section_info or {}).get("khoan"),
                        "vector": 1.0 - row[2],
                        "final": 1.0 - hybrid.rank_distance(row),
                    }
                    for rank, row in enumerate(rows, 1)
                ]
            }
        )
    return RankedSearch(rows, tags, degraded, vector_seconds, expand_seconds, hybrid_seconds)


def _serialize_chunks(rows, tags, usage: EmbeddingUsage) -> SearchResults:
    results = SearchResults()
    results.embedding_usage = usage
    for pos, row in enumerate(rows, 1):
        chunk, doc, distance = row
        # khoảng cách cosine 0–2 → độ tương đồng 1–(-1); kẹp về 0–1 theo hợp đồng
        score = max(0.0, min(1.0, 1.0 - distance))
        doc_meta = {
            "id": str(doc.id),
            "title": doc.title,
            "doc_number": doc.doc_number,
            "doc_type": doc.doc_type,
        }
        effective_date = getattr(doc, "effective_date", None)
        if effective_date is not None:
            doc_meta["effective_date"] = (
                effective_date.isoformat() if hasattr(effective_date, "isoformat") else str(effective_date)
            )
        item = {
            "id": str(chunk.id),
            "content": chunk.content,
            "score": round(score, 4),
            "section_info": chunk.section_info,
            "document": doc_meta,
        }
        if hasattr(row, "retrieval_sources"):
            item["retrieval_sources"] = row.retrieval_sources
        cid = str(chunk.id)
        if hasattr(row, "retrieval_sources") or cid in tags:
            sources: list[str] = []
            for part in getattr(row, "retrieval_sources", None) or []:
                name = _SOURCE_NAMES.get(part["source"], part["source"])
                if name not in sources:
                    sources.append(name)
            sources += [t for t in tags.get(cid, []) if t not in sources]
            item["rank_sources"] = sources
            item["rrf_rank"] = pos
            if "effective_date" in doc_meta:
                item["effective_date"] = doc_meta["effective_date"]
        results.append(item)
    return results


def search_chunks(
    db: Session,
    embedder: Embedder,
    query: str,
    top_k: int = 5,
    doc_types: list[str] | None = None,
    trace: list | None = None,
    planned: bool = False,
    embedding_token_budget: int | None = None,
    request_turn_id: str | None = None,
    exact_refs: list[dict] | None = None,
    expand_adjacent_clauses: bool = True,
    follow_citations: bool = True,
) -> SearchResults:
    """Trả về danh sách chunk đúng hợp đồng ChunkSearchResult trong openapi.yaml (kèm .embedding_usage).
    trace: list tuỳ chọn để xem chi tiết hybrid (mỗi câu tìm một dict {query, candidates}); chỉ ghi thêm vào đó, kết quả không đổi.
    embedding_token_budget: trần token embedding online SAU mở rộng truy vấn (0 = chỉ exact/BM25; None = mặc định cấu hình)."""
    started = time.perf_counter()
    settings = get_settings()
    bind = db.get_bind() if db is not None and hasattr(db, "get_bind") else None
    release_after_phase = isinstance(bind, Engine) and not (
        bind.dialect.name == "sqlite"
        and (bind.url.database in (None, "", ":memory:", "file::memory:") or bind.url.query.get("mode") == "memory")
    )
    model = embedder.model_name
    preflight_started = time.perf_counter()
    if release_after_phase and db.in_transaction():
        db.close()  # Release an existing checkout before the independent BM25 reads.
    try:
        preflight = _preflight(db, model, query, top_k, doc_types, planned, exact_refs)
    finally:
        if release_after_phase:
            db.close()
    preflight_seconds = time.perf_counter() - preflight_started
    queries = preflight.queries

    # 2. Quyết định có mua embedding hay không, rồi embedding trong ngân sách
    budget = settings.embedding_default_token_budget if embedding_token_budget is None else embedding_token_budget
    vectors: list[tuple[float, ...] | None] = [None] * len(queries)
    cached = _cache_get(embedder, queries)
    for i, vec in cached.items():
        vectors[i] = vec
    missing = [i for i in range(len(queries)) if i not in cached]
    cached_tokens = sum(estimate_query_tokens(queries[i]) for i in cached)
    embed_error: Exception | None = None
    embed_http_seconds = 0.0
    if not missing:
        usage = EmbeddingUsage(model=model, request_turn_id=request_turn_id, cached_tokens=cached_tokens)
    elif budget > 0 and preflight.exact_sufficient:
        usage = EmbeddingUsage(
            model=model, request_turn_id=request_turn_id, cached_tokens=cached_tokens, skipped_reason="exact_sufficient"
        )
    else:
        outcome = embed_queries_budgeted(
            embedder, [queries[i] for i in missing], budget,
            request_turn_id=request_turn_id, cached_tokens=cached_tokens,
        )
        embed_http_seconds = outcome.provider_seconds
        usage, embed_error = outcome.usage, outcome.error
        for i, vec, truncated in zip(missing, outcome.vectors, outcome.truncated):
            vectors[i] = vec
            if vec is not None and not truncated:  # vector của câu bị cắt không ứng với câu gốc nên không cache
                _cache_put(embedder, queries[i], vec)
    embedded = time.perf_counter()

    try:
        ranked = _rank_search(
            db, model, doc_types, preflight, vectors, expand_adjacent_clauses, follow_citations,
            trace=trace, split_bind=bind if release_after_phase else None,
        )
    finally:
        if release_after_phase:
            db.close()

    if embed_error is not None and not ranked.rows:
        raise embed_error

    # Đo từng bước để biết chậm ở Gemini embedding hay ở Supabase; parts = số lần tìm (câu gốc + các ý)
    logger.info(
        "rag timing embed=%.2fs db=%.2fs results=%d parts=%d query=%r "
        "preflight_ms=%.1f embed_http_ms=%.1f vector_ms=%.1f expand_ms=%.1f",
        embedded - started, max(0.0, time.perf_counter() - embedded), len(ranked.rows), len(queries),
        queries[0][:200], preflight_seconds * 1000, embed_http_seconds * 1000,
        ranked.vector_seconds * 1000, ranked.expand_seconds * 1000,
    )
    logger.info(
        "rag embedding_usage turn=%s tokens=%d cached=%d attempts=%d cost_nano=%d capped=%s skipped=%s",
        request_turn_id, usage.prompt_tokens, usage.cached_tokens, usage.attempts, usage.cost_nano_usd,
        usage.budget_capped, usage.skipped_reason,
    )
    if ranked.hybrid_seconds is not None:
        logger.info("rag hybrid RRF %.3fs", ranked.hybrid_seconds)
    return _serialize_chunks(ranked.rows, ranked.tags, usage)


_limiter_key = None
_limiter: FairDBScheduler | None = None


def _batch_db_limiter() -> FairDBScheduler:
    global _limiter, _limiter_key
    settings = get_settings()
    options = (
        settings.rag_db_concurrency_limit, settings.rag_db_queue_max_waiting,
        settings.rag_db_queue_wait_seconds, settings.rag_db_interactive_burst,
        settings.rag_db_per_tenant_burst,
    )
    key = (asyncio.get_running_loop(), options)
    if _limiter is None or _limiter_key != key:
        _limiter, _limiter_key = FairDBScheduler(*options), key
    return _limiter


async def run_search_db(scheduler, deadline, tenant_key, priority, operation, *args, session_factory=None):
    def work():
        with (session_factory or get_session)() as db:
            return operation(db, *args)

    return await scheduler.run(deadline, work, tenant_key=tenant_key, priority=priority)


async def _batch_phase(items, parallelism, operation):
    """Keep only a few pending jobs per request and put results back in input order."""
    pending = iter(enumerate(items))
    results = [None] * len(items)

    async def worker():
        for index, item in pending:
            results[index] = await operation(item)

    async with asyncio.TaskGroup() as tasks:
        for _ in range(min(len(items), parallelism)):
            tasks.create_task(worker())
    return results


@dataclass
class BatchSearchWork:
    preflight: SearchPreflight
    usage: EmbeddingUsage
    cached: dict[int, tuple[float, ...]]
    inputs: dict[str, str]


def _plan_batch_item(item, preflight, embedder, request_turn_id) -> BatchSearchWork:
    queries = preflight.queries
    cached = _cache_get(embedder, queries)
    usage = EmbeddingUsage(
        model=embedder.model_name,
        request_turn_id=request_turn_id,
        cached_tokens=sum(estimate_query_tokens(queries[i]) for i in cached),
    )
    budget = item.embedding_token_budget
    if budget is None:
        budget = get_settings().embedding_default_token_budget
    missing = [q for i, q in enumerate(queries) if i not in cached]
    if missing and budget > 0 and preflight.exact_sufficient:
        usage.skipped_reason = "exact_sufficient"
        return BatchSearchWork(preflight, usage, cached, {})
    return BatchSearchWork(preflight, usage, cached, budgeted_inputs(missing, budget, usage))


def _finish_batch_item(db, model, item, work, vectors) -> RankedSearch:
    return _rank_search(
        db, model, item.doc_types, work.preflight, vectors, item.expand_adjacent_clauses, item.follow_citations,
    )


def _search_vectors(work, embedded):
    return [
        work.cached.get(i) if query not in work.inputs else embedded.get(work.inputs[query])
        for i, query in enumerate(work.preflight.queries)
    ]


async def search_single(
    body: SearchRequest, embedder: Embedder, *, tenant_key: str, priority: str, session_factory=None,
) -> SearchResults:
    scheduler = _batch_db_limiter()
    deadline = time.monotonic() + (body.deadline_ms or 90000) / 1000
    model = embedder.model_name
    started = time.perf_counter()
    preflight = await run_search_db(
        scheduler, deadline, tenant_key, priority, _preflight, model,
        body.query, body.top_k, body.doc_types, body.planned, body.exact_refs,
        session_factory=session_factory,
    )
    preflight_done = time.perf_counter()
    work = _plan_batch_item(body, preflight, embedder, body.request_turn_id)
    embedded = await embed_batch_async(embedder, list(dict.fromkeys(work.inputs.values())), deadline, work.usage)
    for query, sent in work.inputs.items():
        if query == sent and sent in embedded:
            _cache_put(embedder, query, embedded[sent])
    embedding_done = time.perf_counter()
    try:
        ranked = await run_search_db(
            scheduler, deadline, tenant_key, priority, _finish_batch_item, model, body, work,
            _search_vectors(work, embedded), session_factory=session_factory,
        )
    except BatchDBUnavailable as exc:
        if exc.code == "RAG_OVERLOADED":
            # A caller must settle its reservation once embedding may have run.
            raise BatchDBUnavailable("RAG_RETRIEVAL_OVERLOADED") from exc
        raise
    if work.usage.skipped_reason == "embedding_failed" and not ranked.rows:
        raise EmbeddingError("Online embedding failed")
    logger.info(
        "rag timing embed=%.2fs db=%.2fs results=%d parts=%d query=%r "
        "preflight_ms=%.1f embed_http_ms=%.1f vector_ms=%.1f expand_ms=%.1f",
        embedding_done - started, time.perf_counter() - embedding_done, len(ranked.rows), len(preflight.queries),
        preflight.queries[0][:200], (preflight_done - started) * 1000, (embedding_done - preflight_done) * 1000,
        ranked.vector_seconds * 1000, ranked.expand_seconds * 1000,
    )
    usage = work.usage
    logger.info(
        "rag embedding_usage turn=%s tokens=%d cached=%d attempts=%d cost_nano=%d capped=%s skipped=%s",
        usage.request_turn_id, usage.prompt_tokens, usage.cached_tokens, usage.attempts, usage.cost_nano_usd,
        usage.budget_capped, usage.skipped_reason,
    )
    if ranked.hybrid_seconds is not None:
        logger.info("rag hybrid RRF %.3fs", ranked.hybrid_seconds)
    return _serialize_chunks(ranked.rows, ranked.tags, usage)


def _batch_db_error(item: BatchSearchItem, exc: Exception, usage=None) -> BatchSearchItemResult:
    if isinstance(exc, BatchDBUnavailable):
        return BatchSearchItemResult(item_id=item.item_id, status="overloaded", error_code=exc.code, embedding_usage=usage)
    logger.warning("Batch DB work failed (%s)", type(exc).__name__)
    return BatchSearchItemResult(
        item_id=item.item_id, status="error", error_code="RAG_DATABASE_UNAVAILABLE", embedding_usage=usage,
    )


async def search_batch(
    body: BatchSearchRequest, embedder: Embedder, *, tenant_key: str | None = None,
) -> BatchSearchResponse:
    scheduler = _batch_db_limiter()
    if scheduler.saturated():
        # Only this pre-dispatch rejection is returned as an HTTP 503.
        raise BatchDBUnavailable("RAG_OVERLOADED")
    deadline = time.monotonic() + (body.deadline_ms or 90000) / 1000
    tenant_key = tenant_key or body.fairness_key or body.request_turn_id or uuid.uuid4().hex
    model = embedder.model_name
    usage = EmbeddingUsage(model=model, request_turn_id=body.request_turn_id)

    async def prepare(item):
        try:
            preflight = await run_search_db(
                scheduler, deadline, tenant_key, "batch", _preflight, model,
                item.query, item.top_k, item.doc_types, item.planned, item.exact_refs,
            )
        except Exception as exc:
            return _batch_db_error(item, exc)
        return _plan_batch_item(item, preflight, embedder, body.request_turn_id)

    work_items = await _batch_phase(body.items, scheduler.phase_parallelism, prepare)
    unique_inputs: dict[str, None] = {}
    cacheable_inputs: dict[str, None] = {}
    for work in work_items:
        if isinstance(work, BatchSearchItemResult):
            continue
        usage.cached_tokens += work.usage.cached_tokens
        usage.budget_capped |= work.usage.budget_capped
        unique_inputs.update(dict.fromkeys(work.inputs.values()))
        cacheable_inputs.update(dict.fromkeys(query for query, sent in work.inputs.items() if query == sent))

    # No batch DB worker/session remains checked out by this request while it awaits embedding.
    embedded = await embed_batch_async(embedder, list(unique_inputs), deadline, usage)
    for query in cacheable_inputs:
        if query in embedded:
            _cache_put(embedder, query, embedded[query])

    async def finish(pair):
        item, work = pair
        if isinstance(work, BatchSearchItemResult):
            return work
        if any(sent not in embedded for sent in work.inputs.values()) and usage.skipped_reason == "embedding_failed":
            work.usage.skipped_reason = usage.skipped_reason
            work.usage.usage_uncertain = usage.usage_uncertain
        try:
            ranked = await run_search_db(
                scheduler, deadline, tenant_key, "batch", _finish_batch_item, model, item, work,
                _search_vectors(work, embedded),
            )
        except Exception as exc:
            return _batch_db_error(item, exc, work.usage)
        error_code = None
        if work.usage.skipped_reason == "embedding_failed":
            error_code = "EMBEDDING_SERVICE_UNAVAILABLE"
        elif ranked.degraded:
            error_code = "RAG_DATABASE_UNAVAILABLE"
        elif work.usage.budget_capped or work.usage.skipped_reason == "budget_zero":
            error_code = "EMBEDDING_BUDGET_LIMIT"
        return BatchSearchItemResult(
            item_id=item.item_id, status="degraded" if error_code else "ok", error_code=error_code,
            chunks=_serialize_chunks(ranked.rows, ranked.tags, work.usage), embedding_usage=work.usage,
        )

    results = await _batch_phase(list(zip(body.items, work_items)), scheduler.phase_parallelism, finish)
    logger.info(
        "rag batch turn=%s items=%d attempts=%d tokens=%d cached=%d cost_nano=%d uncertain=%s",
        body.request_turn_id, len(results), usage.attempts, usage.prompt_tokens, usage.cached_tokens,
        usage.cost_nano_usd, usage.usage_uncertain,
    )
    return BatchSearchResponse(items=results, embedding_usage=usage)

