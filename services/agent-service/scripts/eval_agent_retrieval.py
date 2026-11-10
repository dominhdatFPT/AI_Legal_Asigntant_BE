"""Đo việc viết lại câu hỏi: tìm đoạn luật bằng câu gốc, câu viết lại và kết quả gộp, so hit@k và MRR của ba cách.

Với mỗi câu hỏi: (1) "gốc" = rag_client.search(q); (2) "viết lại" = query_rewrite.rewrite(q, history) rồi rag_client.search(q2);
(3) "gộp" = contract_review.merge_chunks([gốc, viết lại], limit=top_k), đúng cách qa_agent._search_with_rewrite gộp (câu viết lại
giống câu gốc thì cả ba cách là một). Gọi AI THẬT (rewrite) và rag-service THẬT; script gọi thẳng rewrite nên không phụ thuộc
cờ QUERY_REWRITE_ENABLED và không bật cờ nào. Cách tính trúng và MRR giống services/rag-service/scripts/eval_retrieval.py:
đoạn trúng khi đúng văn bản (số hiệu hoặc tên chứa "doc") và Điều nằm trong "dieu"; MRR = trung bình 1/vị trí đoạn đúng đầu
tiên trong top_k (bắt đầu từ 1), không có thì 0. Số đo chỉ để đọc: chạy xong luôn thoát mã 0. Chỗ cần xem nhất là hai danh sách
cuối: câu "gộp" trúng mà "gốc" trượt (được lợi) và câu "gộp" trượt mà "gốc" trúng (bị hại).

File câu hỏi giống eval_natural.json của rag-service: danh sách {"q", "doc", "dieu": [...]}, thêm "history" tuỳ chọn là danh sách
{"role", "content"} (cũ đến mới) cho câu hỏi nối tiếp (xem followup_cases.json).

rag-service không mở cổng ra máy host nên chạy trong mạng Docker (ở thư mục gốc legal-assistant-ai, đã `docker compose up -d rag-service`).
Container cần có khoá AI trong biến môi trường (đã có trong .env của compose: LLM_PROVIDER, OPENROUTER_API_KEY/OPENROUTER_MODEL
hoặc GEMINI_API_KEY...) và rag-service phải đang chạy. Mount app và scripts của agent-service, scripts của rag-service vào /app/rag_scripts:
    docker compose run --rm --no-deps -v ./services/agent-service/app:/app/app -v ./services/agent-service/scripts:/app/scripts -v ./services/rag-service/scripts:/app/rag_scripts agent-service python scripts/eval_agent_retrieval.py --file /app/rag_scripts/eval_natural.json -v
    docker compose run --rm --no-deps -v ./services/agent-service/app:/app/app -v ./services/agent-service/scripts:/app/scripts -v ./services/rag-service/scripts:/app/rag_scripts agent-service python scripts/eval_agent_retrieval.py --file scripts/followup_cases.json -v
    docker compose run --rm --no-deps -v ./services/agent-service/app:/app/app -v ./services/agent-service/scripts:/app/scripts -v ./services/rag-service/scripts:/app/rag_scripts agent-service python scripts/eval_agent_retrieval.py --file /app/rag_scripts/eval_questions.json
Kết quả rewrite do AI nên mỗi lần chạy có thể lệch một chút: nên chạy 2 lần rồi so. Mỗi câu tốn 1 lượt gọi AI và 2 lượt tìm
(embedding Gemini ở rag). Nếu AI lỗi, rewrite trả câu gốc (kèm warning): bảng có dòng đếm số câu như vậy để khỏi đọc nhầm là
"viết lại không đổi gì".

Chạy ngoài Docker (cần rag-service truy cập được, đặt RAG_SERVICE_URL, INTERNAL_TOKEN và khoá AI trong .env; Windows thì thêm
`-X utf8` sau `python` để console in được tiếng Việt):
    python services/agent-service/scripts/eval_agent_retrieval.py --file services/rag-service/scripts/eval_natural.json -v
Tham số: --file (bắt buộc), --top-k N (mặc định RAG_TOP_K của agent), -v (in từng câu).
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_DIR))
REQUEST_ID = "eval-agent-retrieval"
METHODS = ("gốc", "viết lại", "gộp")


def is_hit(chunk: dict, item: dict) -> bool:
    """Giống eval_retrieval.is_hit của rag-service: đúng văn bản (số hiệu hoặc tên) và đúng Điều."""
    doc = chunk.get("document") or {}
    same_doc = item["doc"] in (doc.get("doc_number") or "") or item["doc"] in (doc.get("title") or "")
    return same_doc and (chunk.get("section_info") or {}).get("dieu") in item["dieu"]


def first_rank(chunks: list[dict], item: dict) -> int | None:
    """Vị trí (từ 1) của đoạn đúng đầu tiên, None nếu không có."""
    return next((rank for rank, chunk in enumerate(chunks, 1) if is_hit(chunk, item)), None)


def mean_reciprocal_rank(ranks: list[int | None]) -> float:
    """Trung bình 1/vị trí; câu không có đoạn đúng (None) tính 0; danh sách rỗng trả 0."""
    return sum(1 / r for r in ranks if r) / len(ranks) if ranks else 0.0


def summarize(ranks: list[int | None]) -> dict:
    hits = sum(r is not None for r in ranks)
    return {"n": len(ranks), "hits": hits, "rate": hits / len(ranks) if ranks else 0.0, "mrr": mean_reciprocal_rank(ranks)}


def same_query(a: str, b: str) -> bool:
    """Giống cách qa_agent so câu viết lại với câu gốc: bỏ khoảng trắng thừa, không phân biệt hoa thường."""
    return " ".join(a.split()).lower() == " ".join(b.split()).lower()


def gained_and_lost(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """(câu "gộp" trúng mà "gốc" trượt, câu "gộp" trượt mà "gốc" trúng); mỗi row có ranks[cách] là vị trí hoặc None."""
    gained = [r for r in rows if r["ranks"]["gộp"] is not None and r["ranks"]["gốc"] is None]
    lost = [r for r in rows if r["ranks"]["gộp"] is None and r["ranks"]["gốc"] is not None]
    return gained, lost


def short_chunk(chunk: dict, item: dict) -> str:
    """Ví dụ "Đ.45 31/2024/QH15"; thêm dấu * sau số Điều khi đoạn đó trúng."""
    doc = chunk.get("document") or {}
    label = doc.get("doc_number") or (doc.get("title") or "?")[:20]
    return f"Đ.{(chunk.get('section_info') or {}).get('dieu')}{'*' if is_hit(chunk, item) else ''} {label}"


def describe(chunks: list[dict], rank: int | None, item: dict) -> str:
    mark = f"TRÚNG #{rank}" if rank else "TRƯỢT"
    return f"{mark:9} | " + (", ".join(short_chunk(c, item) for c in chunks) or "(không có đoạn nào)")


def load_items(path: Path) -> list[dict]:
    items = json.loads(path.read_text(encoding="utf-8"))
    for i, item in enumerate(items, 1):
        missing = [k for k in ("q", "doc", "dieu") if k not in item]
        if missing:
            raise ValueError(f"câu {i} thiếu trường {missing}")
    return items


class _WarningCounter(logging.Handler):
    """Đếm warning của query_rewrite: mỗi warning là một lần rewrite phải trả câu gốc (AI lỗi, quá giờ, kết quả không dùng được)."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.count = 0

    def emit(self, record):
        self.count += 1


