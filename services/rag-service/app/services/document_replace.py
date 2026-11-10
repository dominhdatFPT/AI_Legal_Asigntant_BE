"""Thay nội dung một văn bản ĐÃ active bằng bản mới (luật được sửa đổi), tiết kiệm quota embedding.

Hai phần:
- Hàm thuần (không đụng DB, không gọi embedding): so khớp chunk cũ và mới để biết chunk nào dùng lại được vector cũ
  (`reuse_plan`, `fill_vectors`) và dựng kết quả xem trước (`build_replace_preview`).
- Việc nền `replace_document_background`: ba giai đoạn đọc / embed / ghi như `document_approval.approve_document_background`.
  Văn bản vẫn active và chunk cũ vẫn phục vụ tìm kiếm cho tới giai đoạn ghi, rồi đổi sang bản mới trong MỘT giao dịch.

Quy tắc khớp: hai chunk dùng lại được vector khi CÙNG chuỗi `embedding_text(title, chunk)` và chunk cũ có CÙNG
`embedding_model` với model đang dùng; khớp theo đa tập (một chunk cũ chỉ khớp một chunk mới).
"""

import hashlib
import logging
import math
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import text as sql_text

from app.core.config import get_settings
from app.models import LegalDocument
from app.repositories import document as document_repo
from app.services import document_approval as approval
from app.services import ingest_log
from app.services.chunking import Chunk, chunk_legal_text, embedding_text, normalize_text
from app.services.document_approval import DocumentChangedError
from app.services.document_preview import _article_number, analyze_document
from app.services.embedding import EmbeddingError, get_embedder
from app.services.ingest import EmptyDocumentError

logger = logging.getLogger(__name__)

MAX_CHANGED_ARTICLES = 50  # số Điều đổi tối đa liệt kê khi xem trước
LENGTH_CHANGE_WARN_RATIO = 0.3  # độ dài mới chênh quá 30% so với cũ thì cảnh báo


# ── Hàm thuần ──────────────────────────────────────────────────────────────────

def same_content(old: str, new: str) -> bool:
    """Hai bản giống nhau sau khi chuẩn hóa chữ (cắt ra đúng các chunk như nhau nên không có gì để thay)."""
    return normalize_text(old) == normalize_text(new)


def reuse_plan(old: list[tuple[str, str]], new_texts: list[str], model: str) -> list[int | None]:
    """Với mỗi chunk mới, chỉ số của chunk cũ được dùng lại vector, hoặc None nếu phải embed.
    old: [(chuỗi embedding_text, embedding_model)] của chunk cũ theo thứ tự; new_texts: chuỗi embedding_text của chunk mới.
    Chỉ chunk cũ có embedding_model == model mới khớp được; đa tập: mỗi chunk cũ chỉ khớp một lần, theo thứ tự xuất hiện."""
    pool: dict[str, deque[int]] = defaultdict(deque)
    for i, (text, old_model) in enumerate(old):
        if old_model == model:
            pool[text].append(i)
    return [pool[t].popleft() if pool.get(t) else None for t in new_texts]


def fill_vectors(
    plan: list[int | None],
    old_vectors: list,
    new_texts: list[str],
    embed: Callable[[list[str]], list],
) -> tuple[list, int, int]:
    """Vector cho từng chunk mới, đúng thứ tự: dùng lại vector cũ theo `plan`, phần còn lại gọi `embed` MỘT lần (không có chunk
    nào cần embed thì không gọi). Trả về (vectors, số dùng lại, số đã embed). `embed` trả sai số vector thì ném EmbeddingError."""
    vectors: list = [old_vectors[i] if i is not None else None for i in plan]
    todo = [k for k, i in enumerate(plan) if i is None]
    if todo:
        embedded = embed([new_texts[k] for k in todo])
        if len(embedded) != len(todo):
            raise EmbeddingError(f"Embedding trả {len(embedded)} vector cho {len(todo)} đoạn")
        for k, vector in zip(todo, embedded):
            vectors[k] = vector
    return vectors, len(plan) - len(todo), len(todo)


def _article_sort_key(dieu: str) -> tuple[int, str]:
    try:
        return _article_number(dieu), dieu.lower()
    except (AttributeError, ValueError):
        return 10**9, dieu.lower()


def _distinct_articles(infos) -> set[str]:
    return {i["dieu"].lower() for i in infos if i and i.get("dieu")}


