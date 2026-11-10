"""Đo bước chấm liên quan (relevance.grade): chấm có giữ lại đoạn luật đúng không, và có chặn đúng câu ngoài phạm vi không.

Với mỗi câu TRONG phạm vi (--file: danh sách {"q", "doc", "dieu"} như eval_natural.json của rag-service): lấy --candidates đoạn
bằng rag_client.search, rồi gọi relevance.grade thật --repeat lần và xếp từng lần chấm vào một loại:
  1) giữ đúng: ứng viên có đoạn đúng và danh sách chấm giữ lại có đoạn đúng;
  2) mất đoạn đúng: ứng viên có đoạn đúng, danh sách chấm không rỗng nhưng không còn đoạn đúng;
  3) chặn nhầm: ứng viên có đoạn đúng nhưng chấm trả danh sách rỗng (agent sẽ trả "ngoài phạm vi" cho câu này);
  4) không chấm được: grade trả None (AI lỗi, quá giờ, JSON hỏng...), xếp loại này trước, không xét ứng viên;
  5) không có đoạn đúng trong ứng viên: rag không lấy được đoạn đúng, bước chấm không có gì để giữ nên không tính vào 1-3.
Với mỗi câu NGOÀI phạm vi (--out-file: danh sách {"q"}, vd out_of_scope_cases.json): "chặn đúng" nếu chấm trả rỗng, "lọt" nếu trả
danh sách không rỗng (ghi các Điều giữ lại), "không chấm được" nếu None. Đoạn đúng tính như eval_agent_retrieval.py và
eval_retrieval.py của rag-service: đúng văn bản (số hiệu hoặc tên chứa "doc") và Điều nằm trong "dieu".
Số đo chỉ để đọc: chạy xong luôn thoát mã 0. Chỗ cần xem nhất là các câu "chặn nhầm", "mất đoạn đúng" và câu ngoài phạm vi bị "lọt".

Script gọi thẳng relevance.grade nên KHÔNG cần bật RELEVANCE_CHECK và không bật cờ nào. Gọi AI THẬT và rag-service THẬT: mỗi lần
chấm tốn 1 lượt gọi AI (số câu x --repeat), mỗi câu tốn 1 lượt tìm (embedding Gemini ở rag). AI chấm có thể lệch giữa các lần nên
nên dùng --repeat 3 (bảng có dòng đếm số câu "không ổn định": các lần chấm giữ các đoạn khác nhau). Chấm quá RELEVANCE_TIMEOUT_SECONDS
(mặc định 8s) thì grade trả None: ứng viên nhiều hoặc --chunk-chars lớn làm prompt dài, thấy nhiều "không chấm được" thì thêm
`-e RELEVANCE_TIMEOUT_SECONDS=20` sau `run --rm` để đo riêng độ chính xác. --reasoning chỉ tác dụng với OpenRouter (0 = tắt suy luận).

rag-service không mở cổng ra máy host nên chạy trong mạng Docker (ở thư mục gốc legal-assistant-ai, đã `docker compose up -d rag-service`).
Container cần có khoá AI trong biến môi trường (đã có trong .env của compose). Mount app và scripts của agent-service, scripts của
rag-service vào /app/rag_scripts:
    docker compose run --rm --no-deps -v ./services/agent-service/app:/app/app -v ./services/agent-service/scripts:/app/scripts -v ./services/rag-service/scripts:/app/rag_scripts agent-service python scripts/eval_relevance.py --file /app/rag_scripts/eval_natural.json --out-file scripts/out_of_scope_cases.json
Thêm tuỳ chọn để so các cấu hình (mỗi lần chạy chỉ đổi một thứ):
    ... python scripts/eval_relevance.py --file /app/rag_scripts/eval_natural.json --out-file scripts/out_of_scope_cases.json --chunk-chars 1200
    ... python scripts/eval_relevance.py --file /app/rag_scripts/eval_natural.json --out-file scripts/out_of_scope_cases.json --reasoning 256
    ... python scripts/eval_relevance.py --file /app/rag_scripts/eval_natural.json --out-file scripts/out_of_scope_cases.json --repeat 3 -v
(`...` là phần `docker compose run --rm --no-deps -v ... agent-service` như lệnh đầu.)

Chạy ngoài Docker (cần rag-service truy cập được, đặt RAG_SERVICE_URL, INTERNAL_TOKEN và khoá AI trong .env; Windows thì thêm
`-X utf8` sau `python` để console in được tiếng Việt):
    python services/agent-service/scripts/eval_relevance.py --file services/rag-service/scripts/eval_natural.json
Tham số: --file (bắt buộc), --out-file, --candidates N (mặc định 12, tối đa 20), --chunk-chars N (đặt RELEVANCE_CHUNK_CHARS),
--reasoning N (đặt LLM_AUX_REASONING_MAX_TOKENS), --repeat R (mặc định 1), -v.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_DIR))
REQUEST_ID = "eval-relevance"
MAX_CANDIDATES = 20  # rag-service từ chối top_k lớn hơn 20

KEPT, LOST, BLOCKED, UNGRADED, NO_CANDIDATE = (
    "giữ đúng", "mất đoạn đúng", "chặn nhầm", "không chấm được", "không có đoạn đúng trong ứng viên",
)
IN_SCOPE_CATEGORIES = (KEPT, LOST, BLOCKED, UNGRADED, NO_CANDIDATE)
BLOCKED_OUT, LEAKED, UNGRADED_OUT = "chặn đúng", "lọt", "không chấm được"
OUT_OF_SCOPE_CATEGORIES = (BLOCKED_OUT, LEAKED, UNGRADED_OUT)


# ── Phần thuần (không gọi mạng) ─────────────────────────────────────────────

def is_hit(chunk: dict, item: dict) -> bool:
    """Giống eval_agent_retrieval.is_hit: đúng văn bản (số hiệu hoặc tên) và đúng Điều."""
    doc = chunk.get("document") or {}
    same_doc = item["doc"] in (doc.get("doc_number") or "") or item["doc"] in (doc.get("title") or "")
    return same_doc and (chunk.get("section_info") or {}).get("dieu") in item["dieu"]


def classify_in_scope(has_correct_candidate: bool, graded: list[dict] | None, item: dict) -> str:
    """Xếp một lần chấm của câu trong phạm vi vào một trong IN_SCOPE_CATEGORIES (xem docstring đầu file)."""
    if graded is None:
        return UNGRADED
    if not has_correct_candidate:
        return NO_CANDIDATE
    if not graded:
        return BLOCKED
    return KEPT if any(is_hit(c, item) for c in graded) else LOST


def classify_out_of_scope(graded: list[dict] | None) -> str:
    if graded is None:
        return UNGRADED_OUT
    return BLOCKED_OUT if not graded else LEAKED


def signature(graded: list[dict] | None) -> tuple | None:
    """Dấu vết một lần chấm để so các lần với nhau: các đoạn giữ lại (id, không có thì nội dung), None nếu không chấm được."""
    if graded is None:
        return None
    return tuple(c.get("id") or c.get("content") for c in graded)


def is_unstable(runs: list[dict]) -> bool:
    """True khi các lần chấm của một câu cho kết quả khác nhau."""
    return len({r["signature"] for r in runs}) > 1


def summarize(runs: list[dict], categories: tuple) -> dict:
    """runs: danh sách {"category", "kept": số đoạn giữ lại hoặc None, "seconds"}. Trả đếm, phần trăm theo từng loại,
    số đoạn giữ lại trung bình (chỉ các lần chấm được) và thời gian chấm trung bình, lớn nhất."""
    n = len(runs)
    counts = {c: sum(r["category"] == c for r in runs) for c in categories}
    kept = [r["kept"] for r in runs if r["kept"] is not None]
    seconds = [r["seconds"] for r in runs]
    return {
        "runs": n, "counts": counts, "percents": {c: (counts[c] / n if n else 0.0) for c in categories},
        "avg_kept": sum(kept) / len(kept) if kept else 0.0,
        "avg_seconds": sum(seconds) / n if n else 0.0, "max_seconds": max(seconds) if seconds else 0.0,
    }


def short_chunk(chunk: dict, item: dict | None = None) -> str:
    """Ví dụ "Đ.45 31/2024/QH15"; thêm dấu * sau số Điều khi đoạn đó là đoạn đúng (chỉ khi có item)."""
    doc = chunk.get("document") or {}
    label = doc.get("doc_number") or (doc.get("title") or "?")[:20]
    star = "*" if item is not None and is_hit(chunk, item) else ""
    return f"Đ.{(chunk.get('section_info') or {}).get('dieu')}{star} {label}"


def describe(chunks: list[dict] | None, item: dict | None = None) -> str:
    if chunks is None:
        return "(không chấm được)"
    return ", ".join(short_chunk(c, item) for c in chunks) or "(rỗng)"


def load_items(path: Path, required: tuple) -> list[dict]:
    items = json.loads(path.read_text(encoding="utf-8"))
    for i, item in enumerate(items, 1):
        missing = [k for k in required if k not in item]
        if missing:
            raise ValueError(f"{path.name}: câu {i} thiếu trường {missing}")
    return items


# ── Chạy thật ───────────────────────────────────────────────────────────────

async def grade_repeatedly(question: str, chunks: list[dict], repeat: int) -> list[tuple]:
    """Chấm `repeat` lần; trả [(danh sách giữ lại hoặc None, giây)]."""
    from app.services import relevance

    results = []
    for _ in range(repeat):
        started = time.perf_counter()
        graded = await relevance.grade(question, chunks, request_id=REQUEST_ID)
        results.append((graded, time.perf_counter() - started))
    return results


async def fetch_candidates(question: str, count: int) -> list[dict]:
    from app.services import rag_client

    chunks = await rag_client.search(question, top_k=count, request_id=REQUEST_ID)
    if not chunks:
        raise ValueError("rag không trả đoạn nào")
    return chunks


async def run_in_scope(item: dict, count: int, repeat: int) -> dict:
    chunks = await fetch_candidates(item["q"], count)
    has_correct = any(is_hit(c, item) for c in chunks)
    runs = []
    for graded, seconds in await grade_repeatedly(item["q"], chunks, repeat):
        runs.append({
            "category": classify_in_scope(has_correct, graded, item), "graded": graded, "seconds": seconds,
            "kept": None if graded is None else len(graded), "signature": signature(graded),
        })
    return {"item": item, "chunks": chunks, "has_correct": has_correct, "runs": runs}


async def run_out_of_scope(item: dict, count: int, repeat: int) -> dict:
    chunks = await fetch_candidates(item["q"], count)
    runs = []
    for graded, seconds in await grade_repeatedly(item["q"], chunks, repeat):
        runs.append({
            "category": classify_out_of_scope(graded), "graded": graded, "seconds": seconds,
            "kept": None if graded is None else len(graded), "signature": signature(graded),
        })
    return {"item": item, "chunks": chunks, "runs": runs}


def print_question(index: int, row: dict, in_scope: bool) -> None:
    item = row["item"]
    mark_item = item if in_scope else None
    print(f"[{index:2}] {item['q']}")
    if in_scope:
        print(f"      cần Đ.{'/'.join(item['dieu'])} {item['doc']} | "
              f"{'có đoạn đúng trong ứng viên' if row['has_correct'] else 'KHÔNG có đoạn đúng trong ứng viên'}")
    print(f"      ứng viên ({len(row['chunks'])}): {describe(row['chunks'], mark_item)}")
    for n, run in enumerate(row["runs"], 1):
        print(f"      lần {n}: {run['category'].upper()} ({run['seconds']:.2f}s) | giữ: {describe(run['graded'], mark_item)}")


def print_percent_table(title: str, summary: dict, categories: tuple) -> None:
    print(f"\n{title}: {summary['runs']} lần chấm")
    for c in categories:
        print(f"  {c:36} {summary['counts'][c]:4}  {summary['percents'][c]:>5.0%}")
    print(f"  số đoạn giữ lại trung bình {summary['avg_kept']:.1f} | thời gian chấm trung bình {summary['avg_seconds']:.2f}s, "
          f"lớn nhất {summary['max_seconds']:.2f}s")


def print_problem_list(title: str, rows: list[dict], category: str, in_scope: bool) -> None:
    print(f"\n{title} ({len(rows)} câu):")
    for row in rows:
        hit_runs = [r for r in row["runs"] if r["category"] == category]
        item = row["item"]
        print(f"  - {item['q']}  [{len(hit_runs)}/{len(row['runs'])} lần]")
        if in_scope:
            print(f"      cần Đ.{'/'.join(item['dieu'])} {item['doc']} | ứng viên: {describe(row['chunks'], item)}")
        for run in hit_runs[:1]:
            print(f"      giữ lại: {describe(run['graded'], item if in_scope else None)}")
    if not rows:
        print("  (không có)")


def print_summary(settings, args, count: int, in_rows: list[dict], out_rows: list[dict], errors: int) -> None:
    print(f"\n=== cấu hình: ứng viên {count}, relevance_chunk_chars {settings.relevance_chunk_chars}, "
          f"llm_aux_reasoning_max_tokens {settings.llm_aux_reasoning_max_tokens}, "
          f"relevance_timeout_seconds {settings.relevance_timeout_seconds:g}, lặp {args.repeat}, "
          f"provider {settings.llm_provider} ({errors} câu lỗi bỏ qua) ===")
    in_summary = summarize([r for row in in_rows for r in row["runs"]], IN_SCOPE_CATEGORIES)
    with_candidate = sum(row["has_correct"] for row in in_rows)
    print(f"\nTRONG phạm vi: {len(in_rows)} câu, {with_candidate} câu có đoạn đúng trong ứng viên")
    print_percent_table("Các lần chấm", in_summary, IN_SCOPE_CATEGORIES)
    if out_rows or args.out_file:
        out_summary = summarize([r for row in out_rows for r in row["runs"]], OUT_OF_SCOPE_CATEGORIES)
        print(f"\nNGOÀI phạm vi: {len(out_rows)} câu")
        print_percent_table("Các lần chấm", out_summary, OUT_OF_SCOPE_CATEGORIES)
    if args.repeat > 1:
        unstable = [row for row in in_rows + out_rows if is_unstable(row["runs"])]
        print(f"\nCâu không ổn định (các lần chấm giữ đoạn khác nhau): {len(unstable)}/{len(in_rows) + len(out_rows)}")
        for row in unstable:
            print(f"  - {row['item']['q']}")

    def with_category(rows, category):
        return [row for row in rows if any(r["category"] == category for r in row["runs"])]

    print_problem_list("Câu TRONG phạm vi bị CHẶN NHẦM (agent sẽ trả 'ngoài phạm vi')", with_category(in_rows, BLOCKED), BLOCKED, True)
    print_problem_list("Câu TRONG phạm vi MẤT ĐOẠN ĐÚNG", with_category(in_rows, LOST), LOST, True)
    if out_rows or args.out_file:
        print_problem_list("Câu NGOÀI phạm vi bị LỌT", with_category(out_rows, LEAKED), LEAKED, False)


async def main_async(args) -> int:
    from app.core.config import get_settings

    try:
        in_items = load_items(Path(args.file), ("q", "doc", "dieu"))
        out_items = load_items(Path(args.out_file), ("q",)) if args.out_file else []
    except (OSError, ValueError) as e:
        print(f"Lỗi đọc file câu hỏi: {e}")
        return 2
    count = min(max(args.candidates, 1), MAX_CANDIDATES)
    in_rows, out_rows, errors = [], [], 0
    for items, rows, runner, in_scope in ((in_items, in_rows, run_in_scope, True), (out_items, out_rows, run_out_of_scope, False)):
        for i, item in enumerate(items, 1):
            try:
                row = await runner(item, count, args.repeat)
            except Exception as e:  # rag lỗi...: ghi lại và đi tiếp câu sau
                errors += 1
                print(f"[{i:2}] LỖI | {item['q'][:60]}\n      {type(e).__name__}: {e}")
                continue
            rows.append(row)
            if args.verbose:
                print_question(i, row, in_scope)
    print_summary(get_settings(), args, count, in_rows, out_rows, errors)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True, help="câu hỏi TRONG phạm vi: danh sách {q, doc, dieu}")
    p.add_argument("--out-file", help="câu hỏi NGOÀI phạm vi: danh sách {q} (tuỳ chọn)")
    p.add_argument("--candidates", type=int, default=12, help="số đoạn lấy từ rag cho mỗi câu (mặc định 12, tối đa 20)")
    p.add_argument("--chunk-chars", type=int, help="đặt RELEVANCE_CHUNK_CHARS (số ký tự đầu mỗi đoạn mà bước chấm đọc)")
    p.add_argument("--reasoning", type=int, help="đặt LLM_AUX_REASONING_MAX_TOKENS (0 = tắt suy luận, chỉ OpenRouter)")
    p.add_argument("--repeat", type=int, default=1, help="số lần chấm lại mỗi câu để xem độ ổn định (mặc định 1)")
    p.add_argument("-v", "--verbose", action="store_true", help="in từng câu: các Điều ứng viên, danh sách chấm giữ lại")
    args = p.parse_args()
    args.repeat = max(args.repeat, 1)

    # Đặt biến môi trường TRƯỚC khi nạp cấu hình của app (cách eval_retrieval.py của rag-service làm)
    if args.chunk_chars is not None:
        os.environ["RELEVANCE_CHUNK_CHARS"] = str(args.chunk_chars)
    if args.reasoning is not None:
        os.environ["LLM_AUX_REASONING_MAX_TOKENS"] = str(args.reasoning)
    from app.core.config import get_settings

    get_settings.cache_clear()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
