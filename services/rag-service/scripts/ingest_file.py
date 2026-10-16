"""Nạp một văn bản pháp luật (file .txt UTF-8) vào database của rag-service.

Cách chạy (ở thư mục gốc legal-assistant-ai, đã bật venv, .env có RAG_DATABASE_URL + GEMINI_API_KEY):

    # 1. Xem trước cách cắt đoạn, KHÔNG gọi API, KHÔNG ghi database
    python services/rag-service/scripts/ingest_file.py duong_dan/van_ban.txt --title "Luật Đất đai 2024" --dry-run

    # 2. Nạp thật
    python services/rag-service/scripts/ingest_file.py duong_dan/van_ban.txt \
        --title "Luật Đất đai 2024" --doc-type luat --doc-number 31/2024/QH15 \
        --issuing-body "Quốc hội" --effective-date 2025-01-01

    # Chạy thử không cần key Gemini (vector giả, chỉ để kiểm tra luồng):
    ... --fake-embedding
"""

import argparse
import logging
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

SERVICE_DIR = Path(__file__).resolve().parents[1]  # services/rag-service
ROOT_DIR = SERVICE_DIR.parents[1]                  # legal-assistant-ai
sys.path.insert(0, str(SERVICE_DIR))
load_dotenv(ROOT_DIR / ".env")


def main() -> int:
    p = argparse.ArgumentParser(description="Nạp văn bản pháp luật vào rag-service")
    p.add_argument("file", type=Path, help="File .txt UTF-8 chứa toàn văn")
    p.add_argument("--title", required=True)
    p.add_argument("--doc-type", default="luat", choices=["luat", "nghi_dinh", "thong_tu", "quyet_dinh", "khac"])
    p.add_argument("--doc-number")
    p.add_argument("--issuing-body")
    p.add_argument("--issued-date", type=date.fromisoformat)
    p.add_argument("--effective-date", type=date.fromisoformat)
    p.add_argument("--status", default="active", choices=["active", "superseded", "draft"])
    p.add_argument("--source-url")
    p.add_argument("--max-chars", type=int, default=2500)
    p.add_argument("--dry-run", action="store_true", help="Chỉ cắt đoạn và in thống kê")
    p.add_argument("--fake-embedding", action="store_true", help="Dùng vector giả, không gọi Gemini")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.fake_embedding:
        os.environ["EMBEDDING_PROVIDER"] = "fake"

    from app.services.chunking import chunk_legal_text, embedding_text

    content = args.file.read_text(encoding="utf-8")

    if args.dry_run:
        chunks = chunk_legal_text(content, max_chars=args.max_chars)
        sizes = [len(c.content) for c in chunks]
        print(f"Số đoạn: {len(chunks)}")
        if chunks:
            print(f"Độ dài (ký tự): nhỏ nhất {min(sizes)}, lớn nhất {max(sizes)}, trung bình {sum(sizes)//len(sizes)}")
            levels = Counter(
                "điểm" if c.section_info.get("diem") else
                "khoản" if c.section_info.get("khoan") else
                "điều" if c.section_info.get("dieu") else "đoạn văn"
                for c in chunks
            )
            print("Cắt theo:", dict(levels))
            total_chars = sum(len(embedding_text(args.title, c)) for c in chunks)
            print(f"Tổng ký tự gửi embedding: {total_chars:,} (ước ~{total_chars // 3:,} token)")
            for c in chunks[:3]:
                print("-" * 60)
                print(c.section_info)
                print(c.content[:400])
        return 0

    from app.core.database import get_session
    from app.services.embedding import get_embedder
    from app.services.ingest import ingest_document

    db = get_session()
    try:
        doc = ingest_document(
            db,
            get_embedder(),
            title=args.title,
            doc_type=args.doc_type,
            content=content,
            doc_number=args.doc_number,
            issuing_body=args.issuing_body,
            issued_date=args.issued_date,
            effective_date=args.effective_date,
            status=args.status,
            source_url=args.source_url,
            max_chars=args.max_chars,
        )
        print(f"Đã nạp: {doc.title} — {doc.chunk_count} đoạn — id {doc.id}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
