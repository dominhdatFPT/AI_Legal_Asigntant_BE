"""Tạo embedding (vector) cho văn bản.

- GeminiEmbedder: gọi Gemini API, model gemini-embedding-001, rút còn 768 chiều (MRL).
  Khi nạp văn bản dùng task_type RETRIEVAL_DOCUMENT, khi tìm kiếm dùng RETRIEVAL_QUERY.
- FakeEmbedder: vector băm từ khóa, không gọi mạng. Dùng cho test và chạy thử offline.

Muốn đổi sang model khác (VD bge-m3 chạy trên máy): viết class mới có cùng 4 thứ
`model_name`, `dim`, `embed_documents`, `embed_query`, rồi sửa get_embedder().
Đổi model = phải nạp lại toàn bộ văn bản (vector của 2 model không so sánh được với nhau).
"""

import asyncio
import hashlib
import logging
import math
import re
import threading
import time
import unicodedata
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_CEILING
from functools import lru_cache
from typing import Callable, Protocol

import httpx

from app.core.config import get_settings
from app.schemas.search import EmbeddingUsage

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 503, 504}
_async_http_client: httpx.AsyncClient | None = None
_embedder_lock = threading.Lock()


def _get_async_http_client() -> httpx.AsyncClient:
    global _async_http_client
    if _async_http_client is None:
        _async_http_client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _async_http_client


async def close_async_embedding_client() -> None:
    global _async_http_client
    client, _async_http_client = _async_http_client, None
    if client is not None:
        await client.aclose()
    if get_embedder.cache_info().currsize:
        embedder = get_embedder()
        if isinstance(embedder, GeminiEmbedder):
            await embedder.close_async_client()


class EmbeddingError(Exception):
    attempts = 0  # số lần đã gọi provider trước khi lỗi (để báo embedding_usage.attempts)


class Embedder(Protocol):
    model_name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...

    async def embed_batch_async(self, texts: list[str], timeout: float) -> tuple[list[list[float]], dict, int]: ...


def l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]


class _OnlineQueryMixin:
    """Embedding CÂU HỎI: số lần thử lại lấy từ online_max_retries (mặc định 0, vì mỗi lần gọi đều tính tiền).
    embed_documents (nạp văn bản) giữ vòng thử lại riêng của từng provider."""

    online_max_retries: int = 0
    batch_size: int = 20

    def _query_call(self, batch: list[str], delays: tuple[float, ...]) -> tuple[list[list[float]], dict, int]:
        raise NotImplementedError

    def _online_delays(self) -> tuple[float, ...]:
        return tuple(float(min(i + 1, 2)) for i in range(max(0, self.online_max_retries)))

    def embed_query(self, text: str) -> list[float]:
        return self.embed_queries([text])[0]

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return self.embed_queries_with_usage(texts)[0]

    def embed_queries_with_usage(self, texts: list[str]) -> tuple[list[list[float]], dict, int]:
        """(vectors, {"prompt_tokens", "total_tokens"} do provider báo (rỗng nếu không báo), số lần gọi provider)."""
        out: list[list[float]] = []
        usage: dict = {"prompt_tokens": 0, "total_tokens": 0}
        attempts = 0
        for i in range(0, len(texts), self.batch_size):
            try:
                vectors, u, n = self._query_call(list(texts[i : i + self.batch_size]), self._online_delays())
            except EmbeddingError as e:
                e.attempts += attempts
                raise
            out.extend(vectors)
            attempts += n
            if u and usage:
                for k in usage:
                    usage[k] += int(u.get(k) or 0)
            else:
                usage = {}  # một batch không báo usage thì cả request dùng cận trên
        return out, usage, attempts


def _failed(err: EmbeddingError, attempts: int, cause: BaseException | None = None) -> EmbeddingError:
    err.attempts = attempts
    err.__cause__ = cause
    return err


