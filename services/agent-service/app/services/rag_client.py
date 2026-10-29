# HTTP client gọi rag-service qua internal token
# Hợp đồng: services/rag-service/openapi.yaml, POST /internal/v1/rag/search
import logging
import time

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

MAX_QUERY_LENGTH = 1000  # rag-service chỉ nhận query 1–1000 ký tự

# Circuit breaker: lỗi liên tiếp thì ngừng gọi rag-service một lúc, ném đúng RAGError như khi rag lỗi.
# shortcut: đếm theo từng process, không khoá; đủ cho 1 worker, khi chạy nhiều replica thì cân nhắc thư viện.
_FAIL_LIMIT = 3
_OPEN_SECONDS = 30
_fails = 0
_open_until = 0.0


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
    resp = await _post(
        "/internal/v1/rag/search", {"query": query[:MAX_QUERY_LENGTH], "top_k": top_k}, request_id, transport
    )
    try:
        resp.raise_for_status()
        chunks = resp.json()["data"]["chunks"]
    except httpx.HTTPStatusError as e:
        raise RAGError(f"rag-service trả lỗi {e.response.status_code}") from e
    except (ValueError, KeyError, TypeError) as e:
        raise RAGError("rag-service trả kết quả không hợp lệ") from e

    if not isinstance(chunks, list):
        raise RAGError("rag-service trả kết quả không hợp lệ")
    return chunks
