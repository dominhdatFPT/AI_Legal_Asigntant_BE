"""Adapter for the supplied EkycService.java contract, NOT an official VNPT spec.

No mock/fallback, no automatic retries of billable requests, no PII logging.
"""
import base64
from dataclasses import dataclass, field
from datetime import datetime
import json
import math
import os
import re
import time

import httpx

from app.core.config import ConfigurationError
from app.core.errors import DocumentError

MAX_PROVIDER_JSON = 1024 * 1024


@dataclass(frozen=True)
class VnptSettings:
    token_id: str = field(repr=False)
    token_key: str = field(repr=False)
    base_url: str = "https://api.idg.vnpt.vn"

    @classmethod
    def from_env(cls):
        if os.getenv("EKYC_PROVIDER", "vnpt").lower() != "vnpt":
            raise ConfigurationError("Document chỉ hỗ trợ provider VNPT cho luồng CCCD này.")
        if os.getenv("EKYC_BASE_URL", "").rstrip("/") != "https://api.idg.vnpt.vn":
            raise ConfigurationError("EKYC_BASE_URL phải là https://api.idg.vnpt.vn.")
        values = [os.getenv(name, "").strip() for name in ("EKYC_TOKEN_ID", "EKYC_TOKEN")]
        if any(not value or not value.isascii() or any(ch.isspace() or ord(ch) < 33 or ord(ch) > 126
                                                       for ch in value) for value in values):
            raise ConfigurationError("Thiếu hoặc sai EKYC_TOKEN_ID/EKYC_TOKEN.")
        # This flow does OCR only. A boolean must never imply genuine verification.
        enabled = os.getenv("EKYC_VALIDATION_ENABLED", "false").lower()
        if enabled not in {"false", "0"}:
            raise ConfigurationError("Task 4 chỉ OCR; giữ EKYC_VALIDATION_ENABLED=false, chưa hỗ trợ xác minh.")
        return cls(*values)


class Vnpt:
    def __init__(self, settings, *, transport=None):
        self.settings, self.transport = settings, transport

    def read_image(self, image, *, deadline, on_response):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DocumentError("EKYC_TIMEOUT", "VNPT quá thời gian phản hồi.", 504)
        try:
            with httpx.Client(transport=self.transport, timeout=min(20, remaining),
                              follow_redirects=False, trust_env=False) as client:
                with client.stream("POST", self.settings.base_url + "/v1/ocr/id-card",
                                   headers={"Token": self.settings.token_key, "TokenId": self.settings.token_id,
                                            "Accept": "application/json", "Accept-Encoding": "identity"},
                                   json={"img": base64.b64encode(image.content).decode("ascii")}) as response:
                    if response.status_code in {401, 403}:
                        raise DocumentError("EKYC_CREDENTIALS_REJECTED", "VNPT không chấp nhận thông tin truy cập.", 503)
                    if response.status_code in {404, 405}:
                        raise DocumentError("EKYC_ENDPOINT_UNSUPPORTED", "Endpoint từ code mẫu chưa được VNPT chấp nhận.", 503,
                                            {"provider_http_status": response.status_code})
                    if response.status_code == 429:
                        raise DocumentError("EKYC_QUOTA_EXCEEDED", "VNPT đang giới hạn lượt gọi.", 503)
                    if response.status_code != 200:
                        raise DocumentError("EKYC_BAD_RESPONSE", "VNPT phản hồi không hợp lệ.", 502)
                    if response.headers.get("Content-Type", "").split(";", 1)[0].lower() != "application/json":
                        raise DocumentError("EKYC_BAD_RESPONSE", "VNPT không trả JSON hợp lệ.", 502)
                    content = bytearray()
                    for chunk in response.iter_bytes(chunk_size=65536):
                        if time.monotonic() > deadline:
                            raise DocumentError("EKYC_TIMEOUT", "VNPT quá thời gian phản hồi.", 504)
                        if len(content) + len(chunk) > MAX_PROVIDER_JSON:
                            raise DocumentError("EKYC_BAD_RESPONSE", "JSON VNPT vượt giới hạn.", 502)
                        content.extend(chunk)
                    raw = json.loads(content, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(raw, dict):
                raise ValueError
            on_response(raw)  # Persist original JSON before normalization/business validation.
            return normalize(raw)
        except httpx.TimeoutException:
            raise DocumentError("EKYC_TIMEOUT", "VNPT quá thời gian phản hồi.", 504) from None
        except httpx.HTTPError:
            raise DocumentError("EKYC_UNAVAILABLE", "Không thể truy cập VNPT.", 503) from None
        except (ValueError, TypeError, RecursionError):
            raise DocumentError("EKYC_BAD_RESPONSE", "VNPT trả dữ liệu không đúng code mẫu.", 502) from None


def normalize(raw):
    if (raw.get("error") not in (None, False, "") or raw.get("is_valid") is False or raw.get("is_fake") is True
            or raw.get("success") is False):
        raise ValueError
    mapping = {"full_name": "full_name", "identity_number": "id", "date_of_birth": "date_of_birth",
               "issue_date": "issue_date", "issue_place": "issue_place", "permanent_address": "place_of_residence"}
    if not any(raw.get(key) not in {None, ""} for key in mapping.values() if isinstance(raw.get(key), (str, type(None)))):
        raise ValueError
    result = {}
    for target, source in mapping.items():
        value = raw.get(source)
        limit = {"full_name": 200, "issue_place": 500, "permanent_address": 2000}.get(target, 32)
        if value is not None and (not isinstance(value, str) or len(value) > limit
                                  or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
            raise ValueError
        value = value.strip() if value else None
        if value and target == "identity_number" and not re.fullmatch(r"[0-9]{12}", value):
            raise ValueError
        if value and target in {"date_of_birth", "issue_date"}:
            parsed = None
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                try:
                    parsed = datetime.strptime(value, fmt).date().isoformat()
                    break
                except ValueError:
                    continue
            if parsed is None:
                raise ValueError
            value = parsed
        result[target] = value
    confidence = raw.get("confidence")
    if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (float, int))
                                   or not math.isfinite(confidence) or not 0 <= confidence <= 100):
        raise ValueError
    # Never turn confidence/is_valid/is_fake into verified identity.
    return result


def merge_fields(front, back):
    result, warnings = {}, []
    for key in front:
        if front[key] and back[key] and front[key] != back[key]:
            result[key] = None
            warnings.append("CONFLICT_" + key.upper())
        else:
            result[key] = front[key] or back[key]
        if not result[key]:
            warnings.append("REVIEW_" + key.upper())
    return result, warnings