class GeminiEmbedder(_OnlineQueryMixin):
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-embedding-001",
        dim: int = 768,
        batch_size: int = 20,
        client=None,  # test truyền client giả
        sleep: Callable[[float], None] = time.sleep,
        tokens_per_minute: int = 0,  # 0 = không tự giảm tốc
        clock: Callable[[], float] = time.monotonic,
        online_max_retries: int = 0,
    ):
        if client is None:
            if not api_key:
                raise EmbeddingError("Chưa cấu hình GEMINI_API_KEY")
            from google import genai

            client = genai.Client(api_key=api_key)
        self._client = client
        self._async_client = None
        self._sleep = sleep
        self.model_name = model
        self.dim = dim
        self.batch_size = batch_size
        self.tokens_per_minute = tokens_per_minute
        self._clock = clock
        self.online_max_retries = online_max_retries
        self._sent: deque[tuple[float, int]] = deque()  # (thời điểm gửi, số token ước tính)

    @staticmethod
    def estimate_tokens(texts: list[str]) -> int:
        """Ước lượng token: tiếng Việt khoảng 3 ký tự / token (chỉ dùng để giảm tốc, không cần chính xác)."""
        return sum(len(t) for t in texts) // 3 + 1

    def _throttle(self, tokens: int) -> None:
        """Gói free giới hạn token mỗi phút. Chờ trước khi gửi để không bị 429 liên tục."""
        if self.tokens_per_minute <= 0:
            return
        while True:
            now = self._clock()
            while self._sent and now - self._sent[0][0] >= 60:
                self._sent.popleft()
            used = sum(t for _, t in self._sent)
            if not self._sent or used + tokens <= self.tokens_per_minute:
                break
            wait = 60 - (now - self._sent[0][0]) + 0.5
            logger.info("Gần chạm giới hạn %d token/phút, chờ %.0fs", self.tokens_per_minute, wait)
            self._sleep(wait)
        self._sent.append((self._clock(), tokens))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Dùng khi nạp văn bản. Gói free giới hạn theo phút nên chờ lâu hơn khi bị 429."""
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            self._throttle(self.estimate_tokens(batch))
            out.extend(self._call(batch, "RETRIEVAL_DOCUMENT", delays=(5, 15, 30, 60, 60)))
            logger.info("Đã embedding %d/%d đoạn", len(out), len(texts))
        return out

    def _query_call(self, batch, delays):
        return self._call_ex(batch, "RETRIEVAL_QUERY", delays)

    async def embed_batch_async(self, texts, timeout):
        from google.genai import types

        config = types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=self.dim,
            http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)),
        )
        if self._async_client is None:
            self._async_client = self._client.aio
        # The batch orchestrator supplies the absolute timeout.
        response = await self._async_client.models.embed_content(model=self.model_name, contents=texts, config=config)
        return self._vectors(response, len(texts)), {}, 1

    async def close_async_client(self) -> None:
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None

    def _call(self, batch: list[str], task_type: str, delays: tuple[float, ...]) -> list[list[float]]:
        return self._call_ex(batch, task_type, delays)[0]

    def _call_ex(self, batch: list[str], task_type: str, delays: tuple[float, ...]) -> tuple[list[list[float]], dict, int]:
        from google.genai import errors, types

        config = types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.dim)
        for attempt in range(len(delays) + 1):
            try:
                resp = self._client.models.embed_content(
                    model=self.model_name, contents=batch, config=config
                )
                break
            except errors.APIError as e:
                if e.code in RETRYABLE_STATUS and attempt < len(delays):
                    logger.warning("Gemini embedding lỗi %s, thử lại sau %ss", e.code, delays[attempt])
                    self._sleep(delays[attempt])
                    continue
                raise _failed(EmbeddingError(f"Gemini embedding lỗi {e.code}: {e.message}"), attempt + 1, e)
            except Exception as e:  # lỗi mạng, timeout...
                if attempt < len(delays):
                    self._sleep(delays[attempt])
                    continue
                raise _failed(EmbeddingError(f"Không gọi được Gemini embedding: {e}"), attempt + 1, e)

        return self._vectors(resp, len(batch)), {}, attempt + 1

    def _vectors(self, response, count):
        embeddings = response.embeddings or []
        if len(embeddings) != count:
            raise EmbeddingError(f"Gemini trả {len(embeddings)} vector cho {count} đoạn")
        vectors = []
        for emb in embeddings:
            values = list(emb.values or [])
            if len(values) != self.dim:
                raise EmbeddingError(f"Vector có {len(values)} chiều, cần {self.dim}")
            # gemini-embedding-001 chỉ chuẩn hóa sẵn ở 3072 chiều; 768 chiều phải tự chuẩn hóa
            vectors.append(l2_normalize(values))
        return vectors


_WORD = re.compile(r"\w+", re.UNICODE)


class OpenRouterEmbedder(_OnlineQueryMixin):
    """Gọi OpenRouter /embeddings (VD: google/gemini-embedding-001, dim=768).
    Giữ canonical_model='gemini-embedding-001' để tương thích hoàn toàn không gian vector trong DB."""

    def __init__(
        self,
        api_key: str,
        model: str = "google/gemini-embedding-001",
        canonical_model: str = "gemini-embedding-001",
        base_url: str = "https://openrouter.ai/api/v1",
        dim: int = 768,
        batch_size: int = 20,
        sleep: Callable[[float], None] = time.sleep,
        online_max_retries: int = 0,
        client=None,  # test truyền client giả
        async_client: httpx.AsyncClient | None = None,
    ):
        if not api_key:
            raise EmbeddingError("Chưa cấu hình OPENROUTER_API_KEY")
        self._api_key = api_key
        self._remote_model = model
        self.model_name = canonical_model
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self.dim = dim
        self.batch_size = batch_size
        self._sleep = sleep
        self.online_max_retries = online_max_retries
        self._async_client = async_client
        self._sync_http_override = client is not None and async_client is None
        # Giữ kết nối TCP/TLS dùng lại giữa các request embedding thay vì mở lại mỗi lần
        self._client = client or httpx.Client(
            timeout=60.0,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            out.extend(self._call(batch, delays=(3, 8, 20, 45)))
            logger.info("Đã embedding %d/%d đoạn", len(out), len(texts))
        return out

    def _query_call(self, batch, delays):
        return self._call_ex(batch, delays)

    async def embed_batch_async(self, texts, timeout):
        client = self._async_client or _get_async_http_client()
        price_per_million = get_settings().embedding_price_nano_per_token / 1000
        try:
            response = await client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._remote_model,
                    "input": texts,
                    "dimensions": self.dim,
                    "provider": {"allow_fallbacks": False, "max_price": {"prompt": price_per_million}},
                },
                timeout=timeout,
            )
        except (httpx.PoolTimeout, httpx.ConnectTimeout, httpx.ConnectError, httpx.InvalidURL, httpx.UnsupportedProtocol) as exc:
            raise EmbeddingNotDispatched("Không mở được kết nối tới OpenRouter embedding") from exc
        response.raise_for_status()
        body = response.json()
        items = body["data"]
        if len(items) != len(texts) or {i["index"] for i in items if type(i["index"]) is int} != set(range(len(texts))):
            raise EmbeddingError("OpenRouter embedding trả index không hợp lệ")
        vectors = [l2_normalize(i["embedding"]) for i in sorted(items, key=lambda item: item["index"])]
        return vectors, body.get("usage") or {}, 1

    def _call(self, batch: list[str], delays: tuple[float, ...]) -> list[list[float]]:
        return self._call_ex(batch, delays)[0]

    def _call_ex(self, batch: list[str], delays: tuple[float, ...]) -> tuple[list[list[float]], dict, int]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._remote_model,
            "input": batch,
            "dimensions": self.dim,
        }
        data = None
        for attempt in range(len(delays) + 1):
            try:
                resp = self._client.post(self._url, headers=headers, json=payload)
                if resp.status_code in RETRYABLE_STATUS and attempt < len(delays):
                    logger.warning("OpenRouter embedding lỗi %s, thử lại sau %ss", resp.status_code, delays[attempt])
                    self._sleep(delays[attempt])
                    continue
                if resp.status_code >= 400:
                    raise _failed(EmbeddingError(f"OpenRouter embedding lỗi {resp.status_code}: {resp.text[:300]}"), attempt + 1)
                data = resp.json()
                break
            except EmbeddingError:
                raise
            except Exception as e:
                if attempt < len(delays):
                    self._sleep(delays[attempt])
                    continue
                raise _failed(EmbeddingError(f"Không gọi được OpenRouter embedding: {e}"), attempt + 1, e)

        items = sorted((data or {}).get("data") or [], key=lambda x: x.get("index", 0))
        if len(items) != len(batch):
            raise EmbeddingError(f"OpenRouter trả {len(items)} vector cho {len(batch)} đoạn")
        vectors = []
        for item in items:
            values = list(item.get("embedding") or [])
            if len(values) != self.dim:
                raise EmbeddingError(f"Vector có {len(values)} chiều, cần {self.dim}")
            vectors.append(l2_normalize(values))
        return vectors, dict((data or {}).get("usage") or {}), attempt + 1


class FakeEmbedder:
    """Vector 'túi từ' băm vào `dim` ô. Câu có nhiều từ chung → vector gần nhau.
    Không hiểu nghĩa (không biết 'sổ đỏ' = 'giấy chứng nhận'), chỉ để test luồng."""

    def __init__(self, dim: int = 768):
        self.dim = dim
        self.model_name = f"fake-hash-{dim}"

    def _vec(self, text: str) -> list[float]:
        text = unicodedata.normalize("NFC", text).lower()
        words = _WORD.findall(text)
        features = words + [f"{a} {b}" for a, b in zip(words, words[1:])]
        vec = [0.0] * self.dim
        for f in features:
            h = int.from_bytes(hashlib.md5(f.encode()).digest()[:8], "big")
            vec[h % self.dim] += 1.0 if (h >> 32) & 1 else -1.0
        return l2_normalize(vec)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    async def embed_batch_async(self, texts, timeout):
        return self.embed_documents(texts), {}, 0


@lru_cache
def _build_embedder() -> Embedder:
    s = get_settings()
    from app.models.chunk import EMBEDDING_DIM

    if s.embedding_dim != EMBEDDING_DIM:
        raise EmbeddingError(
            f"EMBEDDING_DIM={s.embedding_dim} không khớp vector({EMBEDDING_DIM}) trong database"
        )
    provider = s.embedding_provider.lower()
    if provider == "fake":
        return FakeEmbedder(s.embedding_dim)
    if provider == "openrouter":
        return OpenRouterEmbedder(
            api_key=s.openrouter_api_key,
            model=s.openrouter_embedding_model,
            canonical_model=s.embedding_model,
            base_url=s.openrouter_base_url,
            dim=s.embedding_dim,
            batch_size=s.embedding_batch_size,
            online_max_retries=s.embedding_online_max_retries,
        )
    if provider == "gemini":
        return GeminiEmbedder(
            api_key=s.gemini_api_key,
            model=s.embedding_model,
            dim=s.embedding_dim,
            batch_size=s.embedding_batch_size,
            tokens_per_minute=s.embedding_tokens_per_minute,
            online_max_retries=s.embedding_online_max_retries,
        )
    raise EmbeddingError(f"EMBEDDING_PROVIDER không hợp lệ: {s.embedding_provider}")


def get_embedder() -> Embedder:
    # The cache alone allows concurrent misses to construct different instances.
    # All online requests must use the same identity for embedding single-flight.
    with _embedder_lock:
        return _build_embedder()


get_embedder.cache_clear = _build_embedder.cache_clear
get_embedder.cache_info = _build_embedder.cache_info


# ---------------------------------------------------------------- Ngân sách embedding online cho 1 request search

# Cắt còn dưới ngần này token thì bỏ hẳn: một vector từ vài từ vẫn tính tiền mà không giúp được gì
MIN_TRUNCATED_TOKENS = 8


def estimate_query_tokens(text: str) -> int:
    """Cận trên bảo thủ số token của chuỗi gửi đi embedding: ceil(số byte UTF-8 / 2).
    Tiếng Việt thực tế khoảng 3-4 ký tự / token, nên cận này cao hơn thực tế vài lần: ngân sách không bao giờ bị vượt."""
    return (len(text.encode("utf-8")) + 1) // 2


def fit_to_token_budget(text: str, budget: int) -> str:
    """Cắt text (theo ranh giới từ) cho estimate_query_tokens <= budget. Không cắt được thì trả chuỗi rỗng."""
    if budget <= 0:
        return ""
    if estimate_query_tokens(text) <= budget:
        return text
    cut = text.encode("utf-8")[: budget * 2].decode("utf-8", "ignore")
    if not text[len(cut)].isspace() and not cut[-1:].isspace():  # đang cắt giữa từ -> lùi về từ trước
        cut = cut.rsplit(None, 1)[0] if len(cut.split()) > 1 else ""
    return cut.rstrip()


@dataclass
class EmbedOutcome:
    vectors: list  # tuple[float, ...] | None cho từng text đầu vào (None = không embedding: hết ngân sách / lỗi)
    truncated: list[bool]  # text đã bị cắt: vector KHÔNG ứng với text gốc nên không được cache theo text gốc
    usage: EmbeddingUsage
    error: Exception | None = None  # lỗi provider (đã nuốt để search vẫn trả exact/BM25); người gọi quyết định có ném lại không
    provider_seconds: float = field(default=0.0, repr=False, compare=False)


class EmbeddingNotDispatched(EmbeddingError):
    """No request reached the embedding provider."""


def budgeted_inputs(texts: list[str], budget: int, usage: EmbeddingUsage) -> dict[str, str]:
    """Fit one item's expanded queries into its own budget."""
    if not texts:
        return {}
    if budget <= 0:
        usage.skipped_reason = "budget_zero"
        return {}

    remaining = budget
    plan = {}
    for text in dict.fromkeys(texts):
        send = text
        if estimate_query_tokens(text) > remaining:
            usage.budget_capped = True
            send = fit_to_token_budget(text, remaining) if remaining >= MIN_TRUNCATED_TOKENS else ""
        if send.strip():
            plan[text] = send
            remaining -= estimate_query_tokens(send)
    if not plan:
        usage.skipped_reason = "budget_exhausted"
    return plan