async def run_item(item: dict, top_k: int, counter: _WarningCounter) -> dict:
    from app.services import contract_review, query_rewrite, rag_client

    history = item.get("history") or []
    warnings_before = counter.count
    original = await rag_client.search(item["q"], top_k=top_k, request_id=REQUEST_ID)
    started = time.perf_counter()
    rewritten_q = await query_rewrite.rewrite(item["q"], history, request_id=REQUEST_ID)
    rewrite_seconds = time.perf_counter() - started
    changed = not same_query(rewritten_q, item["q"])
    if changed:
        rewritten = await rag_client.search(rewritten_q, top_k=top_k, request_id=REQUEST_ID)
        merged = contract_review.merge_chunks([original, rewritten], limit=top_k)
    else:
        rewritten = merged = original
    chunks = {"gốc": original, "viết lại": rewritten, "gộp": merged}
    return {
        "item": item, "rewritten_q": rewritten_q, "changed": changed, "seconds": rewrite_seconds,
        "rewrite_failed": counter.count > warnings_before, "chunks": chunks,
        "ranks": {method: first_rank(found, item) for method, found in chunks.items()},
    }


def print_row(index: int, row: dict) -> None:
    item = row["item"]
    print(f"[{index:2}] {item['q']}" + (f"  (có lịch sử {len(item['history'])} tin)" if item.get("history") else ""))
    state = "khác câu gốc" if row["changed"] else "giống câu gốc"
    if row["rewrite_failed"]:
        state += ", AI lỗi nên dùng câu gốc"
    print(f"      câu viết lại: {row['rewritten_q']}  ({state}, {row['seconds']:.2f}s)")
    for method in METHODS:
        print(f"      {method:8} {describe(row['chunks'][method], row['ranks'][method], item)}")
    print(f"      cần Đ.{'/'.join(item['dieu'])} {item['doc']}")


