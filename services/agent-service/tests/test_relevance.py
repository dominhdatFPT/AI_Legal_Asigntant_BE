"""relevance.grade với LLM giả lập (không gọi mạng): đọc JSON, trả None khi không chấm được."""
import asyncio
import logging

import pytest

from app.core.config import get_settings
from app.services import llm_client, prompts, relevance

QUESTION = "Ly hôn chia tài sản thế nào?"
CHUNKS = [
    {
        "id": f"id-{i}",
        "content": f"Điều {i}. Nội dung số {i}",
        "section_info": {"dieu": str(i), "khoan": "1"},
        "document": {"title": "Luật Đất đai 2024", "doc_number": "31/2024/QH15"},
    }
    for i in (1, 2, 3)
]


@pytest.fixture
def fake_llm(monkeypatch):
    """Trả lần lượt các câu trả lời đặt sẵn (hoặc ném nếu là Exception); ghi lại mọi lần gọi."""
    state = {"replies": ['{"relevant": [1]}'], "calls": []}

    async def fake_generate(system_prompt, history, user_content, **options):
        state["calls"].append({"system_prompt": system_prompt, "history": history, "user_content": user_content, **options})
        reply = state["replies"].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, 77, "grader-model"

    monkeypatch.setattr(llm_client, "generate", fake_generate)
    return state


def grade(chunks=CHUNKS, **kwargs):
    return asyncio.run(relevance.grade(QUESTION, chunks, **kwargs))


def reply_with(fake_llm, text):
    fake_llm["replies"] = [text]


def test_json_dung_tra_ve_cac_doan_duoc_chon(fake_llm):
    reply_with(fake_llm, '{"relevant": [1, 3]}')
    info = {}

    assert grade(info=info) == [CHUNKS[0], CHUNKS[2]]
    assert info == {"model": "grader-model", "tokens": 77}


def test_goi_llm_voi_gioi_han_nho_khong_thu_lai_va_khong_lich_su(fake_llm):
    grade()

    call = fake_llm["calls"][0]
    assert call["max_output_tokens"] == 100
    assert call["timeout_seconds"] == 8.0
    assert call["max_retries"] == 0
    assert call["history"] == [] and call["user_content"] == QUESTION


def test_timeout_lay_tu_cau_hinh(fake_llm, monkeypatch):
    monkeypatch.setenv("RELEVANCE_TIMEOUT_SECONDS", "3")
    get_settings.cache_clear()

    grade()

    assert fake_llm["calls"][0]["timeout_seconds"] == 3.0


def test_muc_suy_luan_mac_dinh_la_0_lay_tu_cau_hinh(fake_llm):
    grade()

    assert fake_llm["calls"][0]["reasoning_max_tokens"] == 0


def test_muc_suy_luan_doi_theo_cau_hinh(fake_llm, monkeypatch):
    monkeypatch.setenv("LLM_AUX_REASONING_MAX_TOKENS", "512")
    get_settings.cache_clear()

    grade()

    assert fake_llm["calls"][0]["reasoning_max_tokens"] == 512


def test_qua_llm_client_that_openrouter_nhan_enabled_false(monkeypatch):
    """Không giả llm_client.generate: chỉ giả client OpenRouter, soi tham số thật gửi đi."""
    sent = []
    reply = type("R", (), {
        "choices": [type("C", (), {"message": type("M", (), {"content": '{"relevant": [2]}'})(), "finish_reason": "stop"})()],
        "usage": type("U", (), {"total_tokens": 9})(),
    })()

    class FakeCompletions:
        async def create(self, **kwargs):
            sent.append(kwargs)
            return reply

    fake_client = type("Client", (), {"chat": type("Chat", (), {"completions": FakeCompletions()})()})()
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "model-test")
    get_settings.cache_clear()
    monkeypatch.setattr(llm_client, "_get_openrouter_client", lambda: fake_client)

    assert grade() == [CHUNKS[1]]
    assert sent[0]["extra_body"] == {"reasoning": {"enabled": False}} and sent[0]["max_tokens"] == 100


def test_prompt_danh_so_doan_va_cat_con_600_ky_tu(fake_llm):
    long_chunk = {**CHUNKS[0], "content": "x" * 2000}

    grade([long_chunk, CHUNKS[1], CHUNKS[2]])

    prompt = fake_llm["calls"][0]["system_prompt"]
    assert "[1] Khoản 1 Điều 1, Luật Đất đai 2024 (31/2024/QH15)\n" + "x" * 600 in prompt
    assert "x" * 601 not in prompt
    assert "[2] Khoản 1 Điều 2" in prompt and "[3] Khoản 1 Điều 3" in prompt
    assert '{"relevant"' in prompt  # dặn dạng JSON; dấu { } trong prompt không làm hỏng việc dựng prompt


