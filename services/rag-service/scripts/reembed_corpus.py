"""Nâng cấp / re-embed các văn bản trong RAG DB chưa đạt chuẩn EMBEDDING_FORMAT_VERSION (v2_breadcrumb).

Chạy theo 3 pha an toàn cho từng văn bản: ĐỌC -> EMBED (ngoài transaction) -> GHI (1 transaction ngắn).
Dữ liệu cũ vẫn phục vụ bình thường trong lúc đang gọi API embedding.

Cách chạy:
    python BE/services/rag-service/scripts/reembed_corpus.py --dry-run
    python BE/services/rag-service/scripts/reembed_corpus.py --provider openrouter
"""

import argparse
import hashlib
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text as sql_text

SERVICE_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = SERVICE_DIR.parents[1] if len(SERVICE_DIR.parents) > 1 else SERVICE_DIR
sys.path.insert(0, str(SERVICE_DIR))
if (ROOT_DIR / ".env").exists():
    load_dotenv(ROOT_DIR / ".env")
elif (SERVICE_DIR / ".env").exists():
    load_dotenv(SERVICE_DIR / ".env")
else:
    load_dotenv()


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _needs_upgrade(db, doc_id) -> bool:
    from app.models.chunk import DocumentChunk
    from app.services.chunking import EMBEDDING_FORMAT_VERSION

    row = (
        db.query(DocumentChunk.section_info)
        .filter(DocumentChunk.document_id == doc_id)
        .first()
    )
    if not row:
        return True
    info = row[0] or {}
    return info.get("_embedding_format_version") != EMBEDDING_FORMAT_VERSION


def main() -> int:
    p = argparse.ArgumentParser(description="Nâng cấp các văn bản cũ lên chuẩn v2_breadcrumb")
    p.add_argument("--provider", choices=["openrouter", "gemini", "fake"], help="Ghi đè EMBEDDING_PROVIDER")
    p.add_argument("--doc-number", help="Chỉ nâng cấp 1 số hiệu văn bản cụ thể")
    p.add_argument("--dry-run", action="store_true", help="Chỉ kiểm tra văn bản cần nâng cấp, không gọi API / không ghi DB")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.provider:
        os.environ["EMBEDDING_PROVIDER"] = args.provider

    from app.core.database import get_session
    from app.models.document import LegalDocument
    from app.repositories import document as document_repo
    from app.services import corpus_version
    from app.services.chunking import DEFAULT_MAX_CHARS, chunk_legal_text, embedded_chunk, embedding_text
    from app.services.embedding import get_embedder

    db = get_session()
    try:
        q = db.query(LegalDocument).filter(LegalDocument.status == "active").order_by(LegalDocument.created_at)
        if args.doc_number:
            q = q.filter(LegalDocument.doc_number == args.doc_number)
        docs = q.all()
        targets = [(d.id, d.doc_number, d.title, d.chunk_count) for d in docs if _needs_upgrade(db, d.id)]
    finally:
        db.close()

    print(f"Tìm thấy {len(targets)} văn bản cần nâng cấp lên v2_breadcrumb:")
    for _, doc_num, title, cnt in targets:
        print(f" - {doc_num}: {title} ({cnt} chunks)")

    if args.dry_run or not targets:
        return 0

    embedder = get_embedder()
    print(f"\nBắt đầu re-embed bằng provider={os.environ.get('EMBEDDING_PROVIDER', 'config')}, model={embedder.model_name}...")

    done = 0
    for doc_id, doc_num, title, _ in targets:
        started = time.perf_counter()
        db = get_session()
        try:
            doc = db.get(LegalDocument, doc_id)
            if not doc or not doc.content:
                print(f"⏭ Bỏ qua {doc_num}: không có nội dung")
                continue
            old_content = doc.content
            old_digest = _digest(old_content)
        finally:
            db.close()

        chunks = chunk_legal_text(old_content, max_chars=DEFAULT_MAX_CHARS)
        if not chunks:
            print(f"❌ {doc_num}: không cắt được đoạn nào")
            continue

        print(f"\n[{done + 1}/{len(targets)}] {doc_num} ({title}): {len(chunks)} chunks...")
        vectors = embedder.embed_documents([embedding_text(title, c) for c in chunks])

        db = get_session()
        try:
            if db.get_bind().dialect.name == "postgresql":
                db.execute(sql_text("SET LOCAL statement_timeout = 0"))
            doc = db.get(LegalDocument, doc_id)
            if not doc or _digest(doc.content) != old_digest:
                print(f"⚠ {doc_num}: nội dung đã thay đổi trong lúc embed, bỏ qua ghi")
                continue
            document_repo.replace_chunks(
                db,
                doc,
                [embedded_chunk(c, v) for c, v in zip(chunks, vectors)],
                embedding_model=embedder.model_name,
            )
            db.commit()
            corpus_version.bump_corpus_version()
            done += 1
            print(f"✅ Xong {doc_num}: {len(chunks)} chunks trong {time.perf_counter() - started:.1f}s")
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    print(f"\nHoàn tất nâng cấp {done}/{len(targets)} văn bản lên v2_breadcrumb.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
