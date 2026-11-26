# HTTP client gọi rag-service qua internal token
# Hợp đồng: services/rag-service/openapi.yaml, POST /internal/v1/rag/search
import asyncio
import copy
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from http.cookiejar import DefaultCookiePolicy

import httpx

from app.core.config import get_settings
from app.services import cache
from app.services.case_budget import estimate_text_tokens

logger = logging.getLogger(__name__)

MAX_QUERY_LENGTH = 20000  # cùng giới hạn input API; không cắt đầu vào âm thầm

# Circuit breaker: lỗi liên tiếp thì ngừng gọi rag-service một lúc, ném đúng RAGError như khi rag lỗi.
# shortcut: đếm theo từng process, không khoá; đủ cho 1 worker, khi chạy nhiều replica thì cân nhắc thư viện.
_FAIL_LIMIT = 3
_OPEN_SECONDS = 30
_fails = 0
_open_until = 0.0

_http_client: httpx.AsyncClient | None = None
_http_loop: asyncio.AbstractEventLoop | None = None
_http_timeout: float | None = None


async def _get_http_client(timeout: float) -> httpx.AsyncClient:
    """Dùng chung httpx.AsyncClient trong cùng event loop để tái sử dụng kết nối TCP."""
    global _http_client, _http_loop, _http_timeout
    loop = asyncio.get_running_loop()
    if (
        _http_client is not None
        and not _http_client.is_closed
        and _http_loop is loop
        and _http_timeout == timeout
    ):
        return _http_client
    old_client, old_loop = _http_client, _http_loop
    _http_client = httpx.AsyncClient(
        timeout=timeout,
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
    )
    # Dùng chung cho mọi người dùng (cả document_client): không lưu/gửi lại cookie do service khác đặt.
    _http_client.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))
    _http_loop = loop
    _http_timeout = timeout
    if old_client is not None and not old_client.is_closed and old_loop is loop and not loop.is_closed():
        await old_client.aclose()
    return _http_client


async def close_http_client() -> None:
    """Đóng client HTTP dùng chung khi service tắt."""
    global _http_client, _http_loop, _http_timeout
    client, loop = _http_client, _http_loop
    _http_client, _http_loop, _http_timeout = None, None, None
    if client is not None and not client.is_closed and loop is asyncio.get_running_loop():
        await client.aclose()


# Version the cache whenever retrieval semantics or cache options change.
_RETRIEVAL_PIPELINE_VERSION = "hybrid-rrf-v2"
_SINGLE_PATH = "/internal/v1/rag/search"
_BATCH_PATH = "/internal/v1/rag/search/batch"
_BATCH_STATUSES = {"ok", "degraded", "overloaded", "error"}
_PREPROCESS_REJECTIONS = {401, 403, 422}
_SCHEDULING_ERRORS = {"RAG_OVERLOADED", "RAG_RETRIEVAL_OVERLOADED", "DEADLINE_EXCEEDED"}
_cache: "cache.TTLCache | cache.ResilientCache | None" = None


def _get_cache():
    """Tạo lần đầu cần dùng, theo cấu hình lúc đó."""
    global _cache
    if _cache is None:
        s = get_settings()
        _cache = cache.make_cache("rag", s.rag_cache_max_items, s.rag_cache_ttl_seconds)
    return _cache


def clear_cache() -> None:
    """Bỏ đối tượng cache để lần dùng sau tạo lại theo cấu hình (cho test); KHÔNG xoá khoá trong Redis."""
    global _cache
    _cache = None


class RAGError(Exception):
    pass


class ChunkList(list):
    """Danh sách chunk như cũ (so sánh/duyệt y hệt list), kèm `embedding_usage` của rag-service khi có."""
    embedding_usage: dict | None = None


def _record_failure() -> None:
    global _fails, _open_until
    _fails += 1
    if _fails >= _FAIL_LIMIT:
        _open_until = time.monotonic() + _OPEN_SECONDS
        _fails = 0


