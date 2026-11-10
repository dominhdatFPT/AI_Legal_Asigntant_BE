"""Gọi Lawyer Service (LAWYER_SERVICE_URL). Contract: services/lawyer-service/openapi.yaml, tag Internal."""

import uuid

from app.services.http_client import UpstreamError, bad_response, call

SERVICE = "LAWYER"


def can_practice(user_id: uuid.UUID) -> bool:
    """GET /internal/v1/lawyers/{user_id}/status → can_practice. Chưa có hồ sơ (404) thì không được hành nghề."""
    try:
        data = call(SERVICE, "GET", f"/internal/v1/lawyers/{user_id}/status")
    except UpstreamError as e:
        if e.status == 404:
            return False
        raise bad_response(SERVICE) from None
    if not isinstance(data, dict) or not isinstance(data.get("can_practice"), bool):
        raise bad_response(SERVICE)
    return data["can_practice"]