_PROVIDER_TIMEOUT = 60.0
_FlightKey = tuple[asyncio.AbstractEventLoop, int, str, str]
_flights: dict[_FlightKey, asyncio.Task] = {}


def _embedding_cost(inputs):
    return sum(map(estimate_query_tokens, inputs)) * get_settings().embedding_price_nano_per_token


async def _embed_new_inputs(embedder, inputs):
    usage = EmbeddingUsage()
    reserved_cost = _embedding_cost(inputs)
    try:
        async_method = getattr(embedder, "embed_batch_async", None)
        async with asyncio.timeout(_PROVIDER_TIMEOUT):
            if async_method is None or getattr(embedder, "_sync_http_override", False):
                vectors, provider_usage, attempts = await asyncio.to_thread(_call_provider, embedder, inputs)
            else:
                vectors, provider_usage, attempts = await async_method(inputs, _PROVIDER_TIMEOUT)
        if len(vectors) != len(inputs) or any(
            len(vec) != embedder.dim or not all(math.isfinite(value) for value in vec) for vec in vectors
        ):
            raise EmbeddingError("Embedding batch trả vector không hợp lệ")
        by_text = {text: tuple(vec) for text, vec in zip(inputs, vectors)}
        usage.attempts = attempts
        if attempts:
            reported = provider_usage.get("prompt_tokens") or provider_usage.get("total_tokens")
            if type(reported) is int and reported > 0:
                usage.prompt_tokens = reported
                reported_cost = provider_usage.get("cost")
                if reported_cost is None:
                    usage.cost_nano_usd = reported * get_settings().embedding_price_nano_per_token
                else:
                    amount = Decimal(str(reported_cost))
                    if not amount.is_finite() or amount < 0:
                        raise EmbeddingError("Embedding batch trả chi phí không hợp lệ")
                    usage.cost_nano_usd = int((amount * 1_000_000_000).to_integral_value(rounding=ROUND_CEILING))
            else:
                usage.usage_uncertain = True
                usage.cost_nano_usd = reserved_cost
        return by_text, usage
    except EmbeddingNotDispatched:
        usage.skipped_reason = "embedding_failed"
    except Exception as exc:
        logger.warning("Batch embedding failed (%s)", type(exc).__name__)
        usage.attempts = max(1, getattr(exc, "attempts", 0))
        usage.skipped_reason = "embedding_failed"
        usage.usage_uncertain = True
        usage.cost_nano_usd = reserved_cost
    return {}, usage

