# HTTP client lấy chữ đã trích của tài liệu người dùng từ document-service
# Hợp đồng: services/document-service/openapi.yaml, GET /documents/{id}/extraction
import asyncio
import uuid

import httpx

from app.core.config import get_settings

NOT_READY_MESSAGE = "Tài liệu đang xử lý, vui lòng thử lại sau."
UNREADABLE_MESSAGE = "Không đọc được nội dung tài liệu hoặc tài liệu không đạt kiểm tra an toàn, vui lòng tải tài liệu khác."
# Hai mã này nghĩa là thử lại cũng không bao giờ xong, khác với đang xử lý
_UNREADABLE_CODES = {"EXTRACTION_FAILED", "FILE_REJECTED"}


class DocumentNotFound(Exception):
    """Không có tài liệu, hoặc không phải của người dùng: gộp chung để không lộ file của người khác có tồn tại."""


class DocumentNotReady(Exception):
    """Tài liệu chưa trích chữ xong hoặc không đọc được; str(e) là câu báo cho người dùng."""


class DocumentServiceError(Exception):
    pass


async def get_extraction(
    document_id: uuid.UUID,
    token: str,
    request_id: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,  # test truyền MockTransport
) -> dict:
    """Trả về {"document_id", "source_kind", "text"}. Token của chính người dùng được chuyển tiếp nguyên vẹn
    để document-service tự kiểm tra quyền chủ file (agent-service không dùng token nội bộ ở đây)."""
    url = f"{get_settings().document_service_url.rstrip('/')}/api/v1/documents/{document_id}/extraction"
    headers = {"Authorization": f"Bearer {token}"}
    if request_id:
        headers["X-Request-ID"] = request_id
    try:
        async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
            resp = await client.get(url, headers=headers)
    except httpx.TimeoutException as e:
        raise DocumentServiceError("document-service quá thời gian chờ") from e
    except httpx.RequestError as e:
        raise DocumentServiceError("Không kết nối được document-service") from e

    if resp.status_code in (401, 403, 404):
        raise DocumentNotFound(str(document_id))
    if resp.status_code == 409:
        try:
            code = resp.json()["error"]["code"]
        except (ValueError, KeyError, TypeError):
            code = None
        raise DocumentNotReady(UNREADABLE_MESSAGE if code in _UNREADABLE_CODES else NOT_READY_MESSAGE)
    if resp.status_code != 200:
        raise DocumentServiceError(f"document-service trả lỗi {resp.status_code}")
    try:
        data = resp.json()["data"]
        text, source_kind = data["text"], data["source_kind"]
    except (ValueError, KeyError, TypeError) as e:
        raise DocumentServiceError("document-service trả kết quả không hợp lệ") from e
    if not isinstance(text, str) or not isinstance(source_kind, str):
        raise DocumentServiceError("document-service trả kết quả không hợp lệ")
    return {"document_id": str(document_id), "source_kind": source_kind, "text": text}


async def get_extractions(
    document_ids: list[uuid.UUID],
    token: str,
    request_id: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[dict]:
    """Lấy song song nhiều tài liệu, giữ nguyên thứ tự document_ids. Một tài liệu lỗi thì cả lượt lỗi."""
    return list(await asyncio.gather(*(get_extraction(i, token, request_id, transport) for i in document_ids)))
