"""llm_client.generate_stream với provider giả lập (không gọi mạng): chuyển provider, lỗi giữa chừng, timeout, usage."""
import asyncio
from types import SimpleNamespace

import httpx
import openai
from google.genai import errors

from app.core.config import get_settings
from app.services import llm_client


class FakeStream:
    """Async iterator giả: lần lượt trả/ném từng phần tử đặt sẵn ("hang" = treo lâu hơn mọi timeout của test)."""

    def __init__(self, items):
        self.items = list(items)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.items:
            raise StopAsyncIteration
        item = self.items.pop(0)
        if isinstance(item, str) and item == "hang":
            await asyncio.sleep(10)
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self):
        self.closed = True


class FakeClient:
    """Dùng cho cả Gemini (aio.models.generate_content_stream) và OpenRouter (chat.completions.create).
    `outcomes`: mỗi lần mở stream là một list chunk, hoặc Exception (ném khi mở), hoặc "hang" (treo khi mở)."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.streams = []
        self.aio = self.models = self.chat = self.completions = self

    async def _open(self, kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, str) and outcome == "hang":
            await asyncio.sleep(10)
        if isinstance(outcome, Exception):
            raise outcome
        stream = FakeStream(outcome)
        self.streams.append(stream)
        return stream

    async def generate_content_stream(self, **kwargs):
        return await self._open(kwargs)

    async def create(self, **kwargs):
        return await self._open(kwargs)


def g(text, tokens=None):
    """Chunk của Gemini."""
    usage = SimpleNamespace(total_token_count=tokens) if tokens else None
    return SimpleNamespace(text=text, usage_metadata=usage)


def o(text=None, tokens=None):
    """Chunk của OpenRouter; text=None là mẩu cuối chỉ mang usage (không có choices)."""
    choices = [SimpleNamespace(delta=SimpleNamespace(content=text))] if text is not None else []
    return SimpleNamespace(choices=choices, usage=SimpleNamespace(total_tokens=tokens) if tokens else None)


def gemini_error(code):
    return errors.APIError(code, {"error": {"code": code, "message": f"lỗi giả {code}", "status": "X"}})


def openrouter_error(status):
    response = httpx.Response(status, request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"))
    return openai.APIStatusError("lỗi giả", response=response, body={"error": {"code": status, "message": "lỗi giả"}})


def connection_error():
    return openai.APIConnectionError(request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"))


def configure(monkeypatch, providers, gemini=None, openrouter=None, **env):
    """Đặt LLM_PROVIDER và thay client thật bằng FakeClient; trả về (fake_gemini, fake_openrouter)."""
    monkeypatch.setenv("LLM_PROVIDER", providers)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "or-model")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    fake_gemini, fake_openrouter = FakeClient(gemini or []), FakeClient(openrouter or [])
    monkeypatch.setattr(llm_client, "_get_gemini_client", lambda: fake_gemini)
    monkeypatch.setattr(llm_client, "_get_openrouter_client", lambda: fake_openrouter)
    return fake_gemini, fake_openrouter


async def _collect(info):
    texts = []
    try:
        async for text in llm_client.generate_stream("system prompt", [], "hỏi", info=info):
            texts.append(text)
    except llm_client.LLMError as e:
        return texts, e
    return texts, None


def collect(info=None):
    """(các mẩu chữ đã nhận, LLMError nếu stream kết thúc bằng lỗi)."""
    return asyncio.run(_collect(info))


# ── Chuyển provider ─────────────────────────────────────────────────────────────────────────────────────────


def test_phat_tung_mau_va_dien_info_model_tokens(monkeypatch):
    configure(monkeypatch, "gemini", gemini=[[g("Theo "), g("Điều "), g("188.", tokens=99)]])
    info = {}

    texts, error = collect(info)

    assert texts == ["Theo ", "Điều ", "188."] and error is None
    assert info == {"model": "gemini-test", "tokens": 99}


def test_loi_truoc_mau_dau_thi_chuyen_sang_provider_ke_tiep(monkeypatch):
    gemini, openrouter = configure(
        monkeypatch, "openrouter,gemini", openrouter=[openrouter_error(402)], gemini=[[g("Từ Gemini", tokens=7)]],
    )
    info = {}

    texts, error = collect(info)

    assert texts == ["Từ Gemini"] and error is None
    assert info == {"model": "gemini-test", "tokens": 7}
    assert len(openrouter.calls) == 1 and len(gemini.calls) == 1


def test_loi_503_khong_thu_lai_cung_provider(monkeypatch):
    gemini, openrouter = configure(
        monkeypatch, "openrouter,gemini", openrouter=[openrouter_error(503)], gemini=[[g("OK")]],
    )

    texts, _ = collect()

    assert texts == ["OK"]
    assert len(openrouter.calls) == 1  # không thử lại OpenRouter


def test_treo_khi_mo_stream_qua_timeout_thi_chuyen_provider(monkeypatch):
    gemini, openrouter = configure(
        monkeypatch, "openrouter,gemini", openrouter=["hang"], gemini=[[g("OK")]], OPENROUTER_TIMEOUT_SECONDS="0.05",
    )

    texts, error = collect()

    assert texts == ["OK"] and error is None


def test_khong_co_mau_chu_nao_thi_chuyen_provider(monkeypatch):
    gemini, openrouter = configure(
        monkeypatch, "openrouter,gemini", openrouter=[[o(None, tokens=5)]], gemini=[[g("OK")]],
    )

    texts, error = collect()

    assert texts == ["OK"] and error is None
    assert openrouter.streams[0].closed  # stream đã đóng


def test_mau_dau_qua_timeout_thi_chuyen_provider(monkeypatch):
    gemini, openrouter = configure(
        monkeypatch, "openrouter,gemini", openrouter=[["hang"]], gemini=[[g("OK")]], OPENROUTER_TIMEOUT_SECONDS="0.05",
    )

    texts, error = collect()

    assert texts == ["OK"] and error is None
    assert openrouter.streams[0].closed


def test_loi_sau_mau_dau_thi_khong_chuyen_provider_va_nem_llmerror(monkeypatch):
    gemini, openrouter = configure(
        monkeypatch, "openrouter,gemini", openrouter=[[o("Theo "), connection_error()]], gemini=[[g("KHÔNG ĐƯỢC DÙNG")]],
    )

    texts, error = collect()

    assert texts == ["Theo "]  # người dùng đã thấy chữ này
    assert isinstance(error, llm_client.LLMError) and "giữa chừng" in str(error)
    assert gemini.calls == []  # không chuyển sang Gemini
    assert openrouter.streams[0].closed


def test_treo_giua_hai_mau_qua_timeout_thi_nem_llmerror(monkeypatch):
    gemini, _ = configure(monkeypatch, "gemini", gemini=[[g("A"), "hang"]], GEMINI_TIMEOUT_SECONDS="0.05")

    texts, error = collect()

    assert texts == ["A"]
    assert isinstance(error, llm_client.LLMError) and "giữa chừng" in str(error)
    assert gemini.streams[0].closed


def test_tat_ca_provider_loi_thi_nem_llmerror(monkeypatch):
    configure(monkeypatch, "openrouter,gemini", openrouter=[openrouter_error(402)], gemini=[gemini_error(503)])

    texts, error = collect()

    assert texts == [] and isinstance(error, llm_client.LLMError) and "Tất cả provider thất bại" in str(error)


def test_provider_khong_hop_le_thi_bo_qua_roi_dung_provider_sau(monkeypatch):
    configure(monkeypatch, "khong-co,gemini", gemini=[[g("OK")]])

    texts, _ = collect()

    assert texts == ["OK"]


def test_thieu_cau_hinh_openrouter_thi_chuyen_provider(monkeypatch):
    gemini, openrouter = configure(monkeypatch, "openrouter,gemini", gemini=[[g("OK")]], OPENROUTER_API_KEY="")

    texts, _ = collect()

    assert texts == ["OK"] and openrouter.calls == []


# ── Từng provider ───────────────────────────────────────────────────────────────────────────────────────────


def test_groq_khong_stream_phat_ca_cau_mot_lan(monkeypatch):
    configure(monkeypatch, "groq", GROQ_MODEL="groq-model")
    seen = {}

    async def fake_groq(system_prompt, history, user_content, **options):
        seen.update(options)
        return "Cả câu trả lời", 33

    monkeypatch.setattr(llm_client, "_generate_groq", fake_groq)
    info = {}

    texts, error = collect(info)

    assert texts == ["Cả câu trả lời"] and error is None
    assert seen == {"max_retries": 0}  # không thử lại cùng provider
    assert info == {"model": "groq-model", "tokens": 33}


def test_openrouter_goi_stream_kem_usage_va_giu_tham_so_reasoning(monkeypatch):
    _, openrouter = configure(
        monkeypatch, "openrouter", openrouter=[[o("Xin "), o("chào"), o(None, tokens=55)]],
    )
    info = {}

    texts, error = collect(info)

    assert texts == ["Xin ", "chào"] and error is None
    call = openrouter.calls[0]
    assert call["stream"] is True and call["stream_options"] == {"include_usage": True}
    assert call["model"] == "or-model"
    assert call["extra_body"] == {"reasoning": {"max_tokens": 1024}} and call["max_tokens"] == 800 + 1024
    assert [m["role"] for m in call["messages"]] == ["system", "user"]
    assert info == {"model": "or-model", "tokens": 55}


def test_openrouter_tat_reasoning_thi_khong_gui_extra_body(monkeypatch):
    _, openrouter = configure(
        monkeypatch, "openrouter", openrouter=[[o("OK")]], OPENROUTER_REASONING_MAX_TOKENS="0",
    )

    collect()

    assert "extra_body" not in openrouter.calls[0] and openrouter.calls[0]["max_tokens"] == 800


def test_openrouter_loi_http_giua_chung_giu_cau_bao_loi_de_hieu(monkeypatch):
    configure(monkeypatch, "openrouter", openrouter=[[o("A"), openrouter_error(429)]])

    texts, error = collect()

    assert texts == ["A"] and "giới hạn lượt gọi" in str(error)


def test_gemini_bo_qua_mau_khong_co_chu_va_lay_usage_cuoi(monkeypatch):
    gemini, _ = configure(monkeypatch, "gemini", gemini=[[g(None), g("A"), g("", tokens=3), g("B", tokens=12)]])
    info = {}

    texts, _ = collect(info)

    assert texts == ["A", "B"] and info["tokens"] == 12
    call = gemini.calls[0]
    assert call["model"] == "gemini-test"
    assert call["config"].max_output_tokens == 800 and call["config"].thinking_config.thinking_budget == 0
    assert [c.role for c in call["contents"]] == ["user"]


def test_gemini_loi_khong_retry_duoc_la_llmerror(monkeypatch):
    configure(monkeypatch, "gemini", gemini=[gemini_error(503), [g("KHÔNG BAO GIỜ TỚI")]])

    texts, error = collect()

    assert texts == [] and isinstance(error, llm_client.LLMError)


def test_dung_som_thi_dong_stream_cua_provider(monkeypatch):
    gemini, _ = configure(monkeypatch, "gemini", gemini=[[g("A"), g("B"), g("C")]])

    async def consume_one():
        stream = llm_client.generate_stream("system prompt", [], "hỏi")
        first = await stream.__anext__()
        await stream.aclose()  # client ngắt kết nối giữa chừng
        return first

    assert asyncio.run(consume_one()) == "A"
    assert gemini.streams[0].closed


def test_info_khong_bat_buoc(monkeypatch):
    configure(monkeypatch, "gemini", gemini=[[g("OK")]])
    assert collect(None)[0] == ["OK"]