async def _post(path: str, payload: dict, request_id: str | None, transport) -> httpx.Response:
    """POST tới rag-service kèm internal token. Lỗi mạng đổi thành RAGError; mã HTTP để nơi gọi tự xử lý."""
    global _fails
    if time.monotonic() < _open_until:
        logger.warning("rag-service tạm ngắt do lỗi liên tiếp, bỏ qua %s", path)
        raise RAGError("rag-service tạm ngắt do lỗi liên tiếp")
    settings = get_settings()
    headers = {"X-Internal-Token": settings.internal_token}
    if request_id:
        headers["X-Request-ID"] = request_id
    url = f"{settings.rag_service_url.rstrip('/')}{path}"
    try:
        if transport is not None:
            async with httpx.AsyncClient(timeout=settings.rag_timeout_seconds, transport=transport) as client:
                resp = await client.post(url, json=payload, headers=headers)
        else:
            client = await _get_http_client(settings.rag_timeout_seconds)
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as e:
        _record_failure()
        raise RAGError("rag-service quá thời gian chờ") from e
    except httpx.RequestError as e:
        _record_failure()
        raise RAGError("Không kết nối được rag-service") from e
    if _unavailable_code(resp) in _SCHEDULING_ERRORS:
        return resp
    if resp.status_code >= 500:
        _record_failure()
    elif path != _BATCH_PATH or not resp.is_success:
        _fails = 0
    return resp


def _breaker_open() -> bool:
    return time.monotonic() < _open_until


def _unavailable_code(response: httpx.Response) -> str | None:
    if response.status_code != 503:
        return None
    try:
        code = response.json()["error"]["code"]
        return code if isinstance(code, str) else None
    except (KeyError, ValueError, TypeError):
        return None


def _overloaded(response: httpx.Response) -> bool:
    return _unavailable_code(response) == "RAG_OVERLOADED"


def make_fairness_key(owner_scope: str | None, budget, request_id: str | None) -> str | None:
    scope = owner_scope or getattr(budget, "request_turn_id", None) or request_id
    return hashlib.sha256(str(scope).encode()).hexdigest()[:16] if scope else None


def _valid_query(query: str) -> bool:
    return bool(query.strip()) and len(query) <= MAX_QUERY_LENGTH


def _doc_types(values: list[str] | None) -> list[str] | None:
    return sorted({value.strip().lower() for value in values}) if values else None


def _cache_key(payload: dict, corpus_version: str) -> tuple:
    return (
        cache.normalize(payload["query"]), payload["top_k"], payload.get("planned", False),
        tuple(payload.get("doc_types") or ()),
        json.dumps(payload.get("exact_refs"), sort_keys=True, ensure_ascii=False),
        payload.get("expand_adjacent_clauses", True), payload.get("follow_citations", True),
        corpus_version, _RETRIEVAL_PIPELINE_VERSION, payload.get("embedding_token_budget"),
    )


def _cache_lookup(payloads: list[dict], request_id: str | None) -> list[tuple]:
    if not get_settings().rag_cache_enabled or not payloads:
        return [(None, None) for _ in payloads]
    try:
        version = cache.get_corpus_version()
        result_cache = _get_cache()
    except Exception:
        logger.warning("RAG cache unavailable request_id=%s", request_id, exc_info=True)
        return [(None, None) for _ in payloads]
    keys: list[tuple | None] = []
    valid_keys: list[tuple] = []
    for payload in payloads:
        try:
            key = _cache_key(payload, version)
            keys.append(key)
            valid_keys.append(key)
        except Exception:
            logger.warning("rag cache lỗi khi đọc, gọi rag-service như không có cache, request_id=%s", request_id, exc_info=True)
            keys.append(None)
    get_many = getattr(result_cache, "get_many", None)
    if callable(get_many) and len(valid_keys) > 1:
        try:
            hits = get_many(valid_keys)
            return [(key, hits.get(key) if key is not None else None) for key in keys]
        except Exception:
            logger.warning("rag cache lỗi khi đọc, gọi rag-service như không có cache, request_id=%s", request_id, exc_info=True)
            return [(None, None) for _ in payloads]
    entries = []
    for key in keys:
        if key is None:
            entries.append((None, None))
            continue
        try:
            entries.append((key, result_cache.get(key)))
        except Exception:
            logger.warning("rag cache lỗi khi đọc, gọi rag-service như không có cache, request_id=%s", request_id, exc_info=True)
            entries.append((None, None))
    return entries


