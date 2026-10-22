"""Đọc CCCD bằng VNPT eKYC trước; VNPT lỗi (token hết hạn, hết lượt, mạng) hoặc đọc không đủ thì dùng OCR local.

Chỉ bật khi EKYC_ENABLED=1. Mỗi mặt gọi VNPT tối đa một lần, không retry (VNPT tính phí theo lượt),
không log PII. Đây chỉ là OCR: kết quả VNPT không phải xác minh danh tính.
Luồng API (đã thử thật 22/10/2026): POST /file-service/v1/addFile -> object.hash, rồi POST /ai/v1/ocr/id/{front|back}.
"""
from dataclasses import dataclass, field
from datetime import datetime
import logging
import os
import re
import time
from uuid import uuid4

import httpx

from app.core.errors import DocumentError
from app.services.identity_card_match import mrz_candidates
from app.services.identity_parser import folded

logger = logging.getLogger("document-service")
BASE_URL = "https://api.idg.vnpt.vn"
MAX_PROVIDER_JSON = 1024 * 1024
# Hạn 55s cho cả phiên: VNPT tối đa 12s mỗi mặt (thường ~2s); OCR local mặt trước chừa 10s cho mặt sau.
VNPT_BUDGET_SECONDS = 12
LOCAL_RESERVE_SECONDS = {"front": 10, "back": 0}


@dataclass(frozen=True)
class EkycSettings:
    access_token: str = field(repr=False)
    token_id: str = field(repr=False)
    token_key: str = field(repr=False)

    @classmethod
    def from_env(cls):
        """None khi chưa bật hoặc thiếu token: khi đó chỉ chạy OCR local như cũ."""
        if os.getenv("EKYC_ENABLED", "0") != "1":
            return None
        values = [os.getenv(name, "").strip() for name in ("EKYC_ACCESS_TOKEN", "EKYC_TOKEN_ID", "EKYC_TOKEN")]
        values[0] = re.sub(r"(?i)^bearer\s+", "", values[0])
        if any(not value or not value.isascii() or any(ch.isspace() or not 33 <= ord(ch) <= 126 for ch in value)
               for value in values):
            logger.warning("EKYC_ENABLED=1 nhưng thiếu/sai EKYC_ACCESS_TOKEN, EKYC_TOKEN_ID, EKYC_TOKEN")
            return None
        return cls(*values)


def _side_ok(side, fields, raw):
    if side == "front":
        return bool(fields.get("identity_number") and fields.get("full_name"))
    # Mặt sau cần MRZ để đối chiếu hai mặt (ensure_card_pair).
    return bool(mrz_candidates(raw or {}))


class EkycFirstOcr:
    def __init__(self, local, settings, *, transport=None):
        self.local, self.settings, self.transport = local, settings, transport

    @property
    def request_namespace(self):
        return self.local.request_namespace + "-vnpt-first-v1"

    def read_image(self, image, *, deadline, on_response):
        record = None
        try:
            record = self._vnpt(image, min(deadline, time.monotonic() + VNPT_BUDGET_SECONDS))
            if _side_ok(image.side, record["fields"], record):
                on_response(record)
                return dict(record["fields"])
        except DocumentError as exc:
            logger.warning("VNPT eKYC lỗi side=%s code=%s, chuyển sang OCR local", image.side, exc.code)
        captured = {}
        try:
            fields = self.local.read_image(image, deadline=deadline - LOCAL_RESERVE_SECONDS[image.side],
                                           on_response=lambda raw: captured.update(raw=raw))
        except DocumentError:
            if record is None:
                raise
            fields = None
        # Cả hai đều chưa đủ: VNPT đọc được gì thì giữ VNPT (thường chính xác hơn OCR local).
        if record is not None and (fields is None or not _side_ok(image.side, fields, captured.get("raw"))):
            on_response(record)
            return dict(record["fields"])
        on_response(captured["raw"])
        return fields

    def _vnpt(self, image, deadline):
        headers = {"Authorization": "bearer " + self.settings.access_token, "Token-id": self.settings.token_id,
                   "Token-key": self.settings.token_key, "mac-address": "LEGAL-AI-BE", "Accept": "application/json"}
        with httpx.Client(base_url=BASE_URL, headers=headers, transport=self.transport,
                          follow_redirects=False, trust_env=False) as client:
            def call(path, **kwargs):
                remaining = deadline - time.monotonic()
                if remaining < 2:
                    raise DocumentError("EKYC_TIMEOUT", "VNPT quá thời gian phản hồi.", 504)
                try:
                    return _object(client.post(path, timeout=min(15, remaining), **kwargs))
                except httpx.TimeoutException:
                    raise DocumentError("EKYC_TIMEOUT", "VNPT quá thời gian phản hồi.", 504) from None
                except httpx.HTTPError:
                    raise DocumentError("EKYC_UNAVAILABLE", "Không thể truy cập VNPT.", 503) from None
            uploaded = call("/file-service/v1/addFile", files={"file": ("cccd.jpg", image.content, image.media_type)},
                            data={"title": "cccd", "description": image.side})
            if not isinstance(uploaded.get("hash"), str) or not uploaded["hash"]:
                raise DocumentError("EKYC_BAD_RESPONSE", "VNPT không trả mã file.", 502)
            result = call(f"/ai/v1/ocr/id/{image.side}",
                          json={f"img_{image.side}": uploaded["hash"], "client_session": "LEGAL-AI-BE_" + uuid4().hex,
                                "type": -1, "validate_postcode": False, "token": uuid4().hex})
        return to_record(image.side, result)


