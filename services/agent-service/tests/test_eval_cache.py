"""scripts/eval_cache.py: phần tính tổng và quy tắc mã thoát, agent.process_message giả lập (không gọi mạng, không gọi AI)."""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from app.services import agent, cache, llm_client, rag_client

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "eval_cache.py"
QUESTIONS = ["bố mẹ muốn cắt một phần đất cho tôi", "đất chưa có sổ đỏ thì có bán được không", "công ty tôi muốn đổi tên"]


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("eval_cache", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def set_flags(monkeypatch, **env):
    from app.core.config import get_settings

    for name, value in env.items():
        monkeypatch.setenv(name, str(value))
    get_settings.cache_clear()


@pytest.fixture
def questions_file(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps([{"q": q, "doc": "x", "dieu": ["1"]} for q in QUESTIONS], ensure_ascii=False), encoding="utf-8")
    return path


class FakeAgent:
    """Giả agent.process_message có cache theo cache.normalize như thật. `broken`: câu (đã chuẩn hoá) ném lỗi; `no_cache`: không lưu."""

    def __init__(self, cache_on=True, broken=(), no_cache=False, different_on_hit=False, type_of=None):
        self.cache_on, self.broken, self.no_cache = cache_on, set(broken), no_cache
        self.different_on_hit, self.type_of = different_on_hit, type_of or {}
        self.store, self.calls = {}, []

    async def __call__(self, user_content, history, request_id=None, documents=None):
        self.calls.append((user_content, list(history), request_id))
        key = cache.normalize(user_content)
        if key in self.broken:
            raise rag_client.RAGError("rag chết")
        if self.cache_on and key in self.store:
            content, meta = self.store[key]
            return {"content": content + (" (khác)" if self.different_on_hit else ""),
                    "metadata": {**meta, "cache_hit": True, "tokens_used": 0}}
        meta = {"model": "m", "tokens_used": 100}
        if key in self.type_of:
            meta["type"] = self.type_of[key]
        content = f"Trả lời cho {key}"
        if self.cache_on and not self.no_cache and "type" not in meta:
            self.store[key] = (content, meta)
        return {"content": content, "metadata": meta}


def run(script, file, limit=10):
    return asyncio.run(script.main_async(argparse.Namespace(file=str(file), limit=limit)))


def install(monkeypatch, fake):
    monkeypatch.setattr(agent, "process_message", fake)
    return fake


# ── vary / load_questions ─────────────────────────────────────────────────────

@pytest.mark.parametrize("question, expected", [
    ("bố mẹ muốn cắt đất", "Bố mẹ muốn cắt đất?"),
    ("đất đai", "Đất đai?"),  # chữ đầu có dấu: "đ" -> "Đ"
    ("đã có hỏi rồi?", "Đã có hỏi rồi?"),  # đã có dấu ? thì không thêm
    ("  công ty tôi  ", "Công ty tôi?"),
])
def test_vary_capitalizes_first_letter_and_adds_question_mark(script, question, expected):
    assert script.vary(question) == expected


@pytest.mark.parametrize("question", QUESTIONS + ["Đất đai?", "a"])
def test_vary_keeps_the_question_equal_after_normalize(script, question):
    assert cache.normalize(script.vary(question)) == cache.normalize(question)


def test_load_questions_reads_q_field_and_applies_limit(script, questions_file):
    assert script.load_questions(questions_file, 2) == QUESTIONS[:2]
    assert script.load_questions(questions_file, 99) == QUESTIONS


def test_default_file_is_rag_eval_natural_and_has_q_field(script):
    assert script.DEFAULT_FILE.name == "eval_natural.json" and script.DEFAULT_FILE.parent.parent.name == "rag-service"
    if script.DEFAULT_FILE.exists():
        assert all("q" in item for item in json.loads(script.DEFAULT_FILE.read_text(encoding="utf-8")))


# ── totals ────────────────────────────────────────────────────────────────────

def turn(seconds=1.0, tokens=100, hit=False, content="a", type_=None):
    return {"ok": True, "seconds": seconds, "tokens": tokens, "model": "m", "cache_hit": hit, "type": type_, "content": content}


def error_turn(seconds=0.5):
    return {"ok": False, "seconds": seconds, "error": "RAGError: chết"}


def test_totals_sum_seconds_tokens_and_hit_rate(script):
    pairs = [
        {"question": "1", "r1": turn(10, 500), "r2": turn(0.1, 0, hit=True)},
        {"question": "2", "r1": turn(8, None), "r2": turn(8, 300)},  # tokens thiếu coi là 0; vòng 2 không trúng
    ]

    t = script.totals(pairs)

    assert t["r1"] == {"seconds": 18, "tokens": 500, "errors": 0}
    assert t["r2"]["seconds"] == pytest.approx(8.1) and t["r2"]["tokens"] == 300
    assert (t["eligible"], t["hits"], t["same"], t["not_cacheable"]) == (2, 1, 2, 0)


def test_totals_errors_are_counted_and_do_not_add_tokens(script):
    pairs = [
        {"question": "1", "r1": error_turn(2), "r2": error_turn(3)},
        {"question": "2", "r1": turn(1, 10), "r2": turn(0.1, 0, hit=True)},
    ]

    t = script.totals(pairs)

    assert t["r1"] == {"seconds": 3, "tokens": 10, "errors": 1} and t["r2"]["errors"] == 1
    assert (t["eligible"], t["hits"]) == (1, 1)  # câu lỗi vòng 1 không tính vào mẫu số


def test_totals_out_of_scope_is_not_eligible(script):
    pairs = [{"question": "1", "r1": turn(type_="out_of_scope"), "r2": turn()}]

    t = script.totals(pairs)

    assert (t["eligible"], t["hits"], t["not_cacheable"]) == (0, 0, 1)


# ── quy tắc mã thoát ──────────────────────────────────────────────────────────

def pair(r1, r2):
    return {"question": "q", "r1": r1, "r2": r2}


def test_exit_0_when_every_eligible_question_hits_with_same_content(script):
    pairs = [pair(turn(), turn(hit=True)), pair(turn(content="b"), turn(hit=True, content="b"))]
    assert script.exit_code(pairs, True) == 0


def test_exit_1_when_round_2_misses_cache(script):
    assert script.exit_code([pair(turn(), turn(hit=False))], True) == 1


def test_exit_1_when_round_2_errors_after_round_1_success(script):
    pairs = [pair(turn(), error_turn())]
    assert script.exit_code(pairs, True) == 1
    assert "lỗi" in script.problems(pairs[0], True)[0]


def test_exit_1_when_hit_but_content_differs(script):
    pairs = [pair(turn(content="a"), turn(hit=True, content="b"))]
    assert script.exit_code(pairs, True) == 1
    assert "khác" in script.problems(pairs[0], True)[0]


def test_exit_0_when_answer_cache_disabled_even_if_no_hit(script):
    assert script.exit_code([pair(turn(), turn(hit=False)), pair(turn(), turn(hit=True, content="khác"))], False) == 0


def test_exit_ignores_questions_whose_round_1_failed(script):
    assert script.exit_code([pair(error_turn(), turn(hit=False)), pair(error_turn(), error_turn())], True) == 0


def test_exit_ignores_out_of_scope_questions(script):
    assert script.exit_code([pair(turn(type_="out_of_scope"), turn(type_="out_of_scope"))], True) == 0


def test_exit_0_with_no_questions(script):
    assert script.exit_code([], True) == 0


# ── chạy cả script với agent giả ──────────────────────────────────────────────

def test_main_with_working_cache_exit_0_and_prints_table_and_totals(script, questions_file, monkeypatch, capsys):
    set_flags(monkeypatch, ANSWER_CACHE_ENABLED="true")
    fake = install(monkeypatch, FakeAgent())

    assert run(script, questions_file) == 0

    out = capsys.readouterr().out
    assert len(fake.calls) == 6 and all(c[1] == [] and c[2] == "eval-cache" for c in fake.calls)  # lịch sử rỗng, 2 vòng x 3 câu
    assert [c[0] for c in fake.calls[3:]] == [script.vary(q) for q in QUESTIONS]  # vòng 2 dùng câu đã đổi
    assert "trúng cache vòng 2: 3/3 (100%)" in out and "nội dung hai vòng giống hệt: 3/3" in out
    assert "vòng 1: tổng" in out and "vòng 2: tổng" in out and "tổng token 300" in out and "tổng token 0" in out
    assert out.count("CÓ") >= 6  # cột cache v2 và cột giống, mỗi câu một dòng


def test_main_respects_limit(script, questions_file, monkeypatch):
    set_flags(monkeypatch, ANSWER_CACHE_ENABLED="true")
    fake = install(monkeypatch, FakeAgent())

    assert run(script, questions_file, limit=2) == 0

    assert len(fake.calls) == 4


def test_main_exit_1_when_cache_on_but_nothing_is_hit(script, questions_file, monkeypatch, capsys):
    set_flags(monkeypatch, ANSWER_CACHE_ENABLED="true")
    install(monkeypatch, FakeAgent(cache_on=False))  # agent không cache dù cờ đã bật

    assert run(script, questions_file) == 1

    out = capsys.readouterr().out
    assert "vòng 2 không trúng cache" in out and "trúng cache vòng 2: 0/3 (0%)" in out


def test_main_exit_1_when_hit_content_differs(script, questions_file, monkeypatch, capsys):
    set_flags(monkeypatch, ANSWER_CACHE_ENABLED="true")
    install(monkeypatch, FakeAgent(different_on_hit=True))

    assert run(script, questions_file) == 1
    assert "trúng cache nhưng nội dung khác vòng 1" in capsys.readouterr().out


def test_main_exit_0_when_cache_flag_off_and_warns(script, questions_file, monkeypatch, capsys):
    set_flags(monkeypatch, ANSWER_CACHE_ENABLED="false")
    install(monkeypatch, FakeAgent(cache_on=False))

    assert run(script, questions_file) == 0
    assert "ANSWER_CACHE_ENABLED tắt" in capsys.readouterr().out


def test_main_error_on_one_question_is_recorded_and_run_continues(script, questions_file, monkeypatch, capsys):
    set_flags(monkeypatch, ANSWER_CACHE_ENABLED="true")
    fake = install(monkeypatch, FakeAgent(broken=[cache.normalize(QUESTIONS[1])]))

    assert run(script, questions_file) == 0  # câu lỗi ở vòng 1 không bị coi là cache hỏng

    out = capsys.readouterr().out
    assert len(fake.calls) == 6
    assert "RAGError: rag chết" in out and "vòng 1 LỖI" in out and "vòng 2 LỖI" in out
    assert "trúng cache vòng 2: 2/2 (100%)" in out and "lượt lỗi 1" in out


def test_main_out_of_scope_answers_are_not_a_failure(script, questions_file, monkeypatch, capsys):
    set_flags(monkeypatch, ANSWER_CACHE_ENABLED="true")
    install(monkeypatch, FakeAgent(type_of={cache.normalize(QUESTIONS[0]): "out_of_scope"}))

    assert run(script, questions_file) == 0
    out = capsys.readouterr().out
    assert "không cache được" in out and "trúng cache vòng 2: 2/2" in out


def test_main_prints_all_flags_and_warns_on_bypass(script, questions_file, monkeypatch, capsys):
    set_flags(monkeypatch, LLM_BYPASS="true", OPENROUTER_AUX_MODEL="model-phu", OPENROUTER_MODEL="model-chinh",
              RAG_CACHE_ENABLED="true", RELEVANCE_CHECK="true", QUERY_REWRITE_ENABLED="true")
    install(monkeypatch, FakeAgent(cache_on=False))

    run(script, questions_file, limit=1)

    out = capsys.readouterr().out
    for name in ("RAG_CACHE_ENABLED", "ANSWER_CACHE_ENABLED", "OPENROUTER_MODEL", "OPENROUTER_AUX_MODEL", "RELEVANCE_CHECK",
                 "QUERY_REWRITE_ENABLED", "LLM_BYPASS"):
        assert name in out
    assert "model-phu" in out and "model-chinh" in out
    assert "CẢNH BÁO: LLM_BYPASS đang bật" in out


def test_main_no_bypass_warning_by_default(script, questions_file, monkeypatch, capsys):
    set_flags(monkeypatch)
    install(monkeypatch, FakeAgent())

    run(script, questions_file, limit=1)

    assert "CẢNH BÁO" not in capsys.readouterr().out


def test_script_never_touches_llm_or_rag_modules(script, questions_file, monkeypatch):
    set_flags(monkeypatch, ANSWER_CACHE_ENABLED="true")
    generate, search = llm_client.generate, rag_client.search
    install(monkeypatch, FakeAgent())

    run(script, questions_file)

    assert llm_client.generate is generate and rag_client.search is search


def test_main_rejects_limit_below_one(script, monkeypatch):
    monkeypatch.setattr("sys.argv", ["eval_cache.py", "--limit", "0"])
    with pytest.raises(SystemExit) as e:
        script.main()
    assert e.value.code == 2
