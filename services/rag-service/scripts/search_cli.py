"""Tìm thử trong database rag-service, in ra các đoạn gần câu hỏi nhất.

Cách chạy (ở thư mục gốc legal-assistant-ai, đã bật venv):
    python services/rag-service/scripts/search_cli.py "Chuyển nhượng đất cần giấy tờ gì?" --top-k 5
    # văn bản nạp bằng --fake-embedding thì tìm cũng phải thêm --fake-embedding
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

SERVICE_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = SERVICE_DIR.parents[1]
sys.path.insert(0, str(SERVICE_DIR))
load_dotenv(ROOT_DIR / ".env")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("query")
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--fake-embedding", action="store_true")
    args = p.parse_args()
    if args.fake_embedding:
        os.environ["EMBEDDING_PROVIDER"] = "fake"

    from app.core.database import get_session
    from app.services.embedding import get_embedder
    from app.services.search import search_chunks

    db = get_session()
    try:
        results = search_chunks(db, get_embedder(), args.query, args.top_k)
    finally:
        db.close()

    if not results:
        print("Không có kết quả (database trống, hoặc văn bản được nạp bằng model embedding khác).")
    for i, r in enumerate(results, 1):
        s = r["section_info"] or {}
        where = ", ".join(f"{k} {v}" for k, v in s.items() if v)
        print(f"[{i}] score {r['score']:.3f} | {r['document']['title']} | {where}")
        print("    " + r["content"][:300].replace("\n", "\n    "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
