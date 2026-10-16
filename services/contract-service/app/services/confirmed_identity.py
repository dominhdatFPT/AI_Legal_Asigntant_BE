"""Read confirmed profile only; no OCR, User-profile fallback, persistence or retry."""
import json
import os
import re

import httpx
from pydantic import ValidationError

from app.core.config import service_base
from app.core.errors import ContractError
from app.schemas.identity_suggestions import ConfirmedProfile
from app.services.user_gate import unique_object


def bad_response():
    return ContractError("DOCUMENT_BAD_RESPONSE", "Phản hồi hồ sơ CCCD không hợp lệ; bạn vẫn có thể nhập tay.", 502)


def unavailable():
    return ContractError("DOCUMENT_SERVICE_UNAVAILABLE", "Chưa thể lấy hồ sơ CCCD; vui lòng thử lại hoặc nhập tay.", 503)


class ConfirmedIdentity:
    def __init__(self, base, *, transport=None):
        self.base, self.transport = base, transport

    @classmethod
    def from_env(cls):
        return cls(os.getenv("DOCUMENT_SERVICE_URL", ""))

    def read(self, owner, token, request_id):
        try:
            base = service_base(self.base, "document-service")
        except ContractError:
            raise unavailable() from None
        try:
            with httpx.Client(timeout=5, follow_redirects=False, trust_env=False, transport=self.transport) as client:
                with client.stream("GET", base + "/api/v1/documents/identity-profile/me", headers={
                    "Authorization":"Bearer " + token, "X-Request-ID":request_id,
                }) as response:
                    if response.status_code >= 500:
                        raise unavailable()
                    if response.status_code == 401:
                        raise ContractError("UNAUTHORIZED", "Access token không còn hợp lệ.", 401)
                    if response.status_code not in {200,404}:
                        raise bad_response()
                    raw = bytearray()
                    for chunk in response.iter_bytes(chunk_size=16384):
                        raw.extend(chunk)
                        if len(raw) > 16384:
                            raise bad_response()
                    body = json.loads(raw, object_pairs_hook=unique_object,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                    if not isinstance(body, dict) or set(body) != {"data","error","request_id"} or not isinstance(body["request_id"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", body["request_id"]):
                        raise bad_response()
                    if response.status_code == 404:
                        error = body["error"]
                        if (body["data"] is not None or not isinstance(error, dict)
                            or set(error) - {"code","message","details","current_revision"}
                            or error.get("code") != "IDENTITY_PROFILE_NOT_FOUND"
                            or not isinstance(error.get("message"), str) or not error["message"].strip()
                            or len(error["message"]) > 2000
                            or ("current_revision" in error and (type(error["current_revision"]) is not int or not 0 <= error["current_revision"] <= 2147483647))
                            or ("details" in error and not isinstance(error["details"], list))):
                            raise bad_response()
                        return None
                    if body["error"] is not None:
                        raise bad_response()
                    profile = ConfirmedProfile.model_validate(body["data"])
                    if str(profile.owner_user_id) != owner:
                        raise bad_response()
                    return profile
        except httpx.TimeoutException:
            raise ContractError("DOCUMENT_TIMEOUT", "Lấy hồ sơ CCCD quá thời gian; vui lòng thử lại hoặc nhập tay.", 504) from None
        except httpx.HTTPError:
            raise unavailable() from None
        except (ValueError, TypeError, RecursionError, ValidationError):
            # Do not echo Pydantic input, source IDs, private fields or upstream bodies.
            raise bad_response() from None