def _object(response):
    if response.status_code in {401, 403}:
        raise DocumentError("EKYC_CREDENTIALS_REJECTED", "VNPT không chấp nhận thông tin truy cập.", 503)
    if response.status_code == 429:
        raise DocumentError("EKYC_QUOTA_EXCEEDED", "VNPT đang giới hạn lượt gọi.", 503)
    if len(response.content) > MAX_PROVIDER_JSON:
        raise DocumentError("EKYC_BAD_RESPONSE", "JSON VNPT vượt giới hạn.", 502)
    try:
        body = response.json()
    except ValueError:
        raise DocumentError("EKYC_BAD_RESPONSE", "VNPT không trả JSON hợp lệ.", 502) from None
    if response.status_code == 400:
        # Ví dụ ảnh không phải CCCD: {"errors": ["Loại giấy tờ không hợp lệ"]}.
        raise DocumentError("EKYC_UNREADABLE", "VNPT không đọc được ảnh CCCD.", 422)
    if response.status_code != 200 or not isinstance(body, dict) or not isinstance(body.get("object"), dict):
        raise DocumentError("EKYC_BAD_RESPONSE", "VNPT phản hồi không hợp lệ.", 502)
    return body["object"]


def to_record(side, obj):
    """Raw tự mô tả (engine=vnpt-ekyc) để parse_fields/parse_additional_fields/mrz_candidates đọc thẳng."""
    def text(key, limit=500):
        value = obj.get(key)
        if not isinstance(value, str):
            return None
        value = ", ".join(part.strip() for part in value.splitlines() if part.strip())
        if not value or value == "-" or len(value) > limit or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            return None
        return value

    def date(key):
        value = text(key, 32)
        try:
            return datetime.strptime(value, "%d/%m/%Y").date().isoformat() if value else None
        except ValueError:
            return None

    number = text("id", 32)
    fields = {"full_name": text("name", 200),
              "identity_number": number if number and re.fullmatch(r"[0-9]{12}", number) else None,
              "date_of_birth": date("birth_day"), "issue_date": date("issue_date"),
              "issue_place": text("issue_place"), "permanent_address": text("recent_location", 2000)}
    if side == "back":
        # Giống parse_fields: thông tin chủ thẻ chỉ lấy ở mặt trước, mặt sau đối chiếu qua MRZ.
        fields.update(full_name=None, identity_number=None, date_of_birth=None)
    additional = {"sex": {"nam": "male", "nu": "female"}.get(folded(text("gender") or "").strip()),
                  "nationality": text("nationality", 100), "place_of_origin": text("origin_location", 2000),
                  "expiry_date": date("expiry_date") or date("valid_date"),
                  "residence_address": text("recent_location", 2000),
                  "registered_birth_place": text("place_birth", 2000)}
    mrz = [re.sub(r"\s+", "", line).upper() for line in obj.get("mrz") or [] if isinstance(line, str)]
    return {"engine": "vnpt-ekyc", "side": side, "fields": fields, "additional": additional,
            "lines": [{"text": line, "confidence": 1.0} for line in mrz], "provider_response": obj}


def build_provider(local):
    settings = EkycSettings.from_env()
    return EkycFirstOcr(local, settings) if settings else local
