"""Independent eligible-corpus BM25 + vector + exact metadata lookup, fused by RRF.

Search uses independent_rankings and reciprocal_rank_fusion. Auxiliary reads own
their sessions and share vector visibility/model/type filters. BM25 indexes only
chunk text and document labels, never embeddings or document full text.

The older rerank/get_index helpers remain available for offline comparisons;
HYBRID_ALPHA/HYBRID_POOL apply only to those legacy helpers, not RRF.
"""

import heapq
import logging
import math
import re
import threading
import time
import unicodedata
import weakref
from collections import Counter, OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field
from sys import intern

from sqlalchemy.orm import Session

from app.repositories import document as document_repo

logger = logging.getLogger(__name__)

RRF_K = 60


def reciprocal_rank_fusion(rankings: list[list], k: int = RRF_K) -> list:
    """Fuse independent rankings by ID, with deterministic ties and provenance."""
    scores: dict[str, float] = {}
    rows: dict[str, tuple] = {}
    provenance: dict[str, list[dict]] = {}
    for branch, ranking in enumerate(rankings):
        seen: set[str] = set()
        for rank, row in enumerate(ranking, 1):
            cid = str(row[0].id)
            if cid in seen:
                continue
            seen.add(cid)
            rows.setdefault(cid, row)
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            provenance.setdefault(cid, []).append({"source": ("vector", "lexical", "exact")[branch], "rank": rank})
    out = []
    for cid in sorted(rows, key=lambda cid: -scores[cid]):
        row = ScoredRow(rows[cid], 1.0 - scores[cid])
        row.retrieval_sources = provenance[cid]
        out.append(row)
    return out


def independent_rankings(db: Session, query: str, embedding_model: str,
                         doc_types: list[str] | None, top_k: int) -> tuple[list, list]:
    """BM25 searches the entire eligible corpus, not just vector candidates.

    Uses the cached in-process lexical Bm25Index (with inverted postings list) and
    validates freshness via fingerprint on an independent Session.
    """
    index = _get_lexical_index(db.get_bind(), embedding_model, doc_types)
    terms = query_terms(query)
    ids = index.top_k_ids(terms, top_k)
    numbers = re.findall(r"\b\d{1,4}/\d{4}/[\wĐđ-]+", query, re.I)
    articles = re.findall(r"\bĐiều\s+(\d+[a-zđ]?)\b", query, re.I)
    if not (ids or numbers or articles):
        return [], []
    with Session(bind=db.get_bind(), expire_on_commit=False) as lexical_db:
        lexical = document_repo.retrieval_rows(lexical_db, embedding_model, doc_types, chunk_ids=ids) if ids else []
        exact = document_repo.retrieval_rows(lexical_db, embedding_model, doc_types,
                                             doc_numbers=numbers, articles=articles, limit=top_k) if numbers or articles else []
    by_id = {str(r[0].id): r for r in lexical}
    return [by_id[cid] for cid in ids if cid in by_id], exact

# Hằng số BM25 chuẩn
K1 = 1.2
B = 0.75
# Tối đa bao lâu đối chiếu dấu vân tay với DB một lần (giây)
FINGERPRINT_TTL_SECONDS = 60.0

# Rất ít từ dừng; từ phổ biến khác đã có IDF thấp lo
STOPWORDS = frozenset({"của", "và", "là", "có", "được", "các", "những", "một", "cho", "khi", "thì", "mà"})

# Dấu câu (mọi ký tự không phải chữ/số/khoảng trắng) và gạch dưới ngắt đoạn: bigram không bắc qua chúng
_BOUNDARY = re.compile(r"[^\w\s]|_")


def tokenize(text: str) -> list[str]:
    """Các âm tiết đơn lẻ rồi các bigram ("tách thửa"), có lặp (để đếm tần suất). Từ dừng bị bỏ, bigram không chứa từ dừng
    và không bắc qua dấu câu. Giữ nguyên dấu tiếng Việt."""
    unigrams: list[str] = []
    bigrams: list[str] = []
    for segment in _BOUNDARY.split(unicodedata.normalize("NFC", text).lower()):
        words = segment.split()
        for i, w in enumerate(words):
            if w in STOPWORDS:
                continue
            unigrams.append(intern(w))
            if i + 1 < len(words) and words[i + 1] not in STOPWORDS:
                bigrams.append(intern(f"{w} {words[i + 1]}"))
    return unigrams + bigrams


