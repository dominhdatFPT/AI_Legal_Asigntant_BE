"""Test llm_client.generate với Gemini/OpenRouter giả lập: thử lại khi lỗi tạm thời, báo lỗi ngay khi lỗi khác."""
import asyncio

import httpx
import openai
import pytest
from google.genai import errors

from app.services import llm_client


class _Usage:
    total_token_count = 321


class _Resp:
    def __init__(self, text="Câu trả lời", usage=True):
        self.text = text
        self.usage_metadata = _Usage() if usage else None


def _api_error(code):
    return errors.APIError(code, {"error": {"code": code, "message": f"lỗi giả {code}", "status": "X"}})


class FakeGemini:
    """Giả lập client.aio.models. `outcomes`: lần lượt kết quả mỗi lần gọi (Exception, "hang" hoặc _Resp)."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.aio = self
        self.models = self

    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        outcome = self.outcomes.pop(0)
        if outcome == "hang":
            await asyncio.sleep(10)  # lâu hơn timeout của test
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def gemini(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    def install(outcomes):
        fake = FakeGemini(outcomes)
        monkeypatch.setattr(llm_client, "_get_gemini_client", lambda: fake)
        return fake

    monkeypatch.setattr(llm_client, "_sleep", fake_sleep)
    install.sleeps = sleeps
    return install


def _run(history=None, user_content="Sang tên sổ đỏ cần gì?"):
    return asyncio.run(llm_client.generate("system prompt", history or [], user_content))


def test_thanh_cong_ngay_lan_dau(gemini):
    fake = gemini([_Resp("Theo Điều 45...")])
    assert _run() == ("Theo Điều 45...", 321, "gemini-test")
    assert len(fake.calls) == 1 and gemini.sleeps == []
    assert fake.calls[0]["model"] == "gemini-test"


def test_gioi_han_do_dai_va_tat_suy_nghi(gemini):
    fake = gemini([_Resp()])
    _run()
    config = fake.calls[0]["config"]
    assert config.max_output_tokens == 800
    assert config.thinking_config.thinking_level == "MINIMAL"


def test_gemini_2_tat_suy_nghi_bang_budget():
    assert llm_client._gemini_thinking_config("gemini-2.5-flash").thinking_budget == 0


def test_groq_qwen3_tat_suy_luan():
    assert llm_client._groq_reasoning_args("qwen/qwen3-32b") == {"reasoning_effort": "none"}
    assert llm_client._groq_reasoning_args("llama-3.3-70b-versatile") == {}


def test_doi_role_assistant_thanh_model(gemini):
    fake = gemini([_Resp()])
    _run(history=[{"role": "user", "content": "hỏi"}, {"role": "assistant", "content": "đáp"}])
    roles = [c.role for c in fake.calls[0]["contents"]]
    assert roles == ["user", "model", "user"]


@pytest.mark.parametrize("code", [429, 500, 503, 504])
def test_loi_tam_thoi_thu_lai_roi_thanh_cong(gemini, code):
    fake = gemini([_api_error(code), _Resp("OK")])
    assert _run()[0] == "OK"
    assert len(fake.calls) == 2
    assert gemini.sleeps == [2]


def test_het_luot_thu_thi_bao_loi(gemini):
    fake = gemini([_api_error(503), _api_error(429)])
    with pytest.raises(llm_client.LLMError, match="2 lần thử"):
        _run()
    assert len(fake.calls) == 2  # 1 lần đầu + GEMINI_MAX_RETRIES=1
    assert gemini.sleeps == [2]


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_loi_khac_bao_ngay_khong_thu_lai(gemini, code):
    fake = gemini([_api_error(code), _Resp()])
    with pytest.raises(llm_client.LLMError):
        _run()
    assert len(fake.calls) == 1 and gemini.sleeps == []


def test_qua_thoi_gian_cho_thi_thu_lai(gemini, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", "0.05")
    get_settings.cache_clear()
    fake = gemini(["hang", _Resp("OK")])
    assert _run()[0] == "OK"
    assert len(fake.calls) == 2 and gemini.sleeps == [2]


def test_tat_thu_lai(gemini, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("GEMINI_MAX_RETRIES", "0")
    get_settings.cache_clear()
    fake = gemini([_api_error(503), _Resp()])
    with pytest.raises(llm_client.LLMError):
        _run()
    assert len(fake.calls) == 1 and gemini.sleeps == []


def test_tra_ve_rong_bao_loi(gemini):
    gemini([_Resp(text="")])
    with pytest.raises(llm_client.LLMError, match="rỗng"):
        _run()


def test_khong_co_usage_thi_tokens_none(gemini):
    gemini([_Resp("OK", usage=False)])
    assert _run() == ("OK", None, "gemini-test")


# ── OpenRouter ──────────────────────────────────────────────────────────────


def _or_resp(text="Trả lời từ OpenRouter", tokens=55, finish_reason="stop"):
    choice = type("C", (), {"message": type("M", (), {"content": text})(), "finish_reason": finish_reason})()
    usage = type("U", (), {"total_tokens": tokens})() if tokens else None
    return type("R", (), {"choices": [choice], "usage": usage})()


def _or_error(status, message="lỗi giả"):
    response = httpx.Response(status, request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"))
    return openai.APIStatusError(message, response=response, body={"error": {"code": status, "message": message}})


class FakeOpenRouter:
    """Giả lập client.chat.completions. `outcomes` như FakeGemini."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.chat = self
        self.completions = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if outcome == "hang":
            await asyncio.sleep(10)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def openrouter(monkeypatch, gemini):
    from app.core.config import get_settings

    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "recursive/flint-v1.0:free")
    get_settings.cache_clear()

    def install(outcomes):
        fake = FakeOpenRouter(outcomes)
        monkeypatch.setattr(llm_client, "_get_openrouter_client", lambda: fake)
        return fake

    install.sleeps = gemini.sleeps
    install.gemini = gemini
    return install


