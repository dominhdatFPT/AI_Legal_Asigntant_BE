"""Tìm kiếm ngữ nghĩa: embedding câu hỏi → tìm chunk gần nhất trong pgvector."""

import logging
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from itertools import zip_longest

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.repositories import document as document_repo
from app.services.embedding import Embedder
from app.services.query_expansion import expand
from app.services.query_split import split_question

logger = logging.getLogger(__name__)

# Lấy dư rồi lọc: mỗi Điều giữ tối đa MAX_PER_DIEU đoạn để top_k đoạn bao được nhiều Điều hơn.
# top_k x OVERFETCH nên <= 40 (hnsw.ef_search mặc định của pgvector), quá thì pgvector chỉ trả ~40 đoạn.
OVERFETCH = 5
MAX_PER_DIEU = 2


@lru_cache(maxsize=1024)
def _embed_query_cached(embedder: Embedder, text: str) -> tuple[float, ...]:
    # shortcut: cache riêng từng tiến trình, mất khi restart; chuyển sang Redis nếu chạy nhiều bản rag-service
    return tuple(embedder.embed_query(text))


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
    return sorted(kept, key=lambda r: r[2])  # đoạn gần nhất đứng đầu


def _interleave(row_lists: list[list]) -> list:
    """Xếp xen kẽ kết quả của câu gốc và từng ý (1 đoạn câu gốc, 1 đoạn ý 1, 1 đoạn ý 2...), bỏ đoạn trùng.
    Nhờ vậy khi _diversify cắt còn top_k đoạn, ý nào cũng có đoạn gần nhất của nó. 1 danh sách thì giữ nguyên."""
    seen, out = set(), []
    for group in zip_longest(*row_lists):
        for row in group:
            if row is not None and row[0].id not in seen:
                seen.add(row[0].id)
                out.append(row)
    return out


def search_chunks(
    db: Session,
    embedder: Embedder,
    query: str,
    top_k: int = 5,
    doc_types: list[str] | None = None,
) -> list[dict]:
    """Trả về danh sách chunk đúng hợp đồng ChunkSearchResult trong openapi.yaml."""
    started = time.perf_counter()
    settings = get_settings()
    queries = [query]
    if settings.query_split_enabled:
        parts = split_question(query)
        if len(parts) > 1:
            # Câu gốc đứng đầu để giữ ngữ cảnh chung; mỗi ý tìm thêm căn cứ riêng (xem query_split.py)
            queries += parts
    if settings.query_expansion_enabled:
        queries = [expand(q) for q in queries]
    if len(queries) == 1:
        vectors = [_embed_query_cached(embedder, queries[0])]
    else:
        # Gọi Gemini song song để câu nhiều ý không chậm gấp nhiều lần (agent-service chỉ chờ rag 10 giây)
        with ThreadPoolExecutor(max_workers=len(queries)) as pool:
            vectors = list(pool.map(lambda q: _embed_query_cached(embedder, q), queries))
    embedded = time.perf_counter()
    row_lists = [
        document_repo.semantic_search(
            db, list(vec), top_k * OVERFETCH, embedding_model=embedder.model_name, doc_types=doc_types
        )
        for vec in vectors
    ]
    rows = _diversify(_interleave(row_lists), top_k)
    # Đo từng bước để biết chậm ở Gemini embedding hay ở Supabase; parts = số lần tìm (câu gốc + các ý)
    logger.info(
        "rag timing embed=%.2fs db=%.2fs results=%d parts=%d query=%r",
        embedded - started, time.perf_counter() - embedded, len(rows), len(queries), queries[0][:200],
    )
    results = []
    for chunk, doc, distance in rows:
        # khoảng cách cosine 0–2 → độ tương đồng 1–(-1); kẹp về 0–1 theo hợp đồng
        score = max(0.0, min(1.0, 1.0 - distance))
        results.append(
            {
                "id": str(chunk.id),
                "content": chunk.content,
                "score": round(score, 4),
                "section_info": chunk.section_info,
                "document": {
                    "id": str(doc.id),
                    "title": doc.title,
                    "doc_number": doc.doc_number,
                    "doc_type": doc.doc_type,
                },
            }
        )
    return results
