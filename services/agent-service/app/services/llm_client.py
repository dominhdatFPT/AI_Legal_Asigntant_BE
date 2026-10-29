import asyncio
import contextlib
import inspect
import logging
from collections.abc import AsyncIterator
from functools import lru_cache

import httpx
from google import genai
from google.genai import errors as genai_errors, types
import groq as groq_sdk
import openai

from app.core.config import get_settings

logger = logging.getLogger(__name__)

GEMINI_RETRYABLE_STATUS = {429, 500, 503, 504}
GROQ_RETRYABLE_STATUS = {429, 500, 503, 504}
OPENROUTER_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_sleep = asyncio.sleep


class LLMError(Exception):
    pass


@lru_cache
def _get_gemini_client() -> genai.Client:
    return genai.Client(api_key=get_settings().gemini_api_key)


@lru_cache
def _get_groq_client() -> groq_sdk.AsyncGroq:
    return groq_sdk.AsyncGroq(api_key=get_settings().groq_api_key)


@lru_cache
def _get_openrouter_client() -> openai.AsyncOpenAI:
    s = get_settings()
    headers = {k: v for k, v in {"HTTP-Referer": s.openrouter_site_url, "X-Title": s.openrouter_app_name}.items() if v}
    # Thử lại do _generate_openrouter tự làm (giống Gemini/Groq), tắt retry riêng của SDK
    return openai.AsyncOpenAI(api_key=s.openrouter_api_key, base_url=s.openrouter_base_url,
                              default_headers=headers or None, max_retries=0)


def _openrouter_error_message(e: openai.APIStatusError) -> str:
    """Đổi lỗi HTTP của OpenRouter thành câu dễ hiểu (ghi log, không lộ khóa)."""
    if e.status_code == 401:
        return "OpenRouter từ chối API key (401): kiểm tra OPENROUTER_API_KEY."
    if e.status_code == 402:
        return "Tài khoản OpenRouter đã hết credit (402)."
    if e.status_code == 429:
        return "Model OpenRouter đang vượt giới hạn lượt gọi (429), model free bị giới hạn theo phút/ngày."
    if "data policy" in str(e).lower():
        # Model :free chỉ chạy khi cho phép chính sách dữ liệu tương ứng trong tài khoản
        return ("Model free của OpenRouter bị chặn bởi cài đặt quyền riêng tư: "
                "bật chính sách dữ liệu cho model free tại https://openrouter.ai/settings/privacy.")
    return f"OpenRouter lỗi {e.status_code}: {e.message}"


def _gemini_thinking_config(model: str) -> types.ThinkingConfig | None:
    """Tắt (hoặc giảm tối đa) phần "suy nghĩ" của Gemini: trả lời từ trích đoạn có sẵn không cần suy luận dài."""
    try:
        return types.ThinkingConfig(thinking_budget=0)
    except Exception:
        return None


def _groq_reasoning_args(model: str) -> dict:
    """Qwen3 trên Groq mặc định sinh đoạn <think> trước câu trả lời; tắt đi. Model khác không nhận tham số này."""
    if "qwen3" in model.lower():
        return {"reasoning_effort": "none"}
    return {}


async def _generate_gemini(
    system_prompt: str,
    history: list[dict],
    user_content: str,
    max_output_tokens: int | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
) -> tuple[str, int | None]:
    settings = get_settings()
    contents = [
        types.Content(
            role="model" if m["role"] == "assistant" else "user",
            parts=[types.Part(text=m["content"])],
        )
        for m in history
    ]
    contents.append(types.Content(role="user", parts=[types.Part(text=user_content)]))
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=0.2,
        max_output_tokens=max_output_tokens or settings.llm_max_output_tokens,
        thinking_config=_gemini_thinking_config(settings.gemini_model),
    )

    max_retries = max(0, settings.gemini_max_retries if max_retries is None else max_retries)
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = await asyncio.wait_for(
                _get_gemini_client().aio.models.generate_content(
                    model=settings.gemini_model, contents=contents, config=config
                ),
                timeout=timeout_seconds or settings.gemini_timeout_seconds,
            )
            break
        except genai_errors.APIError as e:
            if e.code not in GEMINI_RETRYABLE_STATUS:
                raise LLMError(str(e)) from e
            last_error = e
        except (asyncio.TimeoutError, TimeoutError, httpx.TransportError) as e:
            last_error = e

        if attempt < max_retries:
            delay = 2 ** (attempt + 1)
            logger.warning(
                "Gemini lỗi tạm thời (%s), thử lại lần %d/%d sau %ds",
                getattr(last_error, "code", type(last_error).__name__), attempt + 1, max_retries, delay,
            )
            await _sleep(delay)
    else:
        raise LLMError(f"Gemini không phản hồi sau {max_retries + 1} lần thử: {last_error}") from last_error

    if not resp.text:
        raise LLMError("Gemini trả về rỗng")

    tokens = resp.usage_metadata.total_token_count if resp.usage_metadata else None
    return resp.text, tokens


