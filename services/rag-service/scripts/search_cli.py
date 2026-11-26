"""Tìm thử trong database rag-service, in ra các đoạn gần câu hỏi nhất.

Cách chạy (ở thư mục gốc legal-assistant-ai, đã bật venv):
    python services/rag-service/scripts/search_cli.py "Chuyển nhượng đất cần giấy tờ gì?" --top-k 5
    python services/rag-service/scripts/search_cli.py "Chuyển nhượng đất cần giấy tờ gì?" --max-chars 0   # in đủ nội dung đoạn
    # văn bản nạp bằng --fake-embedding thì tìm cũng phải thêm --fake-embedding
    python services/rag-service/scripts/search_cli.py "tách sổ đỏ cho con" --explain   # bật hybrid + in điểm từng ứng viên
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

SERVICE_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = SERVICE_DIR.parents[1] if len(SERVICE_DIR.parents) > 1 else SERVICE_DIR
sys.path.insert(0, str(SERVICE_DIR))
if (ROOT_DIR / ".env").exists():
    load_dotenv(ROOT_DIR / ".env")
elif (SERVICE_DIR / ".env").exists():
    load_dotenv(SERVICE_DIR / ".env")
else:
    load_dotenv()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("query")
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--max-chars", type=int, default=300, help="số ký tự in cho mỗi đoạn (mặc định 300, 0 = in đủ)")
    p.add_argument("--fake-embedding", action="store_true")
    p.add_argument(
        "--explain",
        action="store_true",
        help="bật hybrid (HYBRID_ENABLED=true, alpha theo HYBRID_ALPHA) và in điểm vector, BM25, điểm cuối, hạng trước/sau của từng ứng viên",
    )
    args = p.parse_args()
    if args.fake_embedding:
        os.environ["EMBEDDING_PROVIDER"] = "fake"
    if args.explain:
        os.environ["HYBRID_ENABLED"] = "true"

    from app.core.database import get_session
    from app.services.embedding import get_embedder
    from app.services.search import search_chunks

    trace: list | None = [] if args.explain else None
    db = get_session()
    try:
        results = search_chunks(db, get_embedder(), args.query, args.top_k, trace=trace)
    finally:
        db.close()

    for part in trace or []:
        if "final" in part:
            print("== Thứ tự cuối (sau xen kẽ / đa dạng hoá, xếp theo điểm hybrid):")
            for c in part["final"]:
                where = f"{c['doc_number']}, Đ.{c['dieu']}" + (f" k.{c['khoan']}" if c["khoan"] else "")
                print(f"   #{c['rank']:<2} | vector {c['vector']:.4f} | điểm cuối {c['final']:.4f} | {where}")
            continue
        print(f"== Câu tìm (đã mở rộng): {part['query']}")
        print("   hạng trước->sau | vector | BM25 thô | BM25 chuẩn hoá | điểm cuối | văn bản, Điều, khoản")
        for c in sorted(part["candidates"], key=lambda c: c["rank_after"]):
            where = f"{c['doc_number']}, Đ.{c['dieu']}" + (f" k.{c['khoan']}" if c["khoan"] else "")
            print(
                f"   {c['rank_before']:>3} -> {c['rank_after']:<3}      | {c['vector']:.4f} | {c['bm25_raw']:8.3f} "
                f"| {c['bm25_norm']:.3f}          | {c['final']:.4f}    | {where}"
            )
    if trace:
        print("== Kết quả cuối (cùng thứ tự trên; score = điểm vector):")

    if not results:
        print("Không có kết quả (database trống, hoặc văn bản được nạp bằng model embedding khác).")
    for i, r in enumerate(results, 1):
        s = r["section_info"] or {}
        where = ", ".join(f"{k} {v}" for k, v in s.items() if v)
        print(f"[{i}] score {r['score']:.3f} | {r['document']['title']} | {where}")
        content = r["content"]
        cut = args.max_chars > 0 and len(content) > args.max_chars
        if cut:
            content = content[:args.max_chars] + "…"
        print("    " + content.replace("\n", "\n    "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
