"""Gọi Document Service (DOCUMENT_SERVICE_URL) để kiểm tra giấy tờ FE vừa tải lên đúng là của người gửi."""

import uuid

import httpx

from app.core.responses import CREDENTIAL_NOT_FOUND, ApiError, invalid
from app.services.http_client import UpstreamError, bad_response, call

SERVICE = "DOCUMENT"


def ensure_owned(document_ids: list[uuid.UUID], access_token: str) -> None:
    """Đọc từng tài liệu bằng chính token của người gửi: Document Service chỉ trả tài liệu của chủ sở hữu."""
    for document_id in document_ids:
        try:
            data = call(SERVICE, "GET", f"/api/v1/documents/{document_id}", bearer=access_token)
        except UpstreamError as e:
            if e.status in (403, 404):
                raise invalid("credentials", "Giấy tờ không tồn tại hoặc không thuộc về bạn.") from None
            raise bad_response(SERVICE) from None
        if not isinstance(data, dict) or str(data.get("document_id")) != str(document_id):
            raise bad_response(SERVICE)


def get_content(owner_user_id: uuid.UUID, document_id: uuid.UUID) -> httpx.Response:
    """File giấy tờ (bytes + Content-Type) cho admin xem. 404 → None; 409 (chưa quét xong / bị chặn) trả nguyên cho FE."""
    try:
        return call(SERVICE, "GET", f"/internal/v1/documents/{document_id}/content?owner_user_id={owner_user_id}",
                    raw=True)
    except UpstreamError as e:
        if e.status == 404:
            raise ApiError(404, CREDENTIAL_NOT_FOUND) from None
        if e.status == 409:
            raise ApiError(409, e.code, e.message) from None
        raise bad_response(SERVICE) from None
