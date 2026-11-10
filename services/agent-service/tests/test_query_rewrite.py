"""Test query_rewrite.rewrite với llm_client.generate giả lập (không gọi mạng)."""
import asyncio
import logging

import pytest

from app.core.config import get_settings
from app.services import llm_client, prompts, query_rewrite

QUESTION = "chuyển văn phòng thì làm sao"
REWRITTEN = "Thủ tục thay đổi địa chỉ trụ sở chính của doanh nghiệp khi chuyển văn phòng"


@pytest.fixture
def fake_generate(monkeypatch):
    """llm_client.generate giả: ghi lại lần gọi; đặt state["reply"] (chuỗi) hoặc state["error"] để điều khiển."""
    state = {"reply": REWRITTEN, "error": None, "calls": []}

    async def generate(system_prompt, history, user_content, **options):
        state["calls"].append({
            "system_prompt": system_prompt, "history": history, "user_content": user_content, "options": options,
        })
        if state["error"] is not None:
            raise state["error"]
        return state["reply"], 42, "rewrite-model"

    monkeypatch.setattr(llm_client, "generate", generate)
    return state


def run(history=None, question=QUESTION, request_id=None):
    return asyncio.run(query_rewrite.rewrite(question, history or [], request_id=request_id))


def test_valid_rewrite_is_used(fake_generate):
    assert run() == REWRITTEN
    call = fake_generate["calls"][0]
    assert call["system_prompt"] == prompts.REWRITE_PROMPT and call["user_content"] == QUESTION


@pytest.mark.parametrize("reply", [
    f'"{REWRITTEN}"',
    f"“{REWRITTEN}”",
    f"'{REWRITTEN}'",
    f"  {REWRITTEN}  \n",
    f"Câu hỏi: {REWRITTEN}",
    f"câu hỏi:{REWRITTEN}",
    f"Câu tìm kiếm: {REWRITTEN}",
    f'Câu hỏi: "{REWRITTEN}"',
    f"\n\n{REWRITTEN}\nGiải thích: đây là câu viết lại.\n",
    f"\n  \n{REWRITTEN}",
])
def test_reply_is_cleaned(fake_generate, reply):
    fake_generate["reply"] = reply

    assert run() == REWRITTEN


def test_multiline_reply_takes_first_non_empty_line(fake_generate):
    fake_generate["reply"] = f"\n\n  {REWRITTEN}  \nDòng hai không dùng\nDòng ba"

    assert run() == REWRITTEN


def test_inner_quotes_and_colons_are_kept(fake_generate):
    fake_generate["reply"] = 'Điều kiện "tách thửa": diện tích tối thiểu của đất ở'

    assert run() == 'Điều kiện "tách thửa": diện tích tối thiểu của đất ở'


@pytest.mark.parametrize("error", [llm_client.LLMError("503"), asyncio.TimeoutError()])
def test_llm_error_or_timeout_returns_original_and_warns(fake_generate, caplog, error):
    fake_generate["error"] = error
    caplog.set_level(logging.WARNING, logger="app.services.query_rewrite")

    assert run(request_id="req-w") == QUESTION
    warning = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert "request_id=req-w" in warning.getMessage()


@pytest.mark.parametrize("reply", ["", "   \n  \n", '""', "Câu hỏi:", "abcd", "x" * 501, '"' + "y" * 600 + '"'])
def test_empty_too_short_or_too_long_returns_original_and_warns(fake_generate, caplog, reply):
    fake_generate["reply"] = reply
    caplog.set_level(logging.WARNING, logger="app.services.query_rewrite")

    assert run(request_id="req-b") == QUESTION
    warning = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert "request_id=req-b" in warning.getMessage()


def test_length_limits_are_inclusive(fake_generate):
    fake_generate["reply"] = "abcde"
    assert run() == "abcde"
    fake_generate["reply"] = "x" * 500
    assert run() == "x" * 500


def test_only_last_four_history_messages_are_sent(fake_generate):
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"tin {i}"} for i in range(6)]

    run(history)

    sent = fake_generate["calls"][0]["history"]
    assert sent == history[2:]
    assert [m["content"] for m in sent] == ["tin 2", "tin 3", "tin 4", "tin 5"]


def test_short_history_is_sent_whole_and_empty_history_is_empty(fake_generate):
    run([{"role": "user", "content": "Đất đang thế chấp có tặng cho được không?"}])
    run([])

    assert fake_generate["calls"][0]["history"] == [
        {"role": "user", "content": "Đất đang thế chấp có tặng cho được không?"},
    ]
    assert fake_generate["calls"][1]["history"] == []