async def _generate_groq(
    system_prompt: str,
    history: list[dict],
    user_content: str,
    max_output_tokens: int | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
) -> tuple[str, int | None]:
    settings = get_settings()
    messages = [{"role": "system", "content": system_prompt}]
    for m in history:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": user_content})

    max_retries = max(0, settings.groq_max_retries if max_retries is None else max_retries)
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = await asyncio.wait_for(
                _get_groq_client().chat.completions.create(
                    model=settings.groq_model,
                    messages=messages,
                    temperature=0.2,
                    max_tokens=max_output_tokens or settings.llm_max_output_tokens,
                    **_groq_reasoning_args(settings.groq_model),
                ),
                timeout=timeout_seconds or settings.groq_timeout_seconds,
            )
            break
        except groq_sdk.APIStatusError as e:
            if e.status_code not in GROQ_RETRYABLE_STATUS:
                raise LLMError(str(e)) from e
            last_error = e
        except (groq_sdk.APIConnectionError, asyncio.TimeoutError, TimeoutError) as e:
            last_error = e

        if attempt < max_retries:
            delay = 2 ** (attempt + 1)
            logger.warning(
                "Groq lỗi tạm thời (%s), thử lại lần %d/%d sau %ds",
                getattr(last_error, "status_code", type(last_error).__name__), attempt + 1, max_retries, delay,
            )
            await _sleep(delay)
    else:
        raise LLMError(f"Groq không phản hồi sau {max_retries + 1} lần thử: {last_error}") from last_error

    answer = resp.choices[0].message.content
    if not answer:
        raise LLMError("Groq trả về rỗng")

    tokens = resp.usage.total_tokens if resp.usage else None
    return answer, tokens


async def _generate_openrouter(
    system_prompt: str,
    history: list[dict],
    user_content: str,
    max_output_tokens: int | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
) -> tuple[str, int | None]:
    settings = get_settings()
    if not settings.openrouter_api_key or not settings.openrouter_model:
        raise LLMError("Chưa cấu hình OPENROUTER_API_KEY hoặc OPENROUTER_MODEL")
    messages = [{"role": "system", "content": system_prompt}]
    for m in history:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": user_content})
    reasoning = max(0, settings.openrouter_reasoning_max_tokens)
    reasoning_args = {"extra_body": {"reasoning": {"max_tokens": reasoning}}} if reasoning else {}

    max_retries = max(0, settings.openrouter_max_retries if max_retries is None else max_retries)
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = await asyncio.wait_for(
                _get_openrouter_client().chat.completions.create(
                    model=settings.openrouter_model,
                    messages=messages,
                    temperature=0.2,
                    max_tokens=(max_output_tokens or settings.llm_max_output_tokens) + reasoning,
                    **reasoning_args,
                ),
                timeout=timeout_seconds or settings.openrouter_timeout_seconds,
            )
            break
        except openai.APIStatusError as e:
            if e.status_code not in OPENROUTER_RETRYABLE_STATUS:
                raise LLMError(_openrouter_error_message(e)) from e
            last_error = e
        except (openai.APIConnectionError, asyncio.TimeoutError, TimeoutError) as e:
            last_error = e

        if attempt < max_retries:
            delay = 2 ** (attempt + 1)
            logger.warning(
                "OpenRouter lỗi tạm thời (%s), thử lại lần %d/%d sau %ds",
                getattr(last_error, "status_code", type(last_error).__name__), attempt + 1, max_retries, delay,
            )
            await _sleep(delay)
    else:
        reason = (_openrouter_error_message(last_error) if isinstance(last_error, openai.APIStatusError)
                  else f"quá thời gian chờ hoặc mất kết nối ({type(last_error).__name__})")
        raise LLMError(f"OpenRouter không phản hồi sau {max_retries + 1} lần thử: {reason}") from last_error

    answer = resp.choices[0].message.content if resp.choices else None
    if not answer:
        if resp.choices and resp.choices[0].finish_reason == "length":
            raise LLMError("OpenRouter trả về rỗng: model dùng hết token cho phần suy luận, "
                           "hãy tăng OPENROUTER_REASONING_MAX_TOKENS")
        raise LLMError("OpenRouter trả về rỗng")

    tokens = resp.usage.total_tokens if resp.usage else None
    return answer, tokens


