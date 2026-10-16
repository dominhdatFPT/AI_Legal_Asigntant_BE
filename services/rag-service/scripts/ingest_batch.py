"""Nạp nhiều văn bản pháp luật theo file danh sách CSV.

Cách chạy (ở thư mục gốc legal-assistant-ai, đã bật venv, .env có RAG_DATABASE_URL + GEMINI_API_KEY):

    # 1. Kiểm tra danh sách + cách cắt từng văn bản (KHÔNG gọi Gemini, KHÔNG ghi database)
    python services/rag-service/scripts/ingest_batch.py B:\\AI_03\\BE\\data\\danh_sach_luat.csv --dry-run

    # 2. Nạp thật. Văn bản đã có thì tự bỏ qua; hết quota thì dừng, chạy lại để nạp tiếp.
    python services/rag-service/scripts/ingest_batch.py B:\\AI_03\\BE\\data\\danh_sach_luat.csv

Mẫu file danh sách: services/rag-service/scripts/danh_sach_luat.mau.csv
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

SERVICE_DIR = Path(__file__).resolve().parents[1]  # services/rag-service
ROOT_DIR = SERVICE_DIR.parents[1]                  # legal-assistant-ai
sys.path.insert(0, str(SERVICE_DIR))
load_dotenv(ROOT_DIR / ".env")


def main() -> int:
    p = argparse.ArgumentParser(description="Nạp nhiều văn bản pháp luật theo file CSV")
    p.add_argument("manifest", type=Path, help="File CSV danh sách văn bản")
    p.add_argument("--dry-run", action="store_true", help="Chỉ kiểm tra file và cách cắt đoạn")
    p.add_argument("--fake-embedding", action="store_true", help="Dùng vector giả, không gọi Gemini")
    p.add_argument("--max-chars", type=int, default=2500)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # bớt log "HTTP Request: POST ..."
    if args.fake_embedding:
        os.environ["EMBEDDING_PROVIDER"] = "fake"

    from app.services.batch_ingest import ManifestError, load_manifest, run_batch

    try:
        rows = load_manifest(args.manifest)
    except ManifestError as e:
        print(f"❌ {e}")
        return 1
    print(f"Danh sách có {len(rows)} văn bản.\n")

    if args.dry_run:
        result = run_batch(None, None, rows, dry_run=True, max_chars=args.max_chars)
        missing = [d for d, _ in result.failed]
        print(f"\nKiểm tra xong. {'Thiếu file: ' + ', '.join(missing) if missing else 'Đủ file.'}")
        return 1 if missing else 0

    from app.core.database import get_session
    from app.services.embedding import get_embedder

    db = get_session()
    try:
        result = run_batch(db, get_embedder(), rows, max_chars=args.max_chars)
    finally:
        db.close()

    print("\n===== Tổng kết =====")
    print(f"✅ Nạp mới: {len(result.done)}  ⏭ Đã có: {len(result.skipped)}  ❌ Lỗi: {len(result.failed)}")
    for doc_number, reason in result.failed:
        print(f"   ❌ {doc_number}: {reason}")
    if result.stopped_for_quota:
        print("⛔ Dừng vì hết quota Gemini. Chạy lại đúng lệnh này sau để nạp tiếp phần còn thiếu.")
        return 2
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