def _degraded_usage(usage: dict | None) -> bool:
    return isinstance(usage, dict) and (
        usage.get("budget_capped") is True
        or usage.get("skipped_reason") in ("budget_zero", "budget_exhausted", "embedding_failed")
    )


def _cache_store(key, chunks, usage, status, request_id) -> None:
    if key is not None and chunks and status == "ok" and not _degraded_usage(usage):
        try:
            _get_cache().set(key, chunks)
        except Exception:
            logger.warning("rag cache lỗi khi ghi, bỏ qua, request_id=%s", request_id, exc_info=True)


def _cache_store_many(items: dict, request_id: str | None) -> None:
    if not items:
        return
    try:
        store = _get_cache()
        set_many = getattr(store, "set_many", None)
        if callable(set_many):
            set_many(items)
        else:
            for key, chunks in items.items():
                store.set(key, chunks)
    except Exception:
        logger.warning("rag cache lỗi khi ghi, bỏ qua, request_id=%s", request_id, exc_info=True)


async def _run_cache(fn, *args):
    """Chạy thao tác cache trên threadpool khi Redis đang bật để socket đồng bộ không chặn event loop."""
    s = get_settings()
    if s.rag_cache_enabled and cache._use_redis(s):
        return await asyncio.to_thread(fn, *args)
    return fn(*args)


def _result_data(response: httpx.Response) -> dict:
    if not response.is_success:
        raise RAGError(f"rag-service trả lỗi {response.status_code}")
    try:
        data = response.json()["data"]
        if not isinstance(data, dict):
            raise TypeError("Expected a RAG data object")
        return data
    except (ValueError, KeyError, TypeError) as exc:
        raise RAGError("rag-service trả kết quả không hợp lệ") from exc


def _response_usage(data: dict, request_turn_id: str) -> dict | None:
    usage = data.get("embedding_usage")
    if isinstance(usage, dict) and usage.get("request_turn_id") in (None, request_turn_id):
        return usage
    return None


