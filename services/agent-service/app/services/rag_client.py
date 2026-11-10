# HTTP client gọi rag-service qua internal token
# Hợp đồng: services/rag-service/openapi.yaml, POST /internal/v1/rag/search
import logging
import time

import httpx

from app.core.config import get_settings
from app.services import cache

logger = logging.getLogger(__name__)

MAX_QUERY_LENGTH = 1000  # rag-service chỉ nhận query 1–1000 ký tự

# Circuit breaker: lỗi liên tiếp thì ngừng gọi rag-service một lúc, ném đúng RAGError như khi rag lỗi.
# shortcut: đếm theo từng process, không khoá; đủ cho 1 worker, khi chạy nhiều replica thì cân nhắc thư viện.
_FAIL_LIMIT = 3
_OPEN_SECONDS = 30
_fails = 0
_open_until = 0.0


# Cache kết quả tìm luật (RAG_CACHE_ENABLED, mặc định tắt): cùng câu tìm + top_k luôn ra cùng đoạn luật, trừ khi nạp luật mới.
# shortcut: cache theo từng process, mất khi restart; nạp luật mới xong phải restart agent-service để xoá cache.
_cache: cache.TTLCache | None = None


def _get_cache() -> cache.TTLCache:
    """Tạo lần đầu cần dùng, theo cấu hình lúc đó."""
    global _cache
    if _cache is None:
        s = get_settings()
        _cache = cache.TTLCache(s.rag_cache_max_items, s.rag_cache_ttl_seconds)
    return _cache


def clear_cache() -> None:
    """Xoá cache; lần dùng sau tạo lại theo cấu hình (cho test)."""
    global _cache
    _cache = None


class RAGError(Exception):
    pass


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
    try:
        async with httpx.AsyncClient(timeout=settings.rag_timeout_seconds, transport=transport) as client:
            resp = await client.post(f"{settings.rag_service_url.rstrip('/')}{path}", json=payload, headers=headers)
    except httpx.TimeoutException as e:
        _record_failure()
        raise RAGError("rag-service quá thời gian chờ") from e
    except httpx.RequestError as e:
        _record_failure()
        raise RAGError("Không kết nối được rag-service") from e
    if resp.status_code >= 500:
        _record_failure()
    else:
        _fails = 0
    return resp


async def search(
    query: str,
    top_k: int = 5,
    request_id: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,  # test truyền MockTransport
) -> list[dict]:
    """
    Trả về danh sách chunks, mỗi chunk dạng:
    {"id", "content", "score", "section_info": {"chuong", "dieu", "khoan", "diem"},
     "document": {"id", "title", "doc_number", "doc_type"}}
    """
    query = query[:MAX_QUERY_LENGTH]
    key = None
    if get_settings().rag_cache_enabled:
        # Trúng cache thì trả luôn, không gọi rag-service (kể cả khi circuit breaker đang ngắt). Lỗi cache thì gọi rag như không có cache
        try:
            key = (cache.normalize(query), top_k)
            hit = _get_cache().get(key)
        except Exception:  # noqa: BLE001 - cache là phần phụ, không được làm hỏng việc tìm luật
            logger.warning("rag cache lỗi khi đọc, gọi rag-service như không có cache, request_id=%s", request_id, exc_info=True)
            key, hit = None, None
        if hit is not None:
            logger.info("rag cache hit request_id=%s top_k=%d", request_id, top_k)
            return hit

    resp = await _post("/internal/v1/rag/search", {"query": query, "top_k": top_k}, request_id, transport)
    try:
        resp.raise_for_status()
        chunks = resp.json()["data"]["chunks"]
    except httpx.HTTPStatusError as e:
        raise RAGError(f"rag-service trả lỗi {e.response.status_code}") from e
    except (ValueError, KeyError, TypeError) as e:
        raise RAGError("rag-service trả kết quả không hợp lệ") from e

    if not isinstance(chunks, list):
        raise RAGError("rag-service trả kết quả không hợp lệ")
    # Chỉ lưu danh sách hợp lệ và không rỗng (rỗng thường là kho chưa có dữ liệu, không nên giữ tới khi nạp luật rồi restart)
    if key is not None and chunks:
        try:
            _get_cache().set(key, chunks)
        except Exception:  # noqa: BLE001
            logger.warning("rag cache lỗi khi ghi, bỏ qua, request_id=%s", request_id, exc_info=True)
    return chunks