async def embed_batch_async(embedder, inputs: list[str], deadline: float, usage: EmbeddingUsage) -> dict:
    """Coalesce exact inputs, including overlaps between otherwise different batches."""
    if not inputs:
        return {}
    timeout = deadline - time.monotonic()
    if timeout <= 0:
        usage.skipped_reason = "embedding_failed"
        return {}

    loop = asyncio.get_running_loop()
    keys = {text: (loop, id(embedder), embedder.model_name, text) for text in inputs}
    flights = {text: _flights[key] for text, key in keys.items() if key in _flights}
    new_inputs = [text for text in keys if text not in flights]
    owned = None
    if new_inputs:
        owned = loop.create_task(_embed_new_inputs(embedder, new_inputs))
        for text in new_inputs:
            _flights[keys[text]] = flights[text] = owned

        def finished(task):
            for text in new_inputs:
                _flights.pop(keys[text], None)
            if not task.cancelled():
                task.exception()

        owned.add_done_callback(finished)

    tasks = set(flights.values())
    try:
        async with asyncio.timeout(timeout):
            await asyncio.gather(*(asyncio.shield(task) for task in tasks))
    except TimeoutError:
        # The caller's deadline does not change the provider's deadline.
        pass

    completed = {task: task.result() for task in tasks if task.done() and not task.cancelled()}
    if owned in completed:
        _, billed = completed[owned]
        usage.attempts = billed.attempts
        usage.prompt_tokens = billed.prompt_tokens
        usage.cost_nano_usd = billed.cost_nano_usd
        usage.usage_uncertain = billed.usage_uncertain
    elif owned is not None:
        usage.attempts = 1
        usage.cost_nano_usd = _embedding_cost(new_inputs)
        usage.usage_uncertain = True

    vectors = {}
    for text, task in flights.items():
        if task in completed and text in completed[task][0]:
            vectors[text] = completed[task][0][text]
            if task is not owned:
                usage.cached_tokens += estimate_query_tokens(text)
    if len(vectors) != len(keys):
        usage.skipped_reason = "embedding_failed"
    return vectors