class Bm25Index:
    """Chỉ mục BM25 trong bộ nhớ, bất biến sau khi dựng. Khoá đoạn là str(id)."""

    def __init__(self, docs: Iterable[tuple[str, str]], fingerprint=None):
        self.fingerprint = fingerprint
        self.tf: dict[str, Counter] = {}
        self.dl: dict[str, int] = {}
        self._cids: list[str] = []
        self._postings: dict[str, list[tuple[int, int]]] = {}
        df: Counter = Counter()
        for idx, (chunk_id, text) in enumerate(docs):
            counts = Counter(tokenize(text))
            self.tf[chunk_id] = counts
            self.dl[chunk_id] = sum(counts.values())
            self._cids.append(chunk_id)
            df.update(counts.keys())
            for term, freq in counts.items():
                self._postings.setdefault(term, []).append((idx, freq))
        self.df: dict[str, int] = dict(df)
        self.n = len(self.tf)
        self.avgdl = (sum(self.dl.values()) / self.n) if self.n else 0.0
        if self.avgdl:
            self._norms = [
                K1 * (1.0 - B + B * self.dl[cid] / self.avgdl) for cid in self._cids
            ]
        else:
            self._norms = [0.0] * self.n
        self._idf_map = {
            term: math.log(1.0 + (self.n - d + 0.5) / (d + 0.5))
            for term, d in self.df.items()
        }

    def idf(self, term: str) -> float:
        """IDF dạng không âm: ln(1 + (N - df + 0.5) / (df + 0.5)). Từ không có trong chỉ mục: df = 0."""
        cached = self._idf_map.get(term)
        if cached is not None:
            return cached
        return math.log(1.0 + (self.n + 0.5) / 0.5)

    def score(self, query_terms: Iterable[str], chunk_id: str) -> float:
        """Điểm BM25 của một đoạn với tập từ truy vấn (mỗi từ tính một lần). Đoạn không có trong chỉ mục: 0."""
        counts = self.tf.get(chunk_id)
        if not counts or not self.avgdl:
            return 0.0
        norm = K1 * (1.0 - B + B * self.dl[chunk_id] / self.avgdl)
        total = 0.0
        for term in query_terms:
            f = counts.get(term)
            if f:
                total += self.idf(term) * f * (K1 + 1.0) / (f + norm)
        return total

    def top_k_ids(self, terms: Iterable[str], top_k: int) -> list[str]:
        """Xếp hạng top_k chunk theo inverted postings list, giữ nguyên thứ tự tie-break (-score, chunk_id)."""
        if top_k <= 0 or not self.avgdl:
            return []
        scores: dict[int, float] = {}
        k1_plus_1 = K1 + 1.0
        norms = self._norms
        for term in dict.fromkeys(terms):
            posting = self._postings.get(term)
            if not posting:
                continue
            idf_k = self._idf_map[term] * k1_plus_1
            for idx, freq in posting:
                scores[idx] = scores.get(idx, 0.0) + idf_k * freq / (freq + norms[idx])
        if not scores:
            return []
        top = heapq.nsmallest(
            top_k,
            ((-sc, self._cids[idx], idx) for idx, sc in scores.items() if sc > 0.0),
        )
        return [self._cids[idx] for _, _, idx in top]

    def __contains__(self, chunk_id: str) -> bool:
        return chunk_id in self.tf


def query_terms(text: str) -> list[str]:
    """Tập từ truy vấn (không lặp), sắp xếp để cộng điểm theo thứ tự cố định."""
    return sorted(set(tokenize(text)))


_lock = threading.Lock()
_index: Bm25Index | None = None
_checked_at = 0.0
_LEXICAL_CACHE_MAX_ENTRIES = 4
_lexical_cache: weakref.WeakKeyDictionary[object, OrderedDict] = weakref.WeakKeyDictionary()


@dataclass
class _LexicalBuild:
    done: threading.Event = field(default_factory=threading.Event)
    index: Bm25Index | None = None
    error: Exception | None = None


_lexical_builds: dict[tuple, _LexicalBuild] = {}


