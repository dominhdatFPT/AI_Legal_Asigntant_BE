import asyncio
import logging
from functools import lru_cache

import httpx
from google import genai
from google.genai import errors as genai_errors, types
import groq as groq_sdk

from app.core.config import get_settings

logger = logging.getLogger(__name__)

GEMINI_RETRYABLE_STATUS = {429, 500, 503, 504}
GROQ_RETRYABLE_STATUS = {429, 500, 503, 504}

_sleep = asyncio.sleep


class LLMError(Exception):
    pass


@lru_cache
def _get_gemini_client() -> genai.Client:
    return genai.Client(api_key=get_settings().gemini_api_key)


@lru_cache
def _get_groq_client() -> groq_sdk.AsyncGroq:
    return groq_sdk.AsyncGroq(api_key=get_settings().groq_api_key)


def _gemini_thinking_config(model: str) -> types.ThinkingConfig:
    """Tắt (hoặc giảm tối đa) phần "suy nghĩ" của Gemini: trả lời từ trích đoạn có sẵn không cần suy luận dài."""
    if model.startswith("gemini-2"):
        return types.ThinkingConfig(thinking_budget=0)
    return types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL)


def _groq_reasoning_args(model: str) -> dict:
    """Qwen3 trên Groq mặc định sinh đoạn <think> trước câu trả lời; tắt đi. Model khác không nhận tham số này."""
    if "qwen3" in model.lower():
        return {"reasoning_effort": "none"}
    return {}


async def _generate_gemini(
    system_prompt: str,
    history: list[dict],
    user_content: str,
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
        max_output_tokens=settings.llm_max_output_tokens,
        thinking_config=_gemini_thinking_config(settings.gemini_model),
    )

    max_retries = max(0, settings.gemini_max_retries)
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = await asyncio.wait_for(
                _get_gemini_client().aio.models.generate_content(
                    model=settings.gemini_model, contents=contents, config=config
                ),
                timeout=settings.gemini_timeout_seconds,
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
) -> tuple[str, int | None]:
    settings = get_settings()
    messages = [{"role": "system", "content": system_prompt}]
    for m in history:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": user_content})

    max_retries = max(0, settings.groq_max_retries)
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = await asyncio.wait_for(
                _get_groq_client().chat.completions.create(
                    model=settings.groq_model,
                    messages=messages,
                    temperature=0.2,
                    max_tokens=settings.llm_max_output_tokens,
                    **_groq_reasoning_args(settings.groq_model),
                ),
                timeout=settings.groq_timeout_seconds,
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


async def generate(
    system_prompt: str,
    history: list[dict],
    user_content: str,
) -> tuple[str, int | None, str]:
    """
    Trả về (answer, tokens, model_name).
    llm_provider có thể là một provider ("groq") hoặc danh sách ưu tiên ("groq,gemini").
    Nếu provider đầu thất bại, tự động thử provider tiếp theo.
    """
    s = get_settings()
    providers = [p.strip() for p in s.llm_provider.split(",") if p.strip()]

    last_error: LLMError | None = None
    for provider in providers:
        try:
            if provider == "groq":
                answer, tokens = await _generate_groq(system_prompt, history, user_content)
                return answer, tokens, s.groq_model
            elif provider == "gemini":
                answer, tokens = await _generate_gemini(system_prompt, history, user_content)
                return answer, tokens, s.gemini_model
            else:
                raise LLMError(f"Provider không hợp lệ: {provider}")
        except LLMError as e:
            logger.warning("Provider '%s' thất bại: %s — thử provider tiếp theo.", provider, e)
            last_error = e

    raise LLMError(f"Tất cả provider thất bại. Lỗi cuối: {last_error}") from last_error
