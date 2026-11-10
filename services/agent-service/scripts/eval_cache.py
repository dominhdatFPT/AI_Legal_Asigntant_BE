"""Đo hiệu quả cache câu trả lời (ANSWER_CACHE_ENABLED) và cache tìm luật (RAG_CACHE_ENABLED): chạy agent.process_message THẬT, 2 vòng.

Vòng 1 hỏi lần lượt từng câu (lịch sử rỗng). Vòng 2 hỏi lại đúng các câu đó nhưng đổi chữ hoa đầu câu và thêm dấu "?" cuối
(để kiểm chuẩn hoá). Cache nằm trong bộ nhớ process nên hai vòng phải cùng một lần chạy script. Mỗi lượt ghi thời gian, tokens_used,
model và metadata có cache_hit hay không; bảng cuối so thời gian, token và xem câu trả lời hai vòng có giống hệt không.

CÓ GỌI AI THẬT (và rag-service thật, mỗi câu tốn 1 lượt embedding Gemini ở rag) nên mỗi vòng tốn tiền: vòng 1 tốn đủ, vòng 2 chỉ
tốn khi cache không trúng. Dùng --limit nhỏ (mặc định 5). Script KHÔNG tự bật cờ nào: đặt ANSWER_CACHE_ENABLED=true (và
RAG_CACHE_ENABLED=true nếu muốn xem cả cache tìm luật) trong môi trường trước khi chạy; đầu script in các cờ đang dùng.

Mã thoát 1 nếu ANSWER_CACHE_ENABLED bật mà có câu vòng 1 trả lời thành công (và cache được: không phải câu ngoài phạm vi) nhưng vòng 2
không trúng cache, hoặc nội dung hai vòng khác nhau khi trúng cache. Còn lại 0 (kể cả khi tắt cache, lỗi rag/LLM ở từng câu chỉ được ghi).

rag-service không mở cổng ra máy host nên chạy trong mạng Docker (ở thư mục gốc legal-assistant-ai, đã `docker compose up -d rag-service`;
container cần khoá AI trong biến môi trường, đã có trong .env của compose):
    docker compose run --rm --no-deps -e ANSWER_CACHE_ENABLED=true -e RAG_CACHE_ENABLED=true -v ./services/agent-service/app:/app/app -v ./services/agent-service/scripts:/app/scripts -v ./services/rag-service/scripts:/app/rag_scripts agent-service python scripts/eval_cache.py --file /app/rag_scripts/eval_natural.json --limit 3

Chạy ngoài Docker (cần rag-service truy cập được, đặt RAG_SERVICE_URL, INTERNAL_TOKEN và khoá AI trong .env; Windows thì thêm `-X utf8`
sau `python` để console in được tiếng Việt):
    $env:ANSWER_CACHE_ENABLED = "true"; python -X utf8 services/agent-service/scripts/eval_cache.py --limit 3
Tham số: --file (mặc định services/rag-service/scripts/eval_natural.json, danh sách {"q", ...}; lấy trường "q"), --limit N (mặc định 5, câu đầu).
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_DIR))
DEFAULT_FILE = SERVICE_DIR.parent / "rag-service" / "scripts" / "eval_natural.json"
REQUEST_ID = "eval-cache"
DEFAULT_LIMIT = 5


def vary(question: str) -> str:
    """Câu vòng 2: chữ cái đầu viết hoa, thêm dấu "?" cuối nếu chưa có. cache.normalize phải coi là cùng câu với bản gốc."""
    q = question.strip()
    if q:
        q = q[0].upper() + q[1:]
    return q if q.endswith("?") else q + "?"


def load_questions(path: Path, limit: int) -> list[str]:
    """Trường "q" của từng phần tử (định dạng eval_natural.json), lấy `limit` câu đầu."""
    return [item["q"] for item in json.loads(path.read_text(encoding="utf-8"))][:limit]


async def ask(question: str) -> dict:
    """Một lượt hỏi: {ok, seconds, tokens, model, cache_hit, type, content} hoặc {ok: False, seconds, error}."""
    from app.services import agent

    started = time.perf_counter()
    try:
        result = await agent.process_message(question, [], request_id=REQUEST_ID)
    except Exception as e:  # RAGError, LLMError hoặc lỗi bất ngờ: ghi lại và đi tiếp câu khác
        return {"ok": False, "seconds": time.perf_counter() - started, "error": f"{type(e).__name__}: {e}"}
    seconds = time.perf_counter() - started
    meta = result.get("metadata") or {}
    return {
        "ok": True, "seconds": seconds, "tokens": meta.get("tokens_used"), "model": meta.get("model"),
        "cache_hit": bool(meta.get("cache_hit")), "type": meta.get("type"), "content": result.get("content"),
    }


def cacheable(turn: dict) -> bool:
    """Lượt vòng 1 mà cache câu trả lời CÓ lưu: trả lời thành công, không phải câu ngoài phạm vi (metadata.type) và không rỗng."""
    return bool(turn["ok"] and not turn.get("type") and (turn.get("content") or "").strip())


def problems(pair: dict, answer_cache_enabled: bool) -> list[str]:
    """Lỗi cứng của một câu (chỉ xét khi ANSWER_CACHE_ENABLED bật): vòng 2 không trúng cache, hoặc trúng nhưng nội dung khác vòng 1."""
    if not answer_cache_enabled or not cacheable(pair["r1"]):
        return []
    r1, r2 = pair["r1"], pair["r2"]
    if not r2["ok"]:
        return [f"vòng 2 lỗi nên không trúng cache: {r2['error']}"]
    if not r2["cache_hit"]:
        return ["vòng 2 không trúng cache"]
    if r2["content"] != r1["content"]:
        return ["trúng cache nhưng nội dung khác vòng 1"]
    return []


def exit_code(pairs: list[dict], answer_cache_enabled: bool) -> int:
    return 1 if any(problems(p, answer_cache_enabled) for p in pairs) else 0


def totals(pairs: list[dict]) -> dict:
    """Tổng mỗi vòng (thời gian gồm cả lượt lỗi; token chỉ lượt thành công, thiếu coi là 0) và số liệu cache vòng 2."""
    rounds = {}
    for name in ("r1", "r2"):
        turns = [p[name] for p in pairs]
        rounds[name] = {
            "seconds": sum(t["seconds"] for t in turns),
            "tokens": sum((t.get("tokens") or 0) for t in turns if t["ok"]),
            "errors": sum(not t["ok"] for t in turns),
        }
    eligible = [p for p in pairs if cacheable(p["r1"])]
    hits = sum(p["r2"]["ok"] and p["r2"]["cache_hit"] for p in eligible)
    return {
        **rounds,
        "eligible": len(eligible),
        "hits": hits,
        "not_cacheable": sum(p["r1"]["ok"] for p in pairs) - len(eligible),
        "same": sum(1 for p in eligible if p["r2"]["ok"] and p["r2"]["content"] == p["r1"]["content"]),
    }


def print_flags(settings) -> None:
    def show(value):
        return value if value not in ("", None) else "(rỗng)"

    print("=== Cờ đang dùng ===")
    for name in ("RAG_CACHE_ENABLED", "ANSWER_CACHE_ENABLED", "OPENROUTER_MODEL", "OPENROUTER_AUX_MODEL",
                 "RELEVANCE_CHECK", "QUERY_REWRITE_ENABLED", "LLM_BYPASS"):
        print(f"{name:22} = {show(getattr(settings, name.lower()))}")
    if settings.llm_bypass:
        print("CẢNH BÁO: LLM_BYPASS đang bật nên cache câu trả lời KHÔNG chạy (không có lượt gọi AI để lưu); kết quả đo vô nghĩa.")
    elif not settings.answer_cache_enabled:
        print("Lưu ý: ANSWER_CACHE_ENABLED tắt nên vòng 2 sẽ không trúng cache câu trả lời; mã thoát luôn 0.")
    print()


def cell(turn: dict, field: str, fmt: str) -> str:
    return "LỖI" if not turn["ok"] else fmt.format(turn[field] or 0)


def print_table(pairs: list[dict]) -> None:
    print(f"{'#':>2} | {'giây v1':>8} {'giây v2':>8} | {'token v1':>8} {'token v2':>8} | {'cache v2':8} | {'giống':5} | câu hỏi")
    for i, p in enumerate(pairs, 1):
        r1, r2 = p["r1"], p["r2"]
        hit = "LỖI" if not r2["ok"] else ("CÓ" if r2["cache_hit"] else "không")
        same = "-" if not (r1["ok"] and r2["ok"]) else ("CÓ" if r1["content"] == r2["content"] else "KHÁC")
        print(f"{i:>2} | {cell(r1, 'seconds', '{:.1f}'):>8} {cell(r2, 'seconds', '{:.1f}'):>8} | "
              f"{cell(r1, 'tokens', '{}'):>8} {cell(r2, 'tokens', '{}'):>8} | {hit:8} | {same:5} | {p['question'][:60]}")
    print()
    for i, p in enumerate(pairs, 1):
        for name in ("r1", "r2"):
            if not p[name]["ok"]:
                print(f"[{i}] vòng {name[1]} LỖI: {p[name]['error']}")
        if p["r1"]["ok"] and not cacheable(p["r1"]):
            print(f"[{i}] vòng 1 không cache được (metadata.type = {p['r1'].get('type')!r} hoặc câu rỗng), không tính vào tỉ lệ trúng")


def print_totals(pairs: list[dict]) -> None:
    t = totals(pairs)
    print(f"\n=== {len(pairs)} câu ===")
    for name, label in (("r1", "vòng 1"), ("r2", "vòng 2")):
        print(f"{label}: tổng {t[name]['seconds']:.1f}s, tổng token {t[name]['tokens']}, lượt lỗi {t[name]['errors']}")
    rate = f"{t['hits'] / t['eligible']:.0%}" if t["eligible"] else "n/a"
    print(f"trúng cache vòng 2: {t['hits']}/{t['eligible']} ({rate}) | câu không cache được (ngoài phạm vi...): {t['not_cacheable']}")
    print(f"nội dung hai vòng giống hệt: {t['same']}/{t['eligible']}")


async def main_async(args) -> int:
    from app.core.config import get_settings

    settings = get_settings()
    print_flags(settings)
    questions = load_questions(Path(args.file), args.limit)
    pairs = []
    for round_name, make in (("r1", lambda q: q), ("r2", vary)):
        for i, question in enumerate(questions):
            turn = await ask(make(question))
            if round_name == "r1":
                pairs.append({"question": question, "r1": turn})
            else:
                pairs[i]["r2"] = turn
        print(f"đã xong vòng {round_name[1]}", flush=True)
    print()
    print_table(pairs)
    print_totals(pairs)

    failed = [(i, p) for i, p in enumerate(pairs, 1) if problems(p, settings.answer_cache_enabled)]
    for i, p in failed:
        for message in problems(p, settings.answer_cache_enabled):
            print(f"! [{i}] {message}")
    return exit_code(pairs, settings.answer_cache_enabled)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--file", default=str(DEFAULT_FILE), help="JSON danh sách {\"q\": câu hỏi, ...} (mặc định eval_natural.json của rag-service)")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="chỉ chạy N câu đầu (có gọi AI thật nên tốn tiền)")
    args = p.parse_args()
    if args.limit < 1:
        p.error("--limit phải >= 1")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