def reset_index() -> None:
    """Xoá chỉ mục trong bộ nhớ (dùng trong test và khi cần ép dựng lại)."""
    global _index, _checked_at
    with _lock:
        _index, _checked_at = None, 0.0
        _lexical_cache.clear()
        _lexical_builds.clear()  # A pre-reset build must not publish into the cleared cache.


def _get_lexical_index(bind, embedding_model: str, doc_types: list[str] | None) -> Bm25Index:
    """Cache Bm25Index theo (bind, embedding_model, doc_types), đối chiếu fingerprint theo TTL."""
    doc_types_key = tuple(sorted(set(doc_types))) if doc_types else None
    cache_key = (embedding_model, doc_types_key)
    build_key = (bind, *cache_key)
    is_sqlite = getattr(getattr(bind, "dialect", None), "name", "") == "sqlite"

    while True:
        with _lock:
            bind_cache = _lexical_cache.get(bind)
            cached = bind_cache.get(cache_key) if bind_cache is not None else None
            cached_index, checked_at = cached if cached is not None else (None, 0.0)
            if cached_index is not None and not is_sqlite and time.monotonic() - checked_at < FINGERPRINT_TTL_SECONDS:
                bind_cache.move_to_end(cache_key)
                return cached_index
            build = _lexical_builds.get(build_key)
            if build is None:
                build = _lexical_builds[build_key] = _LexicalBuild()
                break
        build.done.wait()
        if build.error is not None:
            raise RuntimeError("BM25 index build failed") from build.error
        if build.index is not None:
            return build.index
        # Cache reset may invalidate a successful build before it publishes.

    try:
        with Session(bind=bind, expire_on_commit=False) as lexical_db:
            fingerprint = document_repo.lexical_corpus_fingerprint(lexical_db, embedding_model, doc_types)
            reuse = cached_index is not None and cached_index.fingerprint == fingerprint
            if not reuse:
                started = time.perf_counter()
                corpus = document_repo.lexical_corpus(lexical_db, embedding_model, doc_types)

        if reuse:
            index = cached_index
        else:
            index = Bm25Index(corpus, fingerprint=fingerprint)
            logger.info(
                "rag lexical dựng chỉ mục BM25 đoạn=%d từ_vựng=%d model=%s mất %.2fs",
                index.n, len(index.df), embedding_model, time.perf_counter() - started,
            )
        with _lock:
            if _lexical_builds.get(build_key) is build:
                bind_cache = _lexical_cache.setdefault(bind, OrderedDict())
                bind_cache[cache_key] = (index, time.monotonic())
                bind_cache.move_to_end(cache_key)
                while len(bind_cache) > _LEXICAL_CACHE_MAX_ENTRIES:
                    bind_cache.popitem(last=False)
                build.index = index
        return index
    except Exception as exc:
        build.error = exc
        raise
    finally:
        with _lock:
            if _lexical_builds.get(build_key) is build:
                del _lexical_builds[build_key]
            build.done.set()


def warmup_lexical(db: Session, embedding_model: str) -> Bm25Index:
    """Dựng sẵn chỉ mục lexical BM25 khi rag-service khởi động."""
    return _get_lexical_index(db.get_bind(), embedding_model, None)


def get_index(db: Session, force_check: bool = False) -> Bm25Index:
    """Chỉ mục BM25 hiện hành. Lần đầu: dựng từ DB. Về sau: dấu vân tay (số đoạn active, thời điểm tạo mới nhất) được đối chiếu
    với DB tối đa mỗi FINGERPRINT_TTL_SECONDS giây (force_check bỏ qua hạn đó); khác thì dựng lại, giống thì dùng tiếp."""
    global _index, _checked_at
    with _lock:
        now = time.monotonic()
        if _index is not None and not force_check and now - _checked_at < FINGERPRINT_TTL_SECONDS:
            return _index
        fingerprint = document_repo.active_chunks_fingerprint(db)
        _checked_at = now
        if _index is not None and _index.fingerprint == fingerprint:
            return _index
        started = time.perf_counter()
        rows = document_repo.active_chunk_texts(db)
        _index = Bm25Index(((str(cid), content) for cid, content in rows), fingerprint)
        logger.info(
            "rag hybrid dựng chỉ mục BM25 đoạn=%d từ_vựng=%d mất %.2fs",
            _index.n, len(_index.df), time.perf_counter() - started,
        )
        return _index