async def search(
    query: str,
    top_k: int = 5,
    request_id: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    planned: bool = False,
    *,
    budget=None,
    embedding_tokens: int | None = None,
    exact_refs: list[dict] | None = None,
    expand_adjacent_clauses: bool | None = None,
    follow_citations: bool | None = None,
    doc_types: list[str] | None = None,
    fairness_key: str | None = None,
    priority: str | None = None,
    deadline: float | None = None,
) -> list[dict]:
    """Tìm căn cứ cho một truy vấn, có thể giữ chỗ và quyết toán embedding qua CaseBudget."""
    if not _valid_query(query):
        raise RAGError(f"Truy vấn phải có 1–{MAX_QUERY_LENGTH} ký tự")
    payload = {"query": query, "top_k": top_k}
    if planned:
        payload["planned"] = True
    options = {
        "doc_types": _doc_types(doc_types), "exact_refs": exact_refs or None,
        "expand_adjacent_clauses": expand_adjacent_clauses, "follow_citations": follow_citations,
    }
    payload.update({name: value for name, value in options.items() if value is not None})
    budgeted = budget is not None and budget.enabled
    amount = estimate_text_tokens(query) if embedding_tokens is None else embedding_tokens
    if budgeted:
        payload["embedding_token_budget"] = min(max(0, amount), budget.embedding_headroom())

    (key, hit), = await _run_cache(_cache_lookup, [payload], request_id)
    if hit is not None:
        logger.info("rag cache hit request_id=%s top_k=%d", request_id, top_k)
        return ChunkList(hit)
    if _breaker_open():
        raise RAGError("rag-service tạm ngắt do lỗi liên tiếp")

    # Request IDs alone are for tracing. Send scheduling fields only with scheduling context.
    if (
        priority is not None or fairness_key or deadline is not None
        or budgeted or getattr(budget, "deadline", None) is not None
    ):
        payload["priority"] = "interactive" if priority is None else priority
        key_for_turn = fairness_key or make_fairness_key(None, budget, request_id)
        if key_for_turn:
            payload["fairness_key"] = key_for_turn
        deadline_ms = int(min(90.0, _remaining(deadline, budget) - 0.1) * 1000)
        if deadline_ms < 100:
            raise RAGError("rag-service quá thời gian chờ")
        payload["deadline_ms"] = deadline_ms

    reservation = None
    if budgeted:
        request_turn_id = f"{budget.request_turn_id}:{uuid.uuid4().hex[:8]}"
        reservation = budget.reserve_embedding(payload["embedding_token_budget"], request_turn_id=request_turn_id)
        payload["request_turn_id"] = request_turn_id
        payload["embedding_token_budget"] = reservation.tokens if reservation else 0
        if key is not None:
            key = (*key[:-1], payload["embedding_token_budget"])
    usage = None
    rejected = False
    try:
        response = await _post(_SINGLE_PATH, payload, request_id, transport)
        rejected = _overloaded(response) or response.status_code in _PREPROCESS_REJECTIONS
        data = _result_data(response)
        # Legacy servers may omit the turn ID or use a different representation.
        usage = data.get("embedding_usage")
        usage = usage if isinstance(usage, dict) else None
        chunks = data.get("chunks")
        if not isinstance(chunks, list):
            raise RAGError("rag-service trả kết quả không hợp lệ")
    finally:
        if reservation is not None:
            if rejected:
                budget.release_unsent(reservation)
            else:
                budget.settle_embedding(reservation, usage)
    await _run_cache(_cache_store, key, chunks, usage, "ok", request_id)
    result = ChunkList(chunks)
    result.embedding_usage = usage
    return result


# Existing callers and tests can replace the single-search implementation.
_SINGLE_SEARCH = search


def single_scheduling(fairness_key, priority, deadline=None) -> dict:
    """Keep the legacy callable signature for replacements of single search."""
    if search is not _SINGLE_SEARCH:
        return {}
    return {"fairness_key": fairness_key, "priority": priority, "deadline": deadline}


class SearchGroups(list):
    """Kết quả theo thứ tự truy vấn, kèm trạng thái suy giảm của cả lượt."""

    def __init__(self, groups, *, degraded: bool):
        super().__init__(groups)
        self.degraded = degraded


@dataclass
class BatchMiss:
    index: int
    payload: dict
    cache_key: tuple | None


def _with_provenance(rows, issue_id, query) -> list[dict]:
    return [
        {**copy.deepcopy(row), "matched_issue_ids": [issue_id],
         "retrieval_provenance": [{"issue_id": issue_id, "query": query, "rank": rank}]}
        for rank, row in enumerate(rows, 1)
    ]


def _batch_results(data: dict, misses: list[BatchMiss], aggregate_usage: dict | None) -> dict:
    items = data.get("items")
    if not isinstance(items, list):
        raise RAGError("rag-service trả kết quả batch không hợp lệ")
    expected = {miss.payload["item_id"] for miss in misses}
    results = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("item_id"), str):
            raise RAGError("rag-service trả item không hợp lệ")
        item_id = item["item_id"]
        status = item.get("status")
        chunks = item.get("chunks", [])
        if (item_id not in expected or item_id in results or not isinstance(status, str) or status not in _BATCH_STATUSES
                or not isinstance(chunks, list) or not all(isinstance(chunk, dict) for chunk in chunks)):
            raise RAGError("rag-service trả item không hợp lệ")
        if not isinstance(item.get("embedding_usage"), dict) and _degraded_usage(aggregate_usage):
            item = {**item, "embedding_usage": aggregate_usage}
        results[item_id] = item
    return results