async def generate(
    system_prompt: str,
    history: list[dict],
    user_content: str,
    max_output_tokens: int | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
) -> tuple[str, int | None, str]:
    """
    Trả về (answer, tokens, model_name).
    llm_provider có thể là một provider ("groq") hoặc danh sách ưu tiên ("openrouter,gemini").
    Nếu provider đầu thất bại, tự động thử provider tiếp theo.
    Ba tham số tùy chọn ghi đè cấu hình mặc định (chỉ chế độ rà soát dùng); không truyền thì giữ hành vi cũ.
    """
    options = {"max_output_tokens": max_output_tokens, "timeout_seconds": timeout_seconds, "max_retries": max_retries}
    s = get_settings()
    providers = [p.strip() for p in s.llm_provider.split(",") if p.strip()]

    last_error: LLMError | None = None
    for provider in providers:
        try:
            if provider == "groq":
                answer, tokens = await _generate_groq(system_prompt, history, user_content, **options)
                return answer, tokens, s.groq_model
            elif provider == "gemini":
                answer, tokens = await _generate_gemini(system_prompt, history, user_content, **options)
                return answer, tokens, s.gemini_model
            elif provider == "openrouter":
                answer, tokens = await _generate_openrouter(system_prompt, history, user_content, **options)
                return answer, tokens, s.openrouter_model
            else:
                raise LLMError(f"Provider không hợp lệ: {provider}")
        except LLMError as e:
            logger.warning("Provider '%s' thất bại: %s — thử provider tiếp theo.", provider, e)
            last_error = e

    raise LLMError(f"Tất cả provider thất bại. Lỗi cuối: {last_error}") from last_error


# ── Stream: phát từng mẩu chữ ngay khi provider sinh ra, thay vì chờ đủ câu ──────────────────────────────────────


