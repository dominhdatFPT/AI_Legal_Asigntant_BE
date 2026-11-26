"""Provider finish reasons/options and the SSE bridge use the same metadata."""
import asyncio
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.services import llm_client
from tests.test_llm_client import FakeGemini, FakeOpenRouter, _Resp, _or_resp
from tests.test_llm_stream import configure, g, o


@pytest.mark.parametrize("provider", ["openrouter", "gemini", "groq"])
def test_generate_finish_reason_length(monkeypatch, provider):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake")
    get_settings.cache_clear()
    if provider == "gemini":
        response = _Resp("partial answer")
        response.candidates = [SimpleNamespace(finish_reason="MAX_TOKENS")]
        fake = FakeGemini([response])
        monkeypatch.setattr(llm_client, "_get_gemini_client", lambda: fake)
    else:
        fake = FakeOpenRouter([_or_resp("partial answer", finish_reason="length")])
        monkeypatch.setattr(llm_client, f"_get_{provider}_client", lambda: fake)
    result = asyncio.run(llm_client.generate("system", [], "q", max_output_tokens=6000))
    assert len(result) == 3 and result[0] == "partial answer"
    assert result.truncated and result.finish_reason == "length"


@pytest.mark.parametrize("provider", ["openrouter", "gemini", "groq"])
def test_stream_options_and_finish_reason(monkeypatch, provider):
    if provider == "groq":
        monkeypatch.setenv("LLM_PROVIDER", provider)
        get_settings.cache_clear()
        fake = FakeOpenRouter([_or_resp("partial", tokens=9, finish_reason="length")])
        monkeypatch.setattr(llm_client, "_get_groq_client", lambda: fake)
    elif provider == "gemini":
        final = g("partial", tokens=9)
        final.candidates = [SimpleNamespace(finish_reason="MAX_TOKENS")]
        fake, _ = configure(monkeypatch, provider, gemini=[[final]])
    else:
        final = o("")
        final.choices[0].finish_reason = "length"
        _, fake = configure(monkeypatch, provider, openrouter=[[o("partial"), final, o(tokens=9)]])
    info = {}

    async def collect():
        return "".join([text async for text in llm_client.generate_stream(
            "system", [], "q", info=info, max_output_tokens=6000, timeout_seconds=2,
            reasoning_max_tokens=0, model="custom-model",
        )])

    assert asyncio.run(collect()) == "partial"
    assert info["truncated"] and info["finish_reason"] == "length" and info["tokens"] == 9
    call = fake.calls[0]
    assert (call["config"].max_output_tokens if provider == "gemini" else call["max_tokens"]) == 6000
    if provider == "openrouter":
        assert call["model"] == info["model"] == "custom-model"