class ScoredRow(tuple):
    """Dòng (chunk, văn bản, khoảng_cách_vector) như cũ, kèm `rank_distance` = 1 - điểm hybrid. Phần tử [2] vẫn là khoảng
    cách vector gốc (nên score trả ra ngoài không đổi); `rank_distance` chỉ để xếp hạng, nhỏ hơn thì đứng trước.
    Vì là tuple nên đi qua _interleave / _diversify của search.py mà không đổi gì."""

    def __new__(cls, row, rank_distance: float):
        self = super().__new__(cls, row)
        self.rank_distance = rank_distance
        return self


def rank_distance(row) -> float:
    """Khoá xếp hạng cuối của một dòng: điểm hybrid nếu có, không thì khoảng cách vector (như cũ)."""
    return getattr(row, "rank_distance", row[2])


def rerank(
    rows: list,
    query: str,
    index: Bm25Index,
    alpha: float,
    pool: int = 0,
    trace: list | None = None,
) -> list:
    """Sắp lại `rows` = [(chunk, doc, khoảng_cách_cosine)], đang theo thứ tự vector (gần nhất trước), bằng điểm cuối
    = (1 - khoảng cách) + alpha x BM25 chuẩn hoá. Trả danh sách MỚI cùng đúng các phần tử (không thêm, không bớt).

    pool > 0: chỉ `pool` ứng viên đầu được chấm và sắp lại, phần còn lại giữ nguyên thứ tự và đứng sau.
    BM25 lớn nhất = 0 (không khớp từ nào) hoặc alpha = 0: trả đúng thứ tự cũ. Điểm bằng nhau giữ thứ tự vector.
    trace: nếu truyền một list thì thêm vào đó một dict cho mỗi ứng viên được chấm (xem điểm từng phần); không ảnh hưởng kết quả."""
    head = rows[:pool] if pool and pool > 0 else rows
    tail = rows[len(head):]
    terms = query_terms(query)
    raw = [index.score(terms, str(chunk.id)) for chunk, _doc, _dist in head]
    top = max(raw, default=0.0)
    normalized = [r / top for r in raw] if top > 0 else [0.0] * len(raw)
    final = [(1.0 - dist) + alpha * norm for (_c, _d, dist), norm in zip(head, normalized)]
    order = sorted(range(len(head)), key=lambda i: (-final[i], i)) if top > 0 else list(range(len(head)))
    if trace is not None:
        rank_after = {i: pos for pos, i in enumerate(order, 1)}
        for i, (chunk, doc, dist) in enumerate(head):
            info = chunk.section_info or {}
            trace.append(
                {
                    "id": str(chunk.id),
                    "doc_number": getattr(doc, "doc_number", None),
                    "dieu": info.get("dieu"),
                    "khoan": info.get("khoan"),
                    "diem": info.get("diem"),
                    "vector": 1.0 - dist,
                    "bm25_raw": raw[i],
                    "bm25_norm": normalized[i],
                    "final": final[i],
                    "rank_before": i + 1,
                    "rank_after": rank_after[i],
                }
            )
    if top <= 0:
        return list(head) + list(tail)  # không khớp từ nào: nguyên dòng vector, không gắn điểm hybrid
    # Gắn điểm hybrid vào từng dòng đã chấm để search.py giữ nó qua _interleave/_diversify; phần ngoài pool giữ nguyên
    return [ScoredRow(head[i], 1.0 - final[i]) for i in order] + list(tail)


def rerank_with_db(
    db: Session, rows: list, query: str, alpha: float, pool: int = 0, trace: list | None = None
) -> list:
    """rerank() với chỉ mục lấy từ get_index. Ứng viên chưa có trong chỉ mục (vừa nạp/thay luật) thì đối chiếu DB ngay một lần.
    Lỗi bất kỳ (DB, bộ nhớ...) thì ghi cảnh báo và trả đúng thứ tự vector: hybrid là phần phụ, không được làm hỏng tìm kiếm."""
    if not rows:
        return rows
    try:
        index = get_index(db)
        if any(str(chunk.id) not in index for chunk, _d, _x in rows):
            index = get_index(db, force_check=True)
        return rerank(rows, query, index, alpha, pool, trace)
    except Exception:
        logger.warning("rag hybrid lỗi, dùng thứ tự vector", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return rows