def test_chu_luat_co_dau_ngoac_nhon_khong_lam_hong_prompt(fake_llm):
    grade([{**CHUNKS[0], "content": "Giá {x} và {context}"}])

    assert "Giá {x} và {context}" in fake_llm["calls"][0]["system_prompt"]


@pytest.mark.parametrize("text", [
    '```json\n{"relevant": [2]}\n```',
    '```\n{"relevant": [2]}\n```',
    'Các đoạn dùng được: {"relevant": [2]} (hết)',
    '  {"relevant":[2]}  ',
])
def test_json_boc_trong_khoi_code_hoac_co_chu_xung_quanh(fake_llm, text):
    reply_with(fake_llm, text)
    assert grade() == [CHUNKS[1]]


def test_danh_sach_rong_la_khong_doan_nao_dung_duoc(fake_llm):
    reply_with(fake_llm, '{"relevant": []}')

    result = grade()

    assert result == [] and result is not None


def test_so_trung_va_sai_thu_tu_duoc_chuan_hoa(fake_llm):
    reply_with(fake_llm, '{"relevant": [3, 1, 1]}')
    assert grade() == [CHUNKS[0], CHUNKS[2]]


@pytest.mark.parametrize("text", [
    "không phải json",
    "",
    '{"relevant": [1,',
    '{"khac": [1]}',
    '{"relevant": "1"}',
    '{"relevant": null}',
    '{"relevant": [1, "2"]}',
    '{"relevant": [true]}',
    '{"relevant": [1.5]}',
    '{"relevant": [null]}',
    "[1, 2]",
])
def test_json_hong_thi_tra_none_va_ghi_warning(fake_llm, caplog, text):
    reply_with(fake_llm, text)
    caplog.set_level(logging.WARNING, logger="app.services.relevance")

    assert grade(request_id="req-1") is None
    assert any(r.levelno == logging.WARNING and "req-1" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("text", [
    '{"relevant": [0]}',
    '{"relevant": [4]}',
    '{"relevant": [-1]}',
    '{"relevant": [1, 4]}',
])
def test_so_thu_tu_ngoai_pham_vi_thi_tra_none(fake_llm, caplog, text):
    reply_with(fake_llm, text)
    caplog.set_level(logging.WARNING, logger="app.services.relevance")

    assert grade(request_id="req-2") is None
    assert any(r.levelno == logging.WARNING and "req-2" in r.getMessage() for r in caplog.records)


def test_llm_loi_thi_tra_none_va_ghi_warning(fake_llm, caplog):
    fake_llm["replies"] = [llm_client.LLMError("Tất cả provider thất bại: timeout")]
    caplog.set_level(logging.WARNING, logger="app.services.relevance")
    info = {}

    assert grade(request_id="req-3", info=info) is None
    assert info == {}  # không có lượt chấm nào thành công
    assert any(r.levelno == logging.WARNING and "req-3" in r.getMessage() for r in caplog.records)


def test_loi_lap_trinh_khong_bi_nuot(fake_llm):
    fake_llm["replies"] = [RuntimeError("bug")]
    with pytest.raises(RuntimeError):
        grade()


def test_khong_co_doan_nao_thi_khoi_goi_llm(fake_llm):
    assert grade([]) == []
    assert fake_llm["calls"] == []


def test_cau_tra_loi_co_dinh_neu_ro_pham_vi_kho():
    text = prompts.OUT_OF_SCOPE_ANSWER
    assert "chưa tìm thấy quy định liên quan" in text and "luật sư" in text


# ── Số ký tự mỗi đoạn luật trong prompt chấm (RELEVANCE_CHUNK_CHARS) ─────────────

def _chars_chunk(number, length, document_title="Luật Đất đai 2024"):
    return {
        "id": f"id-{number}", "content": "  " + "x" * length + "  ", "score": 0.5,
        "section_info": {"dieu": str(number), "khoan": "1"},
        "document": {"title": document_title, "doc_number": "31/2024/QH15"},
    }


def _previous_relevance_prompt(chunks):
    """build_relevance_prompt trước khi đọc cấu hình: cắt cứng 600 ký tự."""
    short = [{**c, "content": (c.get("content") or "").strip()[:600]} for c in chunks]
    context = "\n\n".join(prompts.format_chunk(i, c) for i, c in enumerate(short, start=1))
    return prompts.RELEVANCE_PROMPT.replace("{context}", context)


def _set_chunk_chars(monkeypatch, value):
    monkeypatch.setenv("RELEVANCE_CHUNK_CHARS", str(value))
    get_settings.cache_clear()


def test_relevance_chunk_chars_default_is_600_and_prompt_is_identical_to_before():
    chunks = [_chars_chunk(1, 2000), _chars_chunk(2, 100), _chars_chunk(3, 600), _chars_chunk(4, 601)]

    assert get_settings().relevance_chunk_chars == 600 == prompts.RELEVANCE_CHUNK_CHARS
    prompt = prompts.build_relevance_prompt(chunks)
    assert prompt == _previous_relevance_prompt(chunks)
    assert "x" * 600 in prompt and "x" * 601 not in prompt


def test_relevance_chunk_chars_1200_cuts_long_chunk_to_1200_and_keeps_short_ones(monkeypatch):
    _set_chunk_chars(monkeypatch, 1200)
    chunks = [_chars_chunk(1, 2000), _chars_chunk(2, 800)]

    prompt = prompts.build_relevance_prompt(chunks)

    first, second = prompt.split("\n\n[2] ")
    assert first.endswith("x" * 1200) and "x" * 1201 not in prompt
    assert second.endswith("x" * 800) and "x" * 801 not in second  # đoạn ngắn hơn 1200 giữ nguyên
    assert prompt != _previous_relevance_prompt(chunks)


@pytest.mark.parametrize("value", [0, -1, -600])
def test_relevance_chunk_chars_zero_or_negative_falls_back_to_600(monkeypatch, value):
    _set_chunk_chars(monkeypatch, value)
    chunks = [_chars_chunk(1, 2000), _chars_chunk(2, 100)]

    assert prompts.build_relevance_prompt(chunks) == _previous_relevance_prompt(chunks)


def test_relevance_chunk_chars_small_value_cuts_shorter(monkeypatch):
    _set_chunk_chars(monkeypatch, 50)

    prompt = prompts.build_relevance_prompt([_chars_chunk(1, 2000)])

    assert "x" * 50 in prompt and "x" * 51 not in prompt


def test_grade_sends_the_configured_length_to_the_llm(fake_llm, monkeypatch):
    _set_chunk_chars(monkeypatch, 1000)

    assert grade([_chars_chunk(1, 2500)]) is not None

    prompt = fake_llm["calls"][0]["system_prompt"]
    assert "x" * 1000 in prompt and "x" * 1001 not in prompt


# ── Model phụ riêng (OPENROUTER_AUX_MODEL) ────────────────────────────────────

def test_aux_model_rong_thi_khong_truyen_model(fake_llm):
    assert get_settings().openrouter_aux_model == ""

    grade()

    assert "model" not in fake_llm["calls"][0]


def test_aux_model_chi_toan_khoang_trang_coi_nhu_rong(fake_llm, monkeypatch):
    monkeypatch.setenv("OPENROUTER_AUX_MODEL", "   ")
    get_settings.cache_clear()

    grade()

    assert "model" not in fake_llm["calls"][0]


def test_aux_model_co_gia_tri_thi_truyen_dung_gia_tri_do(fake_llm, monkeypatch):
    monkeypatch.setenv("OPENROUTER_AUX_MODEL", "google/gemini-2.5-flash-lite")
    get_settings.cache_clear()

    grade()

    call = fake_llm["calls"][0]
    assert call["model"] == "google/gemini-2.5-flash-lite"
    # các tham số khác của lượt chấm không đổi
    assert call["max_output_tokens"] == 100 and call["max_retries"] == 0 and call["reasoning_max_tokens"] == 0


def test_aux_model_qua_llm_client_that_openrouter_nhan_model_phu(monkeypatch):
    sent = []
    reply = type("R", (), {
        "choices": [type("C", (), {"message": type("M", (), {"content": '{"relevant": [2]}'})(), "finish_reason": "stop"})()],
        "usage": type("U", (), {"total_tokens": 9})(),
    })()

    class FakeCompletions:
        async def create(self, **kwargs):
            sent.append(kwargs)
            return reply

    fake_client = type("Client", (), {"chat": type("Chat", (), {"completions": FakeCompletions()})()})()
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "model-chinh")
    monkeypatch.setenv("OPENROUTER_AUX_MODEL", "model-phu")
    get_settings.cache_clear()
    monkeypatch.setattr(llm_client, "_get_openrouter_client", lambda: fake_client)
    info = {}

    assert grade(info=info) == [CHUNKS[1]]
    assert sent[0]["model"] == "model-phu" and info["model"] == "model-phu"