def _remaining(deadline: float | None, budget) -> float:
    now = time.monotonic()
    deadlines = [now + get_settings().rag_timeout_seconds]
    if deadline is not None:
        deadlines.append(deadline)
    if budget is not None and budget.deadline is not None:
        deadlines.append(budget.deadline)
    return min(deadlines) - now


async def _fetch_batch(misses, request_id, deadline, budget, transport, fairness_key):
    global _fails
    remaining = _remaining(deadline, budget)
    deadline_ms = int(min(90.0, remaining - 0.1) * 1000)
    if deadline_ms < 100 or _breaker_open():
        return {}

    turn = budget.request_turn_id if budget is not None else uuid.uuid4().hex
    request_turn_id = f"{turn}:{uuid.uuid4().hex[:8]}"
    budgeted = budget is not None and budget.enabled
    reservation = None
    if budgeted:
        total = sum(miss.payload["embedding_token_budget"] for miss in misses)
        reservation = budget.reserve_embedding(total, request_turn_id=request_turn_id)
        granted = reservation.tokens if reservation else 0
        if granted < total:
            share = granted // len(misses)
            for miss in misses:
                cap = min(share, miss.payload["embedding_token_budget"])
                miss.payload["embedding_token_budget"] = cap
                if miss.cache_key is not None:
                    miss.cache_key = (*miss.cache_key[:-1], cap)
    payload = {
        "items": [miss.payload for miss in misses], "request_turn_id": request_turn_id, "deadline_ms": deadline_ms,
        "priority": "batch",
    }
    if fairness_key:
        payload["fairness_key"] = fairness_key
    usage = None
    rejected = False
    try:
        async with asyncio.timeout(remaining):
            response = await _post(_BATCH_PATH, payload, request_id, transport)
        rejected = _overloaded(response) or response.status_code in _PREPROCESS_REJECTIONS
        data = _result_data(response)
        usage = _response_usage(data, request_turn_id)
        results = _batch_results(data, misses, usage)
    except TimeoutError:
        _record_failure()
        return {}
    except RAGError:
        return {}
    finally:
        if reservation is not None:
            if rejected:
                budget.release_unsent(reservation)
            else:
                budget.settle_embedding(reservation, usage)

    if any(item.get("error_code") in ("RAG_DATABASE_UNAVAILABLE", "EMBEDDING_SERVICE_UNAVAILABLE") for item in results.values()):
        _record_failure()
    elif any(item["status"] == "ok" for item in results.values()):
        _fails = 0
    return results


def _group_rows(rows, query, with_provenance):
    if with_provenance:
        return _with_provenance(rows, *query[:2])
    # Batch serialization adds nulls for optional fields omitted by single search.
    optional = {"retrieval_sources", "rank_sources", "rrf_rank", "document_version", "effective_date"}
    return [{k: v for k, v in row.items() if v is not None or k not in optional} for row in rows]


async def _search_many_batch(
    queries, payloads, request_id, deadline, budget, transport, fairness_key, with_provenance,
) -> SearchGroups:
    groups = [[] for _ in queries]
    misses = []
    valid = [(i, payload) for i, payload in enumerate(payloads) if _valid_query(payload["query"])]
    degraded = len(valid) != len(queries)
    succeeded = False
    entries = await _run_cache(_cache_lookup, [payload for _, payload in valid], request_id)
    for (index, payload), (key, hit) in zip(valid, entries):
        if hit is None:
            misses.append(BatchMiss(index, payload, key))
        else:
            succeeded = True
            groups[index] = _group_rows(hit, queries[index], with_provenance)
    if misses:
        results = await _fetch_batch(misses, request_id, deadline, budget, transport, fairness_key)
        to_cache: dict = {}
        for miss in misses:
            item = results.get(miss.payload["item_id"])
            if item is None:
                degraded = True
                continue
            status, chunks, usage = item["status"], item.get("chunks", []), item.get("embedding_usage")
            degraded |= status != "ok" or _degraded_usage(usage)
            succeeded |= status in ("ok", "degraded") and (
                bool(chunks) or item.get("error_code") != "EMBEDDING_SERVICE_UNAVAILABLE"
            )
            group = _group_rows(chunks, queries[miss.index], with_provenance)
            cache_val = chunks if with_provenance else group
            if miss.cache_key is not None and cache_val and status == "ok" and not _degraded_usage(usage):
                to_cache[miss.cache_key] = cache_val
            groups[miss.index] = group
        if to_cache:
            await _run_cache(_cache_store_many, to_cache, request_id)
    if not with_provenance and not succeeded:
        raise RAGError("Không truy xuất được căn cứ rà soát")
    return SearchGroups(groups, degraded=degraded)


