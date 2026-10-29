"""Chấm agent-service mà KHÔNG qua LLM: chạy agent.process_message thật với LLM_BYPASS=true.

Agent trả thẳng các đoạn luật rag-service tìm được, nên không có chữ nào do AI thêm vào để che lỗi.
Script đo: chọn nhánh có đúng không (vô nghĩa / hỏi đáp / rà soát hợp đồng), đoạn luật có đúng
Điều không (hit@k, MRR), prompt có đủ đoạn luật không. rag-service chạy thật qua HTTP (Gemini chỉ dùng để embedding).
Mã thoát 1 nếu có ca sai nhánh, có ca gọi LLM, hoặc prompt thiếu đoạn luật.

rag-service không mở cổng ra máy host nên chạy trong mạng Docker (ở thư mục gốc legal-assistant-ai, đã `docker compose up -d rag-service`):
    docker compose run --rm --no-deps -v ./services/agent-service/app:/app/app -v ./services/agent-service/scripts:/app/scripts -v ./services/rag-service/scripts:/app/rag_scripts agent-service python scripts/eval_pipeline.py -v --rag-questions /app/rag_scripts/eval_questions.json

Chạy ngoài Docker (cần rag-service truy cập được, vd đã mở cổng): đặt RAG_SERVICE_URL, INTERNAL_TOKEN trong .env rồi
    python services/agent-service/scripts/eval_pipeline.py -v
Tuỳ chọn: --top-k N (mặc định RAG_TOP_K), --rag-questions <eval_questions.json> để thêm 20 câu hỏi đáp của rag-service.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_DIR))
CASES = Path(__file__).resolve().parent / "agent_cases.json"
DEFAULT_RAG_QUESTIONS = SERVICE_DIR.parent / "rag-service" / "scripts" / "eval_questions.json"


def is_hit(chunk: dict, case: dict) -> bool:
    """Giống eval_retrieval.is_hit của rag-service: đúng văn bản (số hiệu hoặc tên) và đúng Điều."""
    doc = chunk.get("document") or {}
    want = case["expect_doc"]
    same_doc = want in (doc.get("doc_number") or "") or want in (doc.get("title") or "")
    return same_doc and (chunk.get("section_info") or {}).get("dieu") in case["expect_dieu"]


def branch_of(result: dict) -> str:
    from app.services import query_filter

    meta = result["metadata"]
    if meta.get("mode") == "contract_review":
        return "contract_review"
    if result["content"] == query_filter.DEFAULT_ANSWER:
        return "meaningless"
    return "rag"


def load_cases(rag_questions: Path | None) -> list[dict]:
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    if rag_questions:
        for item in json.loads(rag_questions.read_text(encoding="utf-8")):
            cases.append({"q": item["q"], "expect_branch": "rag", "expect_doc": item["doc"], "expect_dieu": item["dieu"]})
    return cases


async def run_case(case: dict, recorded: dict) -> dict:
    from app.services import agent

    documents = [
        {"document_id": f"eval-{i}", "source_kind": "docx", **d} for i, d in enumerate(case.get("documents") or [], 1)
    ] or None
    recorded.clear()
    return await agent.process_message(case["q"], [], request_id="eval-pipeline", documents=documents)


def check(case: dict, result: dict, chunks: list[dict]) -> dict:
    """Trả {branch, problems (lỗi cứng), rank (vị trí đoạn đúng đầu tiên, None nếu trượt), has_target}."""
    from app.services import prompts

    meta = result["metadata"]
    branch = branch_of(result)
    problems = []
    if branch != case["expect_branch"]:
        problems.append(f"sai nhánh: ra {branch}, cần {case['expect_branch']}")
    if branch in ("rag", "contract_review"):
        if not meta.get("llm_bypass") or meta.get("model") != "no-llm":
            problems.append("không ở chế độ bypass (có thể đã gọi LLM)")
        prompt = meta.get("system_prompt") or ""
        missing = [i for i, c in enumerate(chunks, 1) if prompts.format_chunk(i, c).splitlines()[0] not in prompt]
        if missing:
            problems.append(f"prompt thiếu đoạn luật số {missing}")
        if case.get("documents") and "<<<TÀI LIỆU 1" not in prompt:
            problems.append("prompt thiếu phần tài liệu")
    if branch == "contract_review" and "expect_contract_type" in case:
        if meta.get("contract_type") != case["expect_contract_type"]:
            problems.append(f"loại hợp đồng ra {meta.get('contract_type')}, cần {case['expect_contract_type']}")
    for text in case.get("expect_contains", []):
        if text not in result["content"]:
            problems.append(f"đáp án không chứa {text!r}")
    has_target = "expect_dieu" in case and branch in ("rag", "contract_review")
    rank = next((r for r, c in enumerate(chunks, 1) if is_hit(c, case)), None) if has_target else None
    return {"branch": branch, "problems": problems, "rank": rank, "has_target": has_target}


async def main_async(args) -> int:
    from app.services import llm_client, rag_client

    async def forbidden(*a, **k):
        raise AssertionError("llm_client.generate bị gọi trong lúc chấm không qua LLM")

    # chốt chặn thứ hai, ngoài LLM_BYPASS; trả lại nguyên trạng ở cuối để test import script không làm bẩn module
    original_generate, original_search = llm_client.generate, rag_client.search
    llm_client.generate = forbidden

    recorded: dict[str, dict] = {}

    async def recording_search(*a, **k):
        chunks = await original_search(*a, **k)
        recorded.update({c["id"]: c for c in chunks if c.get("id")})
        return chunks

    rag_client.search = recording_search
    try:
        cases = load_cases(Path(args.rag_questions) if args.rag_questions else None)
        top_k = args.top_k or int(os.environ["RAG_TOP_K"])
        rows, errors = [], 0
        for i, case in enumerate(cases, 1):
            try:
                result = await run_case(case, recorded)
            except Exception as e:  # rag lỗi, LLM bị gọi...: ghi lại và đi tiếp
                errors += 1
                print(f"[{i:2}] LỖI | {case['q'][:60]}\n      {type(e).__name__}: {e}")
                continue
            chunks = [recorded[cid] for cid in result["metadata"].get("rag_chunk_ids", []) if cid in recorded]
            row = check(case, result, chunks) | {"case": case}
            rows.append(row)
            status = "LỖI" if row["problems"] else ("TRƯỢT" if row["has_target"] and not row["rank"] else "ĐẠT")
            print(f"[{i:2}] {status:5} | {row['branch']:15} | {case['q'][:70]}")
            for p in row["problems"]:
                print(f"      ! {p}")
            if row["has_target"]:
                found = ", ".join(
                    f"Đ.{(c.get('section_info') or {}).get('dieu')} {(c.get('document') or {}).get('doc_number')} ({c.get('score', 0):.2f})"
                    for c in chunks
                )
                print(f"      cần Đ.{'/'.join(case['expect_dieu'])} {case['expect_doc']} | tìm được: {found or '(không có)'}")
            if args.verbose:
                if result["metadata"].get("queries"):
                    print("      câu tìm: " + " | ".join(result["metadata"]["queries"]))
                print("      " + result["content"].replace("\n", "\n      "))

        wrong = [r for r in rows if r["problems"]]
        right_branch = sum(r["branch"] == r["case"]["expect_branch"] for r in rows)
        targeted = [r for r in rows if r["has_target"]]
        hits = sum(r["rank"] is not None for r in targeted)
        mrr = sum(1 / r["rank"] for r in targeted if r["rank"]) / len(targeted) if targeted else 0.0
        print(f"\n=== {len(cases)} ca, top_k={top_k}, không qua LLM ===")
        print(f"đúng nhánh: {right_branch}/{len(cases)} | ca lỗi cứng: {len(wrong)} | ca bị exception: {errors}")
        if targeted:
            print(f"đoạn luật đúng: hit@{top_k} {hits}/{len(targeted)} ({hits / len(targeted):.0%}) | MRR {mrr:.2f}")
        return 1 if wrong or errors else 0

    finally:
        llm_client.generate, rag_client.search = original_generate, original_search


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--top-k", type=int, default=0, help="ghi đè RAG_TOP_K (mặc định theo cấu hình agent)")
    p.add_argument("--rag-questions", nargs="?", const=str(DEFAULT_RAG_QUESTIONS), default=None,
                   help="thêm các câu trong eval_questions.json của rag-service (bỏ trống đường dẫn = dùng đường dẫn mặc định)")
    p.add_argument("-v", "--verbose", action="store_true", help="in nguyên đáp án thô và các câu tìm")
    args = p.parse_args()

    os.environ["LLM_BYPASS"] = "true"
    if args.top_k:
        os.environ["RAG_TOP_K"] = str(args.top_k)
    from app.core.config import get_settings

    get_settings.cache_clear()
    os.environ["RAG_TOP_K"] = str(get_settings().rag_top_k)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
