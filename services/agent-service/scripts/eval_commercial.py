"""Offline rubric screening, with an explicit opt-in for real RAG/LLM calls.

Results format: [{"id": "TM01", "content": "...", "metadata": {...}}].
An excellent verdict requires expert_review={"verdict":"excellent","reviewer":"..."}.
Regex matches alone cannot certify legal correctness or model quality.
"""
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_DIR))

from app.services import prompts

DEFAULT_CASES = Path(__file__).with_name("commercial_cases.json")


def evaluate(case: dict, result: dict | None) -> dict:
    report = {"id": case["id"], "question": case["question"]}
    if result is None:
        return {**report, "category": "not_run"}
    metadata = result.get("metadata") or {}
    answer = result.get("content") or ""
    if metadata.get("type") == "out_of_scope" or answer.strip() == prompts.OUT_OF_SCOPE_ANSWER:
        return {**report, "category": "out_of_scope", "tokens_used": metadata.get("tokens_used")}
    cited = set(re.findall(r"\bĐiều\s+(\d+)\b", answer, re.IGNORECASE))
    expected = set(case["articles"])
    missing_articles = sorted(expected - cited)
    failed_checks = [pattern for pattern in case.get("checks", []) if not re.search(pattern, answer, re.IGNORECASE)]
    # Explicit evidence only; empty/missing retrieval evidence is never fabricated.
    evidence = result.get("retrieved_articles")
    if evidence is not None and not expected.issubset(set(evidence)):
        category = "retrieval_gap"
    elif not answer.strip():
        category = "invalid_output"
    elif missing_articles:
        category = "citation_review"
    elif failed_checks:
        category = "content_review"
    else:
        review = result.get("expert_review") or {}
        category = "excellent" if review.get("verdict") == "excellent" and review.get("reviewer") else "needs_expert_review"
    return {**report, "category": category, "missing_articles": missing_articles,
            "failed_checks": failed_checks, "tokens_used": metadata.get("tokens_used"),
            "model": metadata.get("model")}


def evaluate_all(cases: list[dict], results: list[dict]) -> list[dict]:
    ids = [r["id"] for r in results]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate result IDs")
    known = {c["id"] for c in cases}
    if len(known) != len(cases) or set(ids) - known:
        raise ValueError("Duplicate case IDs or unknown result IDs")
    indexed = {r["id"]: r for r in results}
    return [evaluate(case, indexed.get(case["id"])) for case in cases]


async def run_live(cases: list[dict]) -> list[dict]:
    from app.services import agent
    results = []
    for case in cases:
        try:
            result = await agent.process_message(case["question"], [], request_id=f"eval-{case['id']}")
        except Exception as exc:
            result = {"content": "", "metadata": {}, "error": type(exc).__name__}
        results.append({"id": case["id"], **result})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--results", type=Path)
    source.add_argument("--live", action="store_true", help="Calls real configured RAG and LLM; incurs provider costs")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dataset = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    if args.live:
        from app.core.config import get_settings
        settings = get_settings()
        if settings.llm_bypass or settings.llm_provider.strip() != "openrouter" or settings.openrouter_model != "deepseek/deepseek-v4.1-flash":
            parser.error("Live DeepSeek evaluation requires LLM_BYPASS=false, LLM_PROVIDER=openrouter and OPENROUTER_MODEL=deepseek/deepseek-v4.1-flash")
        results = asyncio.run(run_live(cases))
    else:
        results = json.loads(args.results.read_text(encoding="utf-8")) if args.results else []
    report = evaluate_all(cases, results)
    args.output.write_text(json.dumps({"origin": dataset.get("origin"), "cases": report,
                                      "raw_results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if any(r["category"] in {"invalid_output", "citation_review", "content_review", "retrieval_gap", "not_run", "out_of_scope"} for r in report) else 0


if __name__ == "__main__":
    raise SystemExit(main())
