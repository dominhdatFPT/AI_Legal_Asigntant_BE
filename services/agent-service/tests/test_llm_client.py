"""Test llm_client.generate với Gemini giả lập: thử lại khi lỗi tạm thời, báo lỗi ngay khi lỗi khác."""
import asyncio

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
        monkeypatch.setattr(llm_client, "_get_client", lambda: fake)
        return fake

    monkeypatch.setattr(llm_client, "_sleep", fake_sleep)
    install.sleeps = sleeps
    return install


def _run(history=None, user_content="Sang tên sổ đỏ cần gì?"):
    return asyncio.run(llm_client.generate("system prompt", history or [], user_content))


def test_thanh_cong_ngay_lan_dau(gemini):
    fake = gemini([_Resp("Theo Điều 45...")])
    assert _run() == ("Theo Điều 45...", 321)
    assert len(fake.calls) == 1 and gemini.sleeps == []
    assert fake.calls[0]["model"] == "gemini-test"


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
    fake = gemini([_api_error(503), _api_error(503), _api_error(429)])
    with pytest.raises(llm_client.LLMError, match="3 lần thử"):
        _run()
    assert len(fake.calls) == 3  # 1 lần đầu + GEMINI_MAX_RETRIES=2
    assert gemini.sleeps == [2, 4]


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
    assert _run() == ("OK", None)