async def _timed_stream(open_stream, timeout: float) -> AsyncIterator:
    """Mở stream (coroutine `open_stream`) rồi lấy từng mẩu; mỗi bước chờ tối đa `timeout` giây. Mẩu đầu tiên
    chia chung ngân sách với bước mở stream. Đóng stream khi dừng sớm (lỗi, client ngắt kết nối)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    stream = await asyncio.wait_for(open_stream, timeout)
    try:
        iterator = stream.__aiter__()
        wait = max(deadline - loop.time(), 0.001)
        while True:
            try:
                item = await asyncio.wait_for(iterator.__anext__(), wait)
            except StopAsyncIteration:
                return
            wait = timeout
            yield item
    finally:
        close = getattr(stream, "close", None)
        if close is not None:
            with contextlib.suppress(Exception):
                result = close()
                if inspect.isawaitable(result):
                    await result


def _chat_messages(system_prompt: str, history: list[dict], user_content: str) -> list[dict]:
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend({"role": m["role"], "content": m["content"]} for m in history)
    messages.append({"role": "user", "content": user_content})
    return messages


async def _stream_groq(system_prompt: str, history: list[dict], user_content: str, usage: dict) -> AsyncIterator[str]:
    """Groq không stream: lấy cả câu rồi phát một lần. Không thử lại cùng provider."""
    answer, tokens = await _generate_groq(system_prompt, history, user_content, max_retries=0)
    usage["tokens"] = tokens
    yield answer


async def _stream_gemini(system_prompt: str, history: list[dict], user_content: str, usage: dict) -> AsyncIterator[str]:
    settings = get_settings()
    contents = [
        types.Content(role="model" if m["role"] == "assistant" else "user", parts=[types.Part(text=m["content"])])
        for m in history
    ]
    contents.append(types.Content(role="user", parts=[types.Part(text=user_content)]))
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=0.2,
        max_output_tokens=settings.llm_max_output_tokens,
        thinking_config=_gemini_thinking_config(settings.gemini_model),
    )
    emitted = False
    try:
        opened = _get_gemini_client().aio.models.generate_content_stream(
            model=settings.gemini_model, contents=contents, config=config
        )
        async with contextlib.aclosing(_timed_stream(opened, settings.gemini_timeout_seconds)) as chunks:
            async for chunk in chunks:
                if chunk.usage_metadata and chunk.usage_metadata.total_token_count:
                    usage["tokens"] = chunk.usage_metadata.total_token_count
                if chunk.text:
                    emitted = True
                    yield chunk.text
    except genai_errors.APIError as e:
        raise LLMError(str(e)) from e
    except (asyncio.TimeoutError, TimeoutError, httpx.TransportError) as e:
        raise LLMError(f"Gemini quá thời gian chờ hoặc mất kết nối ({type(e).__name__})") from e
    if not emitted:
        raise LLMError("Gemini trả về rỗng")


async def _stream_openrouter(
    system_prompt: str, history: list[dict], user_content: str, usage: dict,
) -> AsyncIterator[str]:
    settings = get_settings()
    if not settings.openrouter_api_key or not settings.openrouter_model:
        raise LLMError("Chưa cấu hình OPENROUTER_API_KEY hoặc OPENROUTER_MODEL")
    reasoning = max(0, settings.openrouter_reasoning_max_tokens)
    reasoning_args = {"extra_body": {"reasoning": {"max_tokens": reasoning}}} if reasoning else {}
    emitted = False
    try:
        opened = _get_openrouter_client().chat.completions.create(
            model=settings.openrouter_model,
            messages=_chat_messages(system_prompt, history, user_content),
            temperature=0.2,
            max_tokens=settings.llm_max_output_tokens + reasoning,
            stream=True,
            stream_options={"include_usage": True},  # mẩu cuối mang usage (không có choices)
            **reasoning_args,
        )
        async with contextlib.aclosing(_timed_stream(opened, settings.openrouter_timeout_seconds)) as chunks:
            async for chunk in chunks:
                if getattr(chunk, "usage", None):
                    usage["tokens"] = chunk.usage.total_tokens
                text = chunk.choices[0].delta.content if chunk.choices else None
                if text:
                    emitted = True
                    yield text
    except openai.APIStatusError as e:
        raise LLMError(_openrouter_error_message(e)) from e
    except (openai.APIConnectionError, asyncio.TimeoutError, TimeoutError) as e:
        raise LLMError(f"OpenRouter quá thời gian chờ hoặc mất kết nối ({type(e).__name__})") from e
    if not emitted:
        raise LLMError("OpenRouter trả về rỗng")


async def generate_stream(
    system_prompt: str,
    history: list[dict],
    user_content: str,
    info: dict | None = None,
) -> AsyncIterator[str]:
    """Phát từng mẩu chữ của câu trả lời. Khi stream kết thúc, `info` (nếu truyền) được điền {"model", "tokens"}.
    Đi theo thứ tự `llm_provider` như `generate`, nhưng chỉ chuyển sang provider kế tiếp khi provider đó CHƯA phát
    mẩu chữ nào; đã phát rồi mà lỗi thì ném LLMError (người dùng đã thấy chữ nên không thể bắt đầu lại).
    Mỗi mẩu chờ tối đa timeout của provider, không thử lại cùng provider."""
    s = get_settings()
    providers = [p.strip() for p in s.llm_provider.split(",") if p.strip()]
    streams = {
        "groq": (_stream_groq, s.groq_model),
        "gemini": (_stream_gemini, s.gemini_model),
        "openrouter": (_stream_openrouter, s.openrouter_model),
    }

    last_error: LLMError | None = None
    for provider in providers:
        emitted = False
        usage: dict = {}
        try:
            if provider not in streams:
                raise LLMError(f"Provider không hợp lệ: {provider}")
            stream_fn, model_name = streams[provider]
            async with contextlib.aclosing(stream_fn(system_prompt, history, user_content, usage)) as texts:
                async for text in texts:
                    emitted = True
                    yield text
        except LLMError as e:
            if emitted:
                raise LLMError(f"{provider} lỗi giữa chừng khi stream: {e}") from e
            logger.warning("Provider '%s' thất bại: %s — thử provider tiếp theo.", provider, e)
            last_error = e
            continue
        if info is not None:
            info.update(model=model_name, tokens=usage.get("tokens"))
        return

    raise LLMError(f"Tất cả provider thất bại. Lỗi cuối: {last_error}") from last_error
