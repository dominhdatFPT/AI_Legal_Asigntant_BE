# HTTP client gọi rag-service qua internal token
# Hợp đồng: services/rag-service/openapi.yaml, POST /internal/v1/rag/search
import httpx

from app.core.config import get_settings

MAX_QUERY_LENGTH = 1000  # rag-service chỉ nhận query 1–1000 ký tự


class RAGError(Exception):
    pass


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
    settings = get_settings()
    headers = {"X-Internal-Token": settings.internal_token}
    if request_id:
        headers["X-Request-ID"] = request_id

    try:
        async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
            resp = await client.post(
                f"{settings.rag_service_url.rstrip('/')}/internal/v1/rag/search",
                json={"query": query[:MAX_QUERY_LENGTH], "top_k": top_k},
                headers=headers,
            )
            resp.raise_for_status()
            chunks = resp.json()["data"]["chunks"]
    except httpx.TimeoutException as e:
        raise RAGError("rag-service quá thời gian chờ") from e
    except httpx.RequestError as e:
        raise RAGError("Không kết nối được rag-service") from e
    except httpx.HTTPStatusError as e:
        raise RAGError(f"rag-service trả lỗi {e.response.status_code}") from e
    except (ValueError, KeyError, TypeError) as e:
        raise RAGError("rag-service trả kết quả không hợp lệ") from e

    if not isinstance(chunks, list):
        raise RAGError("rag-service trả kết quả không hợp lệ")
    return chunks
