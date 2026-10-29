"""scripts/eval_pipeline.py: chấm agent không qua LLM, rag-service giả lập."""
import argparse
import asyncio
import importlib.util
from pathlib import Path

import pytest

from app.services import llm_client, rag_client

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "eval_pipeline.py"


@pytest.fixture
def pipeline(monkeypatch):
    monkeypatch.setenv("LLM_BYPASS", "true")
    monkeypatch.setenv("RAG_TOP_K", "4")
    from app.core.config import get_settings

    get_settings.cache_clear()
    spec = importlib.util.spec_from_file_location("eval_pipeline", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_rag(monkeypatch, chunk):
    async def search(query, top_k=5, request_id=None, transport=None):
        return [{**chunk, "section_info": {"dieu": "45"}, "document": {**chunk["document"], "doc_number": "31/2024/QH15"}}]

    monkeypatch.setattr(rag_client, "search", search)


def run(pipeline):
    args = argparse.Namespace(rag_questions=None, top_k=0, verbose=False)
    return asyncio.run(pipeline.main_async(args))


def test_all_branches_pass_with_working_rag(pipeline, fake_rag, capsys):
    assert run(pipeline) == 0
    out = capsys.readouterr().out
    # rag giả chỉ trả Điều 45: ca "khoản 1 điều 220" là TRƯỢT (chỉ báo, không làm hỏng lượt chạy)
    assert "đúng nhánh: 9/9" in out and "hit@4 2/3" in out


def test_wrong_branch_fails_the_run(pipeline, fake_rag, monkeypatch, capsys):
    cases = [{"q": "asdfgh qwerty zxcvb", "expect_branch": "rag"}]
    monkeypatch.setattr(pipeline, "load_cases", lambda path: cases)

    assert run(pipeline) == 1
    assert "sai nhánh: ra meaningless, cần rag" in capsys.readouterr().out


def test_missing_target_article_is_reported_as_miss_not_failure(pipeline, fake_rag, monkeypatch, capsys):
    cases = [{"q": "mua đất cần gì", "expect_branch": "rag", "expect_doc": "31/2024/QH15", "expect_dieu": ["999"]}]
    monkeypatch.setattr(pipeline, "load_cases", lambda path: cases)

    assert run(pipeline) == 0
    out = capsys.readouterr().out
    assert "TRƯỢT" in out and "hit@4 0/1" in out


def test_run_restores_llm_client_and_rag_client(pipeline, fake_rag):
    generate, search = llm_client.generate, rag_client.search

    run(pipeline)

    assert llm_client.generate is generate and rag_client.search is search