async def search_many(
    queries: list[tuple], top_k: int = 12, request_id: str | None = None,
    deadline: float | None = None,
    *, budget=None, exact_refs: list[dict] | None = None,
    expand_adjacent_clauses: bool | None = None, follow_citations: bool | None = None,
    doc_types: list[str] | None = None, transport: httpx.AsyncBaseTransport | None = None,
    fairness_key: str | None = None, priority: str = "batch",
    planned: bool = True, with_provenance: bool = True,
) -> SearchGroups:
    """Tìm lần lượt theo các nhóm (issue_id, query[, exact_refs]) và giữ provenance."""
    if not queries:
        return SearchGroups([], degraded=False)
    settings = get_settings()
    share = budget.embedding_headroom() // len(queries) if budget is not None and budget.enabled else None
    doc_types = _doc_types(doc_types)
    key_for_turn = fairness_key or make_fairness_key(None, budget, request_id)
    if settings.rag_batch_enabled and search is _SINGLE_SEARCH and len(queries) <= 12 and priority == "batch":
        payloads = []
        for index, (_, query, *refs) in enumerate(queries):
            payload = {
                "item_id": str(index + 1), "query": query, "top_k": top_k, "planned": planned,
                "doc_types": doc_types, "exact_refs": (refs[0] if refs else exact_refs) or None,
                "expand_adjacent_clauses": True if expand_adjacent_clauses is None else expand_adjacent_clauses,
                "follow_citations": True if follow_citations is None else follow_citations,
            }
            if share is not None:
                payload["embedding_token_budget"] = min(share, estimate_text_tokens(query))
            payloads.append(payload)
        return await _search_many_batch(
            queries, payloads, request_id, deadline, budget, transport, key_for_turn, with_provenance,
        )

    semaphore = asyncio.Semaphore(settings.case_retrieval_concurrency)
    options = {
        "expand_adjacent_clauses": expand_adjacent_clauses, "follow_citations": follow_citations,
        **({"doc_types": doc_types} if doc_types else {}), **({"transport": transport} if transport else {}),
    }

    async def one(issue_id, query, *refs):
        async with semaphore:
            rows = await search(
                query, top_k=top_k, request_id=request_id, planned=planned, exact_refs=refs[0] if refs else exact_refs,
                **options, **single_scheduling(key_for_turn, priority, deadline),
                **({"budget": budget, "embedding_tokens": min(share, estimate_text_tokens(query))}
                              if share is not None else {}),
            )
        return _group_rows(rows, (issue_id, query), with_provenance), _degraded_usage(getattr(rows, "embedding_usage", None))

    tasks = [asyncio.create_task(one(*entry)) for entry in queries]
    remaining = max(0.0, deadline - time.monotonic()) if deadline is not None else None
    try:
        await asyncio.wait(tasks, timeout=remaining)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
    if all(isinstance(result, BaseException) for result in results):
        raise RAGError("Không truy xuất được căn cứ cho các vấn đề") from results[0]
    groups = [result[0] if isinstance(result, tuple) else [] for result in results]
    degraded = any(not isinstance(result, tuple) or result[1] for result in results)
    return SearchGroups(groups, degraded=degraded)