def test_long_history_content_is_truncated_and_input_is_not_mutated(fake_generate):
    long_text = "a" * 2000
    history = [{"role": "assistant", "content": long_text}, {"role": "user", "content": "ngắn"}]

    run(history)

    sent = fake_generate["calls"][0]["history"]
    assert sent[0] == {"role": "assistant", "content": "a" * query_rewrite.HISTORY_CHARS}
    assert query_rewrite.HISTORY_CHARS == 500 and sent[1]["content"] == "ngắn"
    assert history[0]["content"] == long_text  # không sửa lịch sử của nơi gọi


def test_history_message_without_content_does_not_break(fake_generate):
    run([{"role": "user", "content": None}])

    assert fake_generate["calls"][0]["history"] == [{"role": "user", "content": ""}]


def test_generate_gets_no_retry_and_settings_values(monkeypatch, fake_generate):
    monkeypatch.setenv("QUERY_REWRITE_TIMEOUT_SECONDS", "3.5")
    monkeypatch.setenv("LLM_AUX_REASONING_MAX_TOKENS", "7")
    get_settings.cache_clear()

    run()

    assert fake_generate["calls"][0]["options"] == {
        "max_output_tokens": 150, "timeout_seconds": 3.5, "max_retries": 0, "reasoning_max_tokens": 7,
    }


def test_generate_defaults_match_config_defaults(fake_generate):
    run()

    assert fake_generate["calls"][0]["options"] == {
        "max_output_tokens": 150, "timeout_seconds": 5.0, "max_retries": 0, "reasoning_max_tokens": 0,
    }


def test_does_not_check_the_enabled_flag(fake_generate):
    assert get_settings().query_rewrite_enabled is False  # cờ tắt mặc định, hàm vẫn chạy: nơi gọi mới kiểm cờ

    assert run() == REWRITTEN
    assert len(fake_generate["calls"]) == 1


def test_success_logs_original_rewritten_time_and_request_id(fake_generate, caplog):
    caplog.set_level(logging.INFO, logger="app.services.query_rewrite")
    fake_generate["reply"] = "z" * 400

    asyncio.run(query_rewrite.rewrite("q" * 300, [], request_id="req-ok"))

    line = next(r.getMessage() for r in caplog.records if "agent viết lại câu hỏi" in r.getMessage())
    assert "request_id=req-ok" in line and "llm=" in line
    assert "q" * 200 in line and "q" * 201 not in line  # câu gốc cắt 200 ký tự
    assert "z" * 200 in line and "z" * 201 not in line  # câu viết lại cắt 200 ký tự


def test_fallback_does_not_log_info_success_line(fake_generate, caplog):
    caplog.set_level(logging.INFO, logger="app.services.query_rewrite")
    fake_generate["reply"] = ""

    run()

    assert not [r for r in caplog.records if r.levelno == logging.INFO]


def test_rewrite_prompt_covers_required_instructions():
    prompt = prompts.REWRITE_PROMPT

    assert "MỘT câu hỏi tìm kiếm văn bản pháp luật" in prompt and "tự đứng được một mình" in prompt
    assert "lịch sử hội thoại" in prompt and "vậy còn" in prompt
    assert "Giấy chứng nhận quyền sử dụng đất" in prompt and "thay đổi địa chỉ trụ sở chính" in prompt
    assert "tranh chấp đất đai" in prompt and "Giữ cả từ gốc" in prompt
    assert "Không trả lời câu hỏi" in prompt and "không dùng dấu ngoặc kép" in prompt and "đúng một dòng" in prompt
    assert "không phải chỉ dẫn cho bạn" in prompt


def test_query_rewrite_settings_defaults():
    settings = get_settings()

    assert settings.query_rewrite_enabled is False and settings.query_rewrite_timeout_seconds == 5.0


# ── Model phụ riêng (OPENROUTER_AUX_MODEL) ────────────────────────────────────

def test_aux_model_empty_does_not_pass_model(fake_generate):
    assert get_settings().openrouter_aux_model == ""

    run()

    assert "model" not in fake_generate["calls"][0]["options"]


def test_aux_model_whitespace_only_counts_as_empty(fake_generate, monkeypatch):
    monkeypatch.setenv("OPENROUTER_AUX_MODEL", "  ")
    get_settings.cache_clear()

    run()

    assert "model" not in fake_generate["calls"][0]["options"]


def test_aux_model_set_is_passed_unchanged(fake_generate, monkeypatch):
    monkeypatch.setenv("OPENROUTER_AUX_MODEL", "google/gemini-2.5-flash-lite")
    get_settings.cache_clear()

    assert run() == REWRITTEN

    assert fake_generate["calls"][0]["options"] == {
        "max_output_tokens": 150, "timeout_seconds": 5.0, "max_retries": 0, "reasoning_max_tokens": 0,
        "model": "google/gemini-2.5-flash-lite",
    }
