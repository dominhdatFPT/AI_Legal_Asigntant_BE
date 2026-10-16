"""Gọi Document Service nội bộ (DOCUMENT_SERVICE_URL): ảnh thẻ luật sư + JSON OCR, link xem ảnh."""

import uuid

from app.services.http_client import UpstreamError, bad_response, call

SERVICE = "DOCUMENT"
OCR_KEYS = ("status", "fields", "warnings", "error_code", "engine")


def get_lawyer_card(card_id: uuid.UUID) -> dict | None:
    """{card_id, owner_user_id, ocr_result}; None khi không tồn tại."""
    try:
        data = call(SERVICE, "GET", f"/internal/v1/documents/lawyer-cards/{card_id}")
    except UpstreamError as e:
        if e.status == 404:
            return None
        raise bad_response(SERVICE) from None
    if not isinstance(data, dict) or not isinstance(data.get("ocr_result"), dict):
        raise bad_response(SERVICE)
    ocr = data["ocr_result"]
    if any(k not in ocr for k in OCR_KEYS) or ocr["status"] not in ("completed", "failed"):
        raise bad_response(SERVICE)
    try:
        data["owner_user_id"] = uuid.UUID(str(data.get("owner_user_id")))
    except ValueError:
        raise bad_response(SERVICE) from None
    return data


def get_view_url(card_id: uuid.UUID) -> dict | None:
    """{url, expires_in}; None khi ảnh không còn."""
    try:
        data = call(SERVICE, "POST", f"/internal/v1/documents/lawyer-cards/{card_id}/view-url")
    except UpstreamError as e:
        if e.status == 404:
            return None
        raise bad_response(SERVICE) from None
    if not isinstance(data, dict) or not isinstance(data.get("url"), str) or not isinstance(data.get("expires_in"), int):
        raise bad_response(SERVICE)
    return {"url": data["url"], "expires_in": data["expires_in"]}