def _call_provider(embedder, texts: list[str]) -> tuple[list, dict, int]:
    """(vectors, usage do provider báo, số lần gọi provider). Embedder tối giản chỉ có embed_query vẫn dùng được."""
    if hasattr(embedder, "embed_queries_with_usage"):
        return embedder.embed_queries_with_usage(texts)
    if len(texts) == 1:
        return [embedder.embed_query(texts[0])], {}, 1
    if hasattr(embedder, "embed_queries"):
        return embedder.embed_queries(texts), {}, 1
    with ThreadPoolExecutor(max_workers=len(texts)) as pool:
        return list(pool.map(embedder.embed_query, texts)), {}, len(texts)


def embed_queries_budgeted(
    embedder, texts: list[str], budget: int, *, request_turn_id: str | None = None, cached_tokens: int = 0
) -> EmbedOutcome:
    """Embedding online các truy vấn ĐÃ mở rộng, tổng token không vượt `budget` (ước lượng bảo thủ).
    budget = 0: không gọi provider. Trùng lặp chỉ gửi 1 lần. Truy vấn đứng trước được ưu tiên ngân sách; truy vấn quá hạn mức
    còn lại bị cắt theo ranh giới từ (budget_capped=True) hoặc bỏ. Không retry ở đây: retry do embedder.online_max_retries."""
    price = get_settings().embedding_price_nano_per_token
    usage = EmbeddingUsage(
        model=getattr(embedder, "model_name", None), request_turn_id=request_turn_id, cached_tokens=cached_tokens
    )
    out = EmbedOutcome([None] * len(texts), [False] * len(texts), usage)
    plan = budgeted_inputs(texts, budget, usage)
    if not plan:
        return out

    sent = list(plan.values())
    provider_started = time.perf_counter()
    try:
        vectors, provider_usage, attempts = _call_provider(embedder, sent)
    except Exception as e:  # lỗi provider: các nhánh exact/BM25 vẫn phải trả được kết quả
        usage.attempts = max(1, getattr(e, "attempts", 0))
        usage.skipped_reason = "embedding_failed"
        # Keep the legacy single-search cost field and flag it as unconfirmed.
        usage.usage_uncertain = True
        out.error = e
        return out
    finally:
        out.provider_seconds = time.perf_counter() - provider_started
    usage.attempts = attempts
    billed = int(provider_usage.get("prompt_tokens") or 0) or int(provider_usage.get("total_tokens") or 0)
    usage.prompt_tokens = billed or sum(estimate_query_tokens(t) for t in sent)
    usage.cost_nano_usd = usage.prompt_tokens * price
    by_text = {orig: tuple(vec) for orig, vec in zip(plan, vectors)}
    for i, text in enumerate(texts):
        out.vectors[i] = by_text.get(text)
        out.truncated[i] = text in plan and plan[text] != text
    return out


def embed_query_with_usage(
    embedder, text: str, budget: int, request_turn_id: str | None = None
) -> tuple[list[float] | None, EmbeddingUsage]:
    """(vector | None, EmbeddingUsage) cho một truy vấn; lỗi provider ném lại như embed_query."""
    outcome = embed_queries_budgeted(embedder, [text], budget, request_turn_id=request_turn_id)
    if outcome.error is not None:
        raise outcome.error
    vec = outcome.vectors[0]
    return (list(vec) if vec is not None else None), outcome.usage