def compare_chunks(
    title: str,
    old_chunks: list[tuple[str, dict | None, str]],
    new_chunks: list[Chunk],
    model: str,
    batch_size: int | None = None,
) -> dict:
    """So chunk cũ (content, section_info, embedding_model) với chunk mới. Cùng quy tắc khớp với việc thay thật."""
    batch_size = batch_size or get_settings().embedding_batch_size
    old_texts = [(embedding_text(title, Chunk(content, info or {})), m) for content, info, m in old_chunks]
    new_texts = [embedding_text(title, c) for c in new_chunks]
    plan = reuse_plan(old_texts, new_texts, model)
    matched = {i for i in plan if i is not None}

    changed: dict[str, str] = {}  # khóa chữ thường -> cách viết gặp đầu tiên
    for c, i in zip(new_chunks, plan):
        if i is None and c.section_info.get("dieu"):
            changed.setdefault(c.section_info["dieu"].lower(), c.section_info["dieu"])
    for i, (_, info, _) in enumerate(old_chunks):
        if i not in matched and info and info.get("dieu"):
            changed.setdefault(info["dieu"].lower(), info["dieu"])
    ordered = sorted(changed.values(), key=_article_sort_key)

    to_embed = [t for t, i in zip(new_texts, plan) if i is None]
    return {
        "old_chunk_count": len(old_chunks),
        "new_chunk_count": len(new_chunks),
        "reused_chunk_count": len(new_chunks) - len(to_embed),
        "embed_chunk_count": len(to_embed),
        "removed_chunk_count": len(old_chunks) - len(matched),
        "changed_articles": ordered[:MAX_CHANGED_ARTICLES],
        "changed_article_count": len(ordered),
        "changed_articles_truncated": len(ordered) > MAX_CHANGED_ARTICLES,
        "embed_estimate": {
            "total_chars": sum(len(t) for t in to_embed),
            "batch_size": batch_size,
            "batch_count": math.ceil(len(to_embed) / batch_size),
        },
    }


def replace_warnings(
    old_content: str,
    new_content: str,
    old_article_count: int,
    new_article_count: int,
    comparison: dict,
) -> list[str]:
    """Cảnh báo riêng cho việc thay (tiếng Việt ngắn)."""
    warnings: list[str] = []
    if new_article_count < old_article_count:
        warnings.append(f"Số Điều mới ({new_article_count}) ít hơn số Điều cũ ({old_article_count}).")
    old_len, new_len = len(normalize_text(old_content)), len(normalize_text(new_content))
    if old_len and abs(new_len - old_len) / old_len > LENGTH_CHANGE_WARN_RATIO:
        pct = round(abs(new_len - old_len) / old_len * 100)
        warnings.append(f"Độ dài mới chênh {pct}% so với bản cũ ({old_len:,} -> {new_len:,} ký tự).")
    if comparison["old_chunk_count"] and comparison["new_chunk_count"] and comparison["reused_chunk_count"] == 0:
        warnings.append(
            f"Không có đoạn nào dùng lại được vector cũ (văn bản đổi hoàn toàn hoặc đổi model embedding), "
            f"phải embed lại cả {comparison['new_chunk_count']} đoạn."
        )
    if same_content(old_content, new_content):
        warnings.append("Nội dung mới giống hệt nội dung hiện tại, không có gì để thay.")
    return warnings


def build_replace_preview(
    title: str,
    old_content: str,
    old_chunks: list[tuple[str, dict | None, str]],
    new_content: str,
    model: str,
) -> dict:
    """Kết quả xem trước: toàn bộ `analyze_document` cho nội dung mới, cộng `comparison` (so chunk) và `replace_warnings`.
    Ném document_preview.EmptyContentError nếu nội dung mới rỗng sau khi chuẩn hóa."""
    analysis = analyze_document(new_content, title)
    comparison = compare_chunks(title, old_chunks, chunk_legal_text(new_content), model)
    old_articles = len(_distinct_articles(info for _, info, _ in old_chunks))
    warnings = replace_warnings(old_content, new_content, old_articles, analysis["article_count"], comparison)
    return {**analysis, "comparison": comparison, "replace_warnings": warnings}


# ── Việc nền ───────────────────────────────────────────────────────────────────

