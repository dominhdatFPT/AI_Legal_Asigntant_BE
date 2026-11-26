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
from app.services.case_budget import BudgetExhaustedError, estimate_chat_input_upper_bound, get_pricing

logger = logging.getLogger(__name__)

GEMINI_RETRYABLE_STATUS = {429, 500, 503, 504}
GROQ_RETRYABLE_STATUS = {429, 500, 503, 504}
OPENROUTER_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_sleep = asyncio.sleep


class LLMError(Exception):
    pass


class Completion(tuple):
    """Tuple-compatible result; existing two/three-value unpacking is preserved."""
    finish_reason: str | None
    truncated: bool

    def __new__(cls, values: tuple, finish_reason: str | None = None, *, usage_breakdown=None):
        result = super().__new__(cls, values)
        result.usage_breakdown = usage_breakdown
        result.finish_reason = finish_reason
        result.truncated = finish_reason == "length"
        return result


def _usage_breakdown(usage, gemini=False):
    if usage is None:
        return None
    names = ({"prompt_tokens": "prompt_token_count", "completion_tokens": "candidates_token_count", "cached_tokens": "cached_content_token_count", "reasoning_tokens": "thoughts_token_count", "total_tokens": "total_token_count"} if gemini else {"prompt_tokens": "prompt_tokens", "completion_tokens": "completion_tokens", "total_tokens": "total_tokens"})
    result = {key: getattr(usage, name, None) for key, name in names.items() if getattr(usage, name, None) is not None}
    if not gemini:
        for key, detail, attr in (("cached_tokens", "prompt_tokens_details", "cached_tokens"), ("reasoning_tokens", "completion_tokens_details", "reasoning_tokens")):
            value = getattr(getattr(usage, detail, None), attr, None)
            if value is not None:
                result[key] = value
        cost = getattr(usage, "cost", None)
        if cost is None:
            cost = (getattr(usage, "model_extra", None) or {}).get("cost")
        if cost is not None:
            result["cost_usd"] = cost
    return result or None

def _finish_reason(response, gemini: bool = False) -> str | None:
    choices = getattr(response, "candidates" if gemini else "choices", None)
    reason = getattr(choices[0], "finish_reason", None) if choices else None
    reason = getattr(reason, "value", reason)
    if reason is None:
        return None
    return "length" if str(reason).upper() in {"LENGTH", "MAX_TOKENS"} else str(reason).lower()


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


def _openrouter_reasoning_args(budget: int) -> dict:
    """Explicitly disable reasoning at zero; exclude only hides billed tokens.

    Positive budgets are an intentional opt-in (also for mandatory reasoning
    models). Do not silently retry a rejected 'none' with paid reasoning on.
    """
    return {"extra_body": {"reasoning": {"max_tokens": budget} if budget > 0 else {"effort": "none"}}}