def print_summary(rows: list[dict], errors: int, top_k: int) -> None:
    print(f"\n=== {len(rows)} câu đo được ({errors} câu lỗi bỏ qua), top_k={top_k}, viết lại bằng AI thật ===")
    print(f"{'cách':10} {'hit@' + str(top_k):>8} {'tỷ lệ':>6} {'MRR':>6}")
    for method in METHODS:
        s = summarize([r["ranks"][method] for r in rows])
        print(f"{method:10} {str(s['hits']) + '/' + str(s['n']):>8} {s['rate']:>6.0%} {s['mrr']:>6.2f}")
    if rows:
        seconds = [r["seconds"] for r in rows]
        print(f"viết lại: trung bình {sum(seconds) / len(seconds):.2f}s, lớn nhất {max(seconds):.2f}s")
        print(f"câu viết lại khác câu gốc: {sum(r['changed'] for r in rows)}/{len(rows)}"
              f" | rewrite phải dùng câu gốc do lỗi: {sum(r['rewrite_failed'] for r in rows)}")

    gained, lost = gained_and_lost(rows)
    for title, group in (
        (f"Câu 'gộp' TRÚNG mà 'gốc' TRƯỢT ({len(gained)}), viết lại giúp được:", gained),
        (f"Câu 'gộp' TRƯỢT mà 'gốc' TRÚNG ({len(lost)}), viết lại làm hại, cần xem:", lost),
    ):
        print(f"\n{title}")
        for r in group:
            print(f"  - {r['item']['q']}\n      viết lại: {r['rewritten_q']}")
            for method in ("gốc", "gộp"):
                print(f"      {method:8} {describe(r['chunks'][method], r['ranks'][method], r['item'])}")
        if not group:
            print("  (không có)")


async def main_async(args) -> int:
    from app.core.config import get_settings

    items = load_items(Path(args.file))
    top_k = args.top_k or get_settings().rag_top_k
    counter = _WarningCounter()
    logging.getLogger("app.services.query_rewrite").addHandler(counter)
    rows, errors = [], 0
    try:
        for i, item in enumerate(items, 1):
            try:
                row = await run_item(item, top_k, counter)
            except Exception as e:  # rag lỗi...: ghi lại và đi tiếp câu sau
                errors += 1
                print(f"[{i:2}] LỖI | {item['q'][:60]}\n      {type(e).__name__}: {e}")
                continue
            rows.append(row)
            if args.verbose:
                print_row(i, row)
    finally:
        logging.getLogger("app.services.query_rewrite").removeHandler(counter)
    print_summary(rows, errors, top_k)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True, help="file câu hỏi: danh sách {q, doc, dieu, history (tuỳ chọn)}")
    p.add_argument("--top-k", type=int, default=0, help="mặc định RAG_TOP_K của agent")
    p.add_argument("-v", "--verbose", action="store_true", help="in từng câu: câu gốc, câu viết lại, các Điều tìm được")
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