def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def replace_document_background(
    document_id: uuid.UUID,
    new_content: str,
    actor_user_id: str | None = None,
    actor_role: str | None = None,
) -> None:
    """Việc nền của POST /{id}/replace (văn bản đã được 'chiếm chỗ' = ingest_status processing, status vẫn active). Ba giai đoạn:
    1. ĐỌC: lấy title, độ dài và băm của nội dung cũ, toàn bộ chunk cũ (kèm vector) rồi kết thúc giao dịch.
    2. EMBED (không giữ giao dịch): cắt nội dung mới, chunk khớp chunk cũ thì dùng lại vector, chỉ phần còn lại gọi embedder.
    3. GHI một giao dịch ngắn: trên PostgreSQL `SET LOCAL statement_timeout = 0`, kiểm tra nội dung hiện tại vẫn là bản đã đọc
       (lệch thì không ghi gì), lưu toàn văn cũ vào document_versions, thay chunk, đổi content, ingest_status done, commit một lần.
    Lỗi ở giai đoạn nào cũng: rollback, ingest_status failed + ingest_error ngắn đã che khóa, status/nội dung/chunk giữ nguyên
    (văn bản vẫn active và tìm kiếm vẫn dùng bản cũ). Không ném lỗi ra ngoài. Kết quả ghi vào nhật ký nạp."""
    started = time.perf_counter()
    db = None
    doc_number = title = None
    try:
        db = approval._open_session()
        doc = db.get(LegalDocument, document_id)
        if doc is None:
            raise LookupError(f"Không tìm thấy văn bản {document_id}")
        doc_number, title = doc.doc_number, doc.title
        old_length, old_digest = len(doc.content), _digest(doc.content)
        old_chunks = document_repo.list_chunks_with_vectors(db, document_id)
        db.rollback()  # đóng giao dịch của lần đọc, không giữ qua lúc gọi Gemini

        new_chunks = chunk_legal_text(new_content)
        if not new_chunks:
            raise EmptyDocumentError("Văn bản rỗng, không cắt được đoạn nào")
        embedder = get_embedder()
        new_texts = [embedding_text(title, c) for c in new_chunks]
        old_texts = [(embedding_text(title, Chunk(content, info or {})), m) for content, info, _, m in old_chunks]
        plan = reuse_plan(old_texts, new_texts, embedder.model_name)
        vectors, reused, embedded = fill_vectors(plan, [v for _, _, v, _ in old_chunks], new_texts, embedder.embed_documents)

        if db.get_bind().dialect.name == "postgresql":
            db.execute(sql_text("SET LOCAL statement_timeout = 0"))  # phải là câu đầu của giao dịch ghi; chỉ trong giao dịch này
        doc = db.get(LegalDocument, document_id)
        if doc is None or doc.status != "active" or len(doc.content) != old_length or _digest(doc.content) != old_digest:
            raise DocumentChangedError("Văn bản vừa bị đổi trong lúc xử lý, hãy thử lại.")
        document_repo.add_document_version(db, document_id, doc.content, doc.chunk_count, actor_user_id)
        document_repo.replace_chunks(
            db,
            doc,
            [{"content": c.content, "section_info": c.section_info, "embedding": v} for c, v in zip(new_chunks, vectors)],
            embedding_model=embedder.model_name,
        )
        doc.content = new_content
        doc.ingest_status = "done"
        doc.ingest_error = None
        doc.updated_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:  # noqa: BLE001 - mọi lỗi đều đổi thành failed, không để thoát ra khỏi hàm nền
        logger.exception("Thay nội dung văn bản %s thất bại sau %.2fs", document_id, time.perf_counter() - started)
        approval._mark_failed(db, document_id, exc)  # chỉ đặt ingest_status/ingest_error, không đụng status, nội dung, chunk
        ingest_log.record(
            "replace_failed", actor_user_id=actor_user_id, actor_role=actor_role, document_id=document_id,
            doc_number=doc_number, title=title, detail=approval._failure_message(exc),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    else:
        logger.info(
            "Thay nội dung văn bản %s xong: %d chunk (dùng lại %d, embed %d), %.2fs",
            document_id, len(new_chunks), reused, embedded, time.perf_counter() - started,
        )
        ingest_log.record(
            "replace_done", actor_user_id=actor_user_id, actor_role=actor_role, document_id=document_id,
            doc_number=doc_number, title=title, chunk_count=len(new_chunks),
            detail=f"dùng lại {reused}, embed {embedded}",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                logger.exception("Không đóng được session của việc nền thay nội dung văn bản %s", document_id)