async def _generate_gemini(
    system_prompt: str,
    history: list[dict],
    user_content: str,
    max_output_tokens: int | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    budget_usage=None,
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

    tokens = resp.usage_metadata.total_token_count if resp.usage_metadata else None
    breakdown = _usage_breakdown(resp.usage_metadata, True)
    if budget_usage is not None:
        budget_usage.update(tokens=tokens, usage_breakdown=breakdown)
    if not resp.text:
        raise LLMError("Gemini trả về rỗng")

    return Completion((resp.text, tokens), _finish_reason(resp, gemini=True), usage_breakdown=breakdown)


async def _generate_groq(
    system_prompt: str,
    history: list[dict],
    user_content: str,
    max_output_tokens: int | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    budget_usage=None,
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

    tokens = resp.usage.total_tokens if resp.usage else None
    breakdown = _usage_breakdown(resp.usage)
    if budget_usage is not None:
        budget_usage.update(tokens=tokens, usage_breakdown=breakdown)
    answer = resp.choices[0].message.content if resp.choices else None
    if not answer:
        raise LLMError("Groq trả về rỗng")

    return Completion((answer, tokens), _finish_reason(resp), usage_breakdown=breakdown)


async def _generate_openrouter(
    system_prompt: str,
    history: list[dict],
    user_content: str,
    max_output_tokens: int | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    reasoning_max_tokens: int | None = None,
    model: str | None = None,
    budget_pricing=None,
    budget_usage=None,
    response_format: dict | None = None,
) -> tuple[str, int | None]:
    settings = get_settings()
    model = model or settings.openrouter_model
    if not settings.openrouter_api_key or not model:
        raise LLMError("Chưa cấu hình OPENROUTER_API_KEY hoặc OPENROUTER_MODEL")
    messages = [{"role": "system", "content": system_prompt}]
    for m in history:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": user_content})
    reasoning = max(0, settings.openrouter_reasoning_max_tokens
                    if reasoning_max_tokens is None else reasoning_max_tokens)
    reasoning_args = _openrouter_reasoning_args(reasoning)
    if budget_pricing:
        reasoning_args["extra_body"]["provider"] = {"max_price": budget_pricing["max_price"]}

    max_retries = max(0, settings.openrouter_max_retries if max_retries is None else max_retries)
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = await asyncio.wait_for(
                _get_openrouter_client().chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.2,
                    max_tokens=(max_output_tokens or settings.llm_max_output_tokens) + reasoning,
                    **reasoning_args,
                    **({"response_format": response_format} if response_format else {}),
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

    tokens = resp.usage.total_tokens if resp.usage else None
    breakdown = _usage_breakdown(resp.usage)
    if budget_usage is not None:
        budget_usage.update(tokens=tokens, usage_breakdown=breakdown)
    answer = resp.choices[0].message.content if resp.choices else None
    if not answer:
        if resp.choices and resp.choices[0].finish_reason == "length":
            raise LLMError("OpenRouter trả về rỗng và hết giới hạn đầu ra; kiểm tra reasoning của model, "
                           "OPENROUTER_REASONING_MAX_TOKENS và LLM_MAX_OUTPUT_TOKENS")
        raise LLMError("OpenRouter trả về rỗng")

    return Completion((answer, tokens), _finish_reason(resp), usage_breakdown=breakdown)


async def generate(
    system_prompt: str,
    history: list[dict],
    user_content: str,
    max_output_tokens: int | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    reasoning_max_tokens: int | None = None,
    model: str | None = None,
    budget=None,
    budget_stage="responder",
    response_format: dict | None = None,
) -> tuple[str, int | None, str]:
    """
    Trả về (answer, tokens, model_name).
    llm_provider có thể là một provider ("groq") hoặc danh sách ưu tiên ("openrouter,gemini").
    Nếu provider đầu thất bại, tự động thử provider tiếp theo.
    Ba tham số đầu ghi đè cấu hình mặc định (chỉ chế độ rà soát dùng); không truyền thì giữ hành vi cũ.
    `reasoning_max_tokens` (lượt gọi phụ như chấm liên quan) chỉ tác dụng với OpenRouter: không truyền thì lấy
    OPENROUTER_REASONING_MAX_TOKENS như cũ, 0 = tắt suy luận, số dương = chừa chừng đó token. Gemini trực tiếp
    (luôn tắt suy nghĩ) và Groq bỏ qua tham số này.
    `model` (lượt gọi phụ, cấu hình OPENROUTER_AUX_MODEL) cũng chỉ tác dụng với OpenRouter: có giá trị thì gọi bằng model đó thay cho
    OPENROUTER_MODEL và model_name trả về là model đó; không truyền thì như cũ. Gemini và Groq bỏ qua. Model phụ lỗi thì đi tiếp
    provider kế tiếp như mọi lỗi khác.
    """
    options = {"max_output_tokens": max_output_tokens, "timeout_seconds": timeout_seconds, "max_retries": max_retries}
    s = get_settings()
    providers = [p.strip() for p in s.llm_provider.split(",") if p.strip()]

    last_error: LLMError | None = None
    dispatched = False
    for provider in providers:
        reservation = None
        result = None
        attempt_usage = {}
        if budget is not None and budget.enabled:
            model_name = {"groq": s.groq_model, "gemini": s.gemini_model, "openrouter": model or s.openrouter_model}.get(provider, "")
            reasoning = max(0, s.openrouter_reasoning_max_tokens if reasoning_max_tokens is None else reasoning_max_tokens) if provider == "openrouter" else 0
            out_cap = budget.clamp_output(budget_stage, max_output_tokens or s.llm_max_output_tokens)
            reservation = budget.reserve(budget_stage, provider, model_name, estimate_chat_input_upper_bound(system_prompt, history, user_content, budget.strict_byte_upper_bound), out_cap, reasoning_tokens=reasoning)
            if reservation is None:
                last_error = BudgetExhaustedError("Budget or route unavailable")
                continue
            # Không retry ngầm; output gửi đi đúng bằng cap đã giữ chỗ
            options = {**options, "max_retries": 0, "max_output_tokens": out_cap}
        try:
            if reservation:
                budget.mark_dispatched(reservation)
                dispatched = True
            if provider == "groq":
                result = await _generate_groq(system_prompt, history, user_content, **options, **({"budget_usage": attempt_usage} if reservation else {}))
                return Completion((*result, s.groq_model), getattr(result, "finish_reason", None), usage_breakdown=getattr(result, "usage_breakdown", None))
            elif provider == "gemini":
                result = await _generate_gemini(system_prompt, history, user_content, **options, **({"budget_usage": attempt_usage} if reservation else {}))
                return Completion((*result, s.gemini_model), getattr(result, "finish_reason", None), usage_breakdown=getattr(result, "usage_breakdown", None))
            elif provider == "openrouter":
                result = await _generate_openrouter(
                    system_prompt, history, user_content, **options, reasoning_max_tokens=reasoning_max_tokens, model=model,
                    response_format=response_format,
                    **({"budget_usage": attempt_usage, **({"budget_pricing": get_pricing(provider, model or s.openrouter_model)} if budget.enforce_max_price else {})} if reservation else {})
                )
                return Completion((*result, model or s.openrouter_model), getattr(result, "finish_reason", None), usage_breakdown=getattr(result, "usage_breakdown", None))
            else:
                raise LLMError(f"Provider không hợp lệ: {provider}")
        except LLMError as e:
            logger.warning("Provider '%s' thất bại: %s — thử provider tiếp theo.", provider, e)
            last_error = e
        finally:
            if reservation:
                budget.settle(reservation, usage_breakdown=getattr(result, "usage_breakdown", None) if result is not None else attempt_usage.get("usage_breakdown"), total_tokens=result[1] if result is not None else attempt_usage.get("tokens"), sent=reservation.dispatched, finish_reason=getattr(result, "finish_reason", None))

    if budget is not None and budget.enabled and not dispatched:
        raise BudgetExhaustedError("Ngân sách token hoặc chi phí cho lượt xử lý đã hết hoặc tuyến chưa xác minh")
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


async def _stream_groq(system_prompt: str, history: list[dict], user_content: str, usage: dict, **options) -> AsyncIterator[str]:
    """Groq không stream: lấy cả câu rồi phát một lần. Không thử lại cùng provider."""
    result = await _generate_groq(system_prompt, history, user_content,
                                  **{k: v for k, v in options.items() if k in {"max_output_tokens", "timeout_seconds"}}, max_retries=0,
                                  **({"budget_usage": usage} if options.get("budget_active") else {}))
    answer, tokens = result
    if options.get("budget_active"):
        usage["usage_breakdown"] = getattr(result, "usage_breakdown", None)
    usage.update(tokens=tokens, finish_reason=getattr(result, "finish_reason", None),
                 truncated=getattr(result, "truncated", False))
    yield answer


async def _stream_gemini(system_prompt: str, history: list[dict], user_content: str, usage: dict, **options) -> AsyncIterator[str]:
    settings = get_settings()
    contents = [
        types.Content(role="model" if m["role"] == "assistant" else "user", parts=[types.Part(text=m["content"])])
        for m in history
    ]
    contents.append(types.Content(role="user", parts=[types.Part(text=user_content)]))
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=0.2,
        max_output_tokens=options.get("max_output_tokens") or settings.llm_max_output_tokens,
        thinking_config=_gemini_thinking_config(settings.gemini_model),
    )
    emitted = False
    try:
        opened = _get_gemini_client().aio.models.generate_content_stream(
            model=settings.gemini_model, contents=contents, config=config
        )
        async with contextlib.aclosing(_timed_stream(opened, options.get("timeout_seconds") or settings.gemini_timeout_seconds)) as chunks:
            async for chunk in chunks:
                reason = _finish_reason(chunk, gemini=True)
                if reason:
                    usage.update(finish_reason=reason, truncated=reason == "length")
                if chunk.usage_metadata:
                    usage["tokens"] = chunk.usage_metadata.total_token_count
                    if options.get("budget_active"):
                        usage["usage_breakdown"] = _usage_breakdown(chunk.usage_metadata, True)
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
    system_prompt: str, history: list[dict], user_content: str, usage: dict, **options,
) -> AsyncIterator[str]:
    settings = get_settings()
    effective_model = (options.get("model") or settings.openrouter_model or "").strip()
    if not settings.openrouter_api_key or not effective_model:
        raise LLMError("Chưa cấu hình OPENROUTER_API_KEY hoặc OPENROUTER_MODEL")
    reasoning = max(0, options.get("reasoning_max_tokens", settings.openrouter_reasoning_max_tokens))
    reasoning_args = _openrouter_reasoning_args(reasoning)
    if options.get("budget_pricing"):
        reasoning_args["extra_body"]["provider"] = {"max_price": options["budget_pricing"]["max_price"]}
    emitted = False
    try:
        opened = _get_openrouter_client().chat.completions.create(
            model=effective_model,
            messages=_chat_messages(system_prompt, history, user_content),
            temperature=0.2,
            max_tokens=(options.get("max_output_tokens") or settings.llm_max_output_tokens) + reasoning,
            stream=True,
            stream_options={"include_usage": True},  # mẩu cuối mang usage (không có choices)
            **reasoning_args,
        )
        async with contextlib.aclosing(_timed_stream(opened, options.get("timeout_seconds") or settings.openrouter_timeout_seconds)) as chunks:
            async for chunk in chunks:
                if getattr(chunk, "usage", None):
                    usage["tokens"] = chunk.usage.total_tokens
                    detail = _usage_breakdown(chunk.usage)
                    if detail and any(k != "total_tokens" for k in detail):
                        usage["usage_breakdown"] = detail
                reason = _finish_reason(chunk)
                if reason:
                    usage.update(finish_reason=reason, truncated=reason == "length")
                text = chunk.choices[0].delta.content if getattr(chunk, "choices", None) else None
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
    max_output_tokens: int | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    reasoning_max_tokens: int | None = None,
    model: str | None = None,
    budget=None,
    budget_stage="responder",
) -> AsyncIterator[str]:
    """Phát từng mẩu chữ của câu trả lời. Khi stream kết thúc, `info` (nếu truyền) được điền {"model", "tokens"}.
    Đi theo thứ tự `llm_provider` như `generate`, nhưng chỉ chuyển sang provider kế tiếp khi provider đó CHƯA phát
    mẩu chữ nào; đã phát rồi mà lỗi thì ném LLMError (người dùng đã thấy chữ nên không thể bắt đầu lại).
    Mỗi mẩu chờ tối đa timeout của provider, không thử lại cùng provider."""
    options = {k: v for k, v in {"max_output_tokens": max_output_tokens, "timeout_seconds": timeout_seconds,
               "reasoning_max_tokens": reasoning_max_tokens, "model": model}.items() if v is not None}
    s = get_settings()
    providers = [p.strip() for p in s.llm_provider.split(",") if p.strip()]
    streams = {
        "groq": (_stream_groq, s.groq_model),
        "gemini": (_stream_gemini, s.gemini_model),
        "openrouter": (_stream_openrouter, s.openrouter_model),
    }

    last_error: LLMError | None = None
    dispatched = False
    for provider in providers:
        emitted = False
        usage: dict = {}
        reservation = None
        if budget is not None and budget.enabled:
            model_name = (model or s.openrouter_model) if provider == "openrouter" else streams.get(provider, (None, ""))[1]
            reasoning = max(0, s.openrouter_reasoning_max_tokens if reasoning_max_tokens is None else reasoning_max_tokens) if provider == "openrouter" else 0
            out_cap = budget.clamp_output(budget_stage, max_output_tokens or s.llm_max_output_tokens)
            reservation = budget.reserve(budget_stage, provider, model_name, estimate_chat_input_upper_bound(system_prompt, history, user_content, budget.strict_byte_upper_bound), out_cap, reasoning_tokens=reasoning)
            if reservation is None:
                last_error = BudgetExhaustedError("Budget or route unavailable")
                continue
            options["budget_active"] = True
            options["max_output_tokens"] = out_cap
            if provider == "openrouter" and budget.enforce_max_price:
                options["budget_pricing"] = get_pricing(provider, model_name)
        try:
            if reservation:
                budget.mark_dispatched(reservation)
                dispatched = True
            if provider not in streams:
                raise LLMError(f"Provider không hợp lệ: {provider}")
            stream_fn, model_name = streams[provider]
            async with contextlib.aclosing(stream_fn(system_prompt, history, user_content, usage, **options)) as texts:
                async for text in texts:
                    emitted = True
                    yield text
        except LLMError as e:
            if emitted:
                raise LLMError(f"{provider} lỗi giữa chừng khi stream: {e}") from e
            logger.warning("Provider '%s' thất bại: %s — thử provider tiếp theo.", provider, e)
            last_error = e
            continue
        finally:
            if reservation:
                budget.settle(reservation, usage_breakdown=usage.get("usage_breakdown"), total_tokens=usage.get("tokens"), sent=reservation.dispatched, finish_reason=usage.get("finish_reason"))
        if info is not None:
            info.update(model=model_name, tokens=usage.get("tokens"))
            if provider == "openrouter" and model:
                info["model"] = model
            if usage.get("usage_breakdown") and any(k != "total_tokens" for k in usage["usage_breakdown"]):
                info["usage_breakdown"] = usage["usage_breakdown"]
            if usage.get("finish_reason"):
                info.update(finish_reason=usage["finish_reason"], truncated=usage.get("truncated", False))
        return

    if budget is not None and budget.enabled and not dispatched:
        raise BudgetExhaustedError("Ngân sách token hoặc chi phí cho lượt xử lý đã hết hoặc tuyến chưa xác minh")
    raise LLMError(f"Tất cả provider thất bại. Lỗi cuối: {last_error}") from last_error