def test_openrouter_thanh_cong(openrouter):
    fake = openrouter([_or_resp("Theo Điều 45...")])
    assert _run(history=[{"role": "assistant", "content": "Chào bạn"}]) == ("Theo Điều 45...", 55, "recursive/flint-v1.0:free")
    call = fake.calls[0]
    assert call["model"] == "recursive/flint-v1.0:free"
    assert [m["role"] for m in call["messages"]] == ["system", "assistant", "user"]
    # Chừa token cho phần suy luận bắt buộc của model: 800 (câu trả lời) + 1024 (suy nghĩ)
    assert call["extra_body"] == {"reasoning": {"max_tokens": 1024}} and call["max_tokens"] == 800 + 1024


def test_openrouter_tat_tham_so_reasoning(openrouter, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("OPENROUTER_REASONING_MAX_TOKENS", "0")
    get_settings.cache_clear()
    fake = openrouter([_or_resp()])
    _run()
    assert "extra_body" not in fake.calls[0] and fake.calls[0]["max_tokens"] == 800


def test_openrouter_het_token_suy_luan_bao_ro(openrouter):
    openrouter([_or_resp(text="", finish_reason="length")])
    with pytest.raises(llm_client.LLMError, match="OPENROUTER_REASONING_MAX_TOKENS"):
        _run()


@pytest.mark.parametrize("status,match", [
    (401, "API key"), (402, "hết credit"), (404, "quyền riêng tư"),
])
def test_openrouter_loi_khong_thu_lai_va_de_hieu(openrouter, status, match):
    message = "No endpoints found matching your data policy" if status == 404 else "lỗi giả"
    fake = openrouter([_or_error(status, message)])
    with pytest.raises(llm_client.LLMError, match=match):
        _run()
    assert len(fake.calls) == 1 and openrouter.sleeps == []


def test_openrouter_429_thu_lai_roi_bao_loi_de_hieu(openrouter):
    fake = openrouter([_or_error(429), _or_error(429)])
    with pytest.raises(llm_client.LLMError, match="giới hạn lượt gọi"):
        _run()
    assert len(fake.calls) == 2 and openrouter.sleeps == [2]


def test_openrouter_qua_thoi_gian_thu_lai(openrouter, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("OPENROUTER_TIMEOUT_SECONDS", "0.05")
    get_settings.cache_clear()
    fake = openrouter(["hang", _or_resp("OK")])
    assert _run()[0] == "OK"
    assert len(fake.calls) == 2


def test_openrouter_tra_ve_rong_bao_loi(openrouter):
    openrouter([_or_resp(text=None)])
    with pytest.raises(llm_client.LLMError, match="rỗng"):
        _run()


def test_openrouter_thieu_cau_hinh_bao_ro_khong_goi_mang(openrouter, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    get_settings.cache_clear()
    fake = openrouter([_or_resp()])
    with pytest.raises(llm_client.LLMError, match="OPENROUTER_API_KEY"):
        _run()
    assert fake.calls == []


def test_openrouter_loi_thi_chuyen_sang_gemini(openrouter, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("LLM_PROVIDER", "openrouter,gemini")
    get_settings.cache_clear()
    openrouter([_or_error(402)])
    openrouter.gemini([_Resp("Gemini trả lời")])
    assert _run() == ("Gemini trả lời", 321, "gemini-test")


@pytest.mark.parametrize("site,app_name,expected", [
    ("", "", {}),
    ("https://ai-legal.example", "AI Legal", {"HTTP-Referer": "https://ai-legal.example", "X-Title": "AI Legal"}),
])
def test_openrouter_header_chi_gui_khi_co_gia_tri(monkeypatch, site, app_name, expected):
    from app.core.config import get_settings

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("OPENROUTER_SITE_URL", site)
    monkeypatch.setenv("OPENROUTER_APP_NAME", app_name)
    get_settings.cache_clear()
    llm_client._get_openrouter_client.cache_clear()
    try:
        client = llm_client._get_openrouter_client()
        headers = {k: v for k, v in client.default_headers.items() if k in {"HTTP-Referer", "X-Title"}}
        assert headers == expected
        assert str(client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"
    finally:
        llm_client._get_openrouter_client.cache_clear()
