"""Đo chất lượng tìm kiếm của rag-service trên bộ câu hỏi mẫu (scripts/eval_questions.json).

Mỗi câu hỏi ghi sẵn văn bản (số hiệu) và các Điều được coi là đúng. Một câu "trúng" khi trong top_k
đoạn tìm được có ít nhất 1 đoạn thuộc đúng văn bản và đúng Điều.
In ra: tỷ lệ trúng (hit@k) và MRR (trung bình 1/vị trí đoạn đúng đầu tiên, càng gần 1 càng tốt).

Cách chạy (ở thư mục gốc legal-assistant-ai, đã bật venv, .env có RAG_DATABASE_URL + GEMINI_API_KEY):
    python services/rag-service/scripts/eval_retrieval.py              # cấu hình hiện tại
    python services/rag-service/scripts/eval_retrieval.py --compare    # so sánh tắt/bật đổi từ đời thường
    python services/rag-service/scripts/eval_retrieval.py --top-k 4 -v # in từng câu

Chạy trong Docker (không cần venv), ở thư mục gốc:
    docker compose run --rm -v ./services/rag-service/scripts:/app/scripts rag-service python scripts/eval_retrieval.py --compare
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_DIR))
try:  # chạy local thì đọc .env ở gốc repo; trong Docker (/app) biến môi trường đã có sẵn
    from dotenv import load_dotenv

    if len(SERVICE_DIR.parents) > 1:
        load_dotenv(SERVICE_DIR.parents[1] / ".env")
except ImportError:
    pass

QUESTIONS = Path(__file__).resolve().parent / "eval_questions.json"


def is_hit(chunk: dict, item: dict) -> bool:
    doc = chunk.get("document") or {}
    same_doc = item["doc"] in (doc.get("doc_number") or "") or item["doc"] in (doc.get("title") or "")
    return same_doc and (chunk.get("section_info") or {}).get("dieu") in item["dieu"]


def run(questions: list[dict], top_k: int, expansion: bool, verbose: bool) -> dict:
    os.environ["QUERY_EXPANSION_ENABLED"] = "true" if expansion else "false"
    from app.core.config import get_settings
    from app.core.database import get_session
    from app.services.embedding import get_embedder
    from app.services.search import search_chunks

    get_settings.cache_clear()
    embedder = get_embedder()
    db = get_session()
    hits, rr, seconds = 0, 0.0, 0.0
    try:
        for i, item in enumerate(questions, 1):
            started = time.perf_counter()
            chunks = search_chunks(db, embedder, item["q"], top_k)
            seconds += time.perf_counter() - started
            rank = next((r for r, c in enumerate(chunks, 1) if is_hit(c, item)), None)
            hits += rank is not None
            rr += 1 / rank if rank else 0
            if verbose:
                found = ", ".join(
                    f"Đ.{(c.get('section_info') or {}).get('dieu')} {c['document']['doc_number']} ({c['score']:.2f})"
                    for c in chunks
                )
                mark = f"TRÚNG #{rank}" if rank else "TRƯỢT"
                print(f"[{i:2}] {mark:9} | {item['q']}\n      cần Đ.{'/'.join(item['dieu'])} {item['doc']} | tìm được: {found}")
    finally:
        db.close()
    n = len(questions)
    return {"hit": hits, "n": n, "mrr": rr / n, "avg_s": seconds / n}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--top-k", type=int, default=4, help="giống RAG_TOP_K bên agent-service (mặc định 4)")
    p.add_argument("--compare", action="store_true", help="chạy 2 lần: tắt và bật đổi từ đời thường")
    p.add_argument("-v", "--verbose", action="store_true", help="in kết quả từng câu")
    args = p.parse_args()

    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    modes = [False, True] if args.compare else [None]
    for mode in modes:
        if mode is None:
            from app.core.config import get_settings

            mode = get_settings().query_expansion_enabled
        label = "BẬT đổi từ đời thường" if mode else "TẮT đổi từ đời thường"
        print(f"\n=== {label}, top_k={args.top_k}, {len(questions)} câu ===")
        r = run(questions, args.top_k, mode, args.verbose)
        print(f"hit@{args.top_k}: {r['hit']}/{r['n']} ({r['hit'] / r['n']:.0%}) | MRR: {r['mrr']:.2f} "
              f"| trung bình {r['avg_s']:.2f}s/câu")
    return 0


if __name__ == "__main__":
    sys.exit(main())
