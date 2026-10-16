"""Bounded owner-authenticated Document handoff. JWT stays in request memory only."""
from dataclasses import dataclass, field
import hashlib
import json
import os
import re
import time
from uuid import UUID

import httpx

from app.core.config import service_base
from app.core.errors import ContractError
from app.services.contract_storage import MIMES
from app.services.user_gate import unique_object

MAX_FILE = 9437184


def bad():
    return ContractError("DOCUMENT_BAD_RESPONSE", "Dữ liệu Document không khớp; vui lòng thử lại.", 502)


@dataclass(frozen=True)
class ImportedSource:
    document_id: str
    job_id: str
    sha256: str
    format: str
    extraction: dict = field(repr=False)
    content: bytes = field(repr=False)


class DocumentImport:
    def __init__(self, base, transport=None):
        self.base, self.transport = base, transport

    @classmethod
    def from_env(cls):
        return cls(os.getenv("DOCUMENT_SERVICE_URL", ""))

    def load(self, document_id, owner, token, request_id):
        base = service_base(self.base, "document-service")
        document_id, owner = str(document_id), str(owner)
        headers = {"Authorization": "Bearer " + token, "X-Request-ID": request_id, "Accept-Encoding": "identity"}
        with httpx.Client(transport=self.transport, timeout=5, follow_redirects=False, trust_env=False) as client:
            def read(suffix, limit, binary=False):
                deadline = time.monotonic() + 5
                try:
                    with client.stream("GET", base + "/api/v1/documents/" + document_id + suffix, headers=headers) as response:
                        if response.status_code in {401,403,404,409}:
                            raise ContractError("DOCUMENT_HTTP_" + str(response.status_code), "Document chưa sẵn sàng hoặc không được phép truy cập.", response.status_code)
                        if response.status_code != 200:
                            raise bad()
                        raw = bytearray()
                        for chunk in response.iter_bytes(65536):
                            raw.extend(chunk)
                            if len(raw) > limit or time.monotonic() > deadline:
                                raise bad()
                        if binary:
                            return bytes(raw), response.headers
                        body = json.loads(raw, object_pairs_hook=unique_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                        if not isinstance(body, dict) or body.get("error", True) is not None or not isinstance(body.get("request_id"), str) or not body["request_id"] or not isinstance(body.get("data"), dict):
                            raise bad()
                        return body["data"]
                except httpx.TimeoutException:
                    raise ContractError("DOCUMENT_TIMEOUT", "Document hết thời gian chờ.", 504) from None
                except httpx.HTTPError:
                    raise ContractError("DOCUMENT_UNAVAILABLE", "Không thể kết nối Document.", 503) from None
                except (ValueError, TypeError, RecursionError):
                    raise bad() from None
            try:
                meta = read("", 65536)
                if str(UUID(meta["document_id"])) != document_id or str(UUID(meta["owner_user_id"])) != owner:
                    raise ContractError("DOCUMENT_NOT_FOUND", "Không tìm thấy tài liệu.", 404)
                if meta["purpose"] != "contract_template" or meta["document_type"] != "contract" or meta["media_type"] not in MIMES.values():
                    raise ContractError("DOCUMENT_NOT_CONTRACT_TEMPLATE", "Chỉ nhập mẫu hợp đồng DOCX/PDF.", 422)
                if meta["file_status"] != "available" or meta["processing_status"] != "completed":
                    raise ContractError("EXTRACTION_NOT_READY", "Chờ Document xử lý xong trước khi nhập.", 409)
                size, digest, job = meta["size_bytes"], meta["sha256"], str(UUID(meta["processing_job_id"]))
                if type(size) is not int or not 1 <= size <= MAX_FILE or not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest):
                    raise bad()
                processing = read("/processing", 65536)
                if processing["document_id"] != document_id or processing["job_id"] != job:
                    raise bad()
                if processing["status"] != "completed" or processing["file_status"] != "available":
                    raise ContractError("EXTRACTION_NOT_READY", "Document chưa xử lý xong.", 409)
                extraction = read("/extraction", 16700000)
                format = next(k for k,v in MIMES.items() if v == meta["media_type"])
                if (extraction["document_id"] != document_id or extraction["job_id"] != job or extraction["schema_version"] != "1.0"
                    or extraction["source_sha256"] != digest or extraction["source_kind"] not in ({"docx"} if format == "docx" else {"pdf_text","pdf_scan","pdf_mixed"})
                    or not isinstance(extraction["text"], str) or len(extraction["text"]) > 1000000
                    or not isinstance(extraction["pages"], list) or len(extraction["pages"]) > 100
                    or not isinstance(extraction["warnings"], list) or len(extraction["warnings"]) > 100
                    or any(not isinstance(w,str) or len(w)>500 for w in extraction["warnings"])
                    or not isinstance(extraction["extracted_fields"], list) or extraction["extracted_fields"]):
                    raise bad()
                data, h = read("/content", MAX_FILE, True)
                if len(data) != size or hashlib.sha256(data).hexdigest() != digest or h.get("X-Content-SHA256") != digest or h.get("Content-Length") != str(size) or h.get("Content-Type", "").split(";")[0] != meta["media_type"] or h.get("X-Content-Type-Options") != "nosniff":
                    raise bad()
                # Latest metadata recheck catches deletion/retry while copying the source.
                current = read("", 65536)
                if any(current.get(k) != meta.get(k) for k in ("document_id","owner_user_id","processing_job_id","processing_status","file_status","sha256","size_bytes","purpose","document_type","media_type")):
                    raise bad()
                return ImportedSource(document_id, job, digest, format, extraction, data)
            except (KeyError, TypeError, ValueError, AttributeError, StopIteration):
                raise bad() from None
