"""Offline regression checks; these do not claim to benchmark a live model."""
import importlib.util
import json
import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "eval_commercial.py"
spec = importlib.util.spec_from_file_location("eval_commercial", SCRIPT)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)
DATASET = json.loads(SCRIPT.with_name("commercial_cases.json").read_text(encoding="utf-8"))
CASES = {case["id"]: case for case in DATASET["cases"]}


def test_seventeen_cases_are_explicitly_supplemental_and_not_run():
    assert len(CASES) == 17
    assert "không phải bộ 17 câu gốc" in DATASET["origin"]
    report = evaluation.evaluate_all(list(CASES.values()), [])
    assert len(report) == 17 and all(r["category"] == "not_run" for r in report)


@pytest.mark.parametrize("case", list(CASES.values()), ids=list(CASES))
def test_commercial_case_is_usable_by_the_chat_api(case):
    assert 0 < len(case["question"]) <= 2000
    assert case["articles"] and case["checks"]
    for pattern in case["checks"]:
        re.compile(pattern)


def test_penalty_uses_violated_obligation_not_thirty_billion_total():
    case = CASES["TM16"]
    good = {"content": "Trần 8% × 6 tỷ = 480 triệu đồng (Điều 301 Luật Thương mại)."}
    wrong = {"content": "Trần 8% × 30 tỷ = 2,4 tỷ đồng (Điều 301 Luật Thương mại)."}
    assert evaluation.evaluate(case, good)["category"] == "needs_expert_review"
    assert evaluation.evaluate(case, wrong)["category"] == "content_review"


def test_wrong_article_is_flagged_even_with_correct_amount():
    result = {"content": "Trần 8%, tương ứng 480 triệu đồng (Điều 306)."}
    assert evaluation.evaluate(CASES["TM16"], result)["category"] == "citation_review"


def test_complaint_period_is_not_confused_with_limitation_period():
    wrong = {"content": "Thời hạn 2 năm (Điều 318)."}
    assert evaluation.evaluate(CASES["TM11"], wrong)["category"] == "content_review"


def test_missing_corpus_is_separate_from_bad_answer():
    result = {"content": "Điều 301, trần 8%, 480 triệu.", "retrieved_articles": ["306"]}
    assert evaluation.evaluate(CASES["TM16"], result)["category"] == "retrieval_gap"


def test_out_of_scope_is_not_a_successful_legal_answer():
    result = {"content": evaluation.prompts.OUT_OF_SCOPE_ANSWER, "metadata": {"tokens_used": 6575}}
    report = evaluation.evaluate(CASES["TM17"], result)
    assert report["category"] == "out_of_scope" and report["tokens_used"] == 6575


def test_excellence_requires_named_expert_review():
    result = {"content": "Điều 301, trần 8%, 480 triệu đồng."}
    assert evaluation.evaluate(CASES["TM16"], result)["category"] == "needs_expert_review"
    result["expert_review"] = {"verdict": "excellent", "reviewer": "Reviewer A"}
    assert evaluation.evaluate(CASES["TM16"], result)["category"] == "excellent"
    result["content"] = "Điều 301, phạt 10%, 600 triệu đồng."
    assert evaluation.evaluate(CASES["TM16"], result)["category"] == "content_review"


def test_duplicate_and_unknown_results_are_rejected():
    with pytest.raises(ValueError):
        evaluation.evaluate_all(list(CASES.values()), [{"id": "TM01"}, {"id": "TM01"}])
    with pytest.raises(ValueError):
        evaluation.evaluate_all(list(CASES.values()), [{"id": "unknown"}])
