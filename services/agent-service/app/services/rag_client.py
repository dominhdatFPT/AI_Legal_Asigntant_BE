# HTTP client gọi rag-service qua internal token
# Hợp đồng: services/rag-service/openapi.yaml, POST /internal/v1/rag/search
import httpx

from app.core.config import get_settings

MAX_QUERY_LENGTH = 1000  # rag-service chỉ nhận query 1–1000 ký tự


class RAGError(Exception):
    pass


class ArticleNotFound(Exception):
    """rag-service trả 404/409 khi tra cứu điều luật: không có văn bản / Điều / Khoản, hoặc tên văn bản mơ hồ."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


async def _post(path: str, payload: dict, request_id: str | None, transport) -> httpx.Response:
    """POST tới rag-service kèm internal token. Lỗi mạng đổi thành RAGError; mã HTTP để nơi gọi tự xử lý."""
    settings = get_settings()
    headers = {"X-Internal-Token": settings.internal_token}
    if request_id:
        headers["X-Request-ID"] = request_id
    try:
        async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
            return await client.post(f"{settings.rag_service_url.rstrip('/')}{path}", json=payload, headers=headers)
    except httpx.TimeoutException as e:
        raise RAGError("rag-service quá thời gian chờ") from e
    except httpx.RequestError as e:
        raise RAGError("Không kết nối được rag-service") from e


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


async def get_article(
    law: str,
    dieu: str,
    khoan: str | None = None,
    request_id: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """Nguyên văn 1 Điều/Khoản (POST /internal/v1/rag/articles). Không có thì ném ArticleNotFound, lỗi khác RAGError.
    Trả về {"document": {...}, "chuong", "dieu", "khoan", "content", "note"}."""
    resp = await _post("/internal/v1/rag/articles", {"law": law[:200], "dieu": dieu, "khoan": khoan}, request_id, transport)
    try:
        if resp.status_code in (404, 409):
            error = resp.json().get("error") or {}
            raise ArticleNotFound(error.get("code", ""), error.get("message", "Không tìm thấy điều luật."))
        resp.raise_for_status()
        article = resp.json()["data"]
    except httpx.HTTPStatusError as e:
        raise RAGError(f"rag-service trả lỗi {e.response.status_code}") from e
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise RAGError("rag-service trả kết quả không hợp lệ") from e
    if not isinstance(article, dict) or "content" not in article:
        raise RAGError("rag-service trả kết quả không hợp lệ")
    return article
