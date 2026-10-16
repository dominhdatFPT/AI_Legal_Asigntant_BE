"""Read-only cross-service admission checks. No direct DB access or JWT persistence."""
import json
import os
import time
from uuid import UUID

import httpx

from app.core.config import service_base
from app.core.errors import ContractError
from app.services.user_gate import unique_object


def unavailable(service):
    return ContractError(service.upper() + "_SERVICE_UNAVAILABLE", "Không thể kiểm tra dữ liệu " + service + "; vui lòng thử lại.", 503)


class DraftReferences:
    def __init__(self, document_base, procedure_base, *, transport=None):
        # Lazy: an empty attachment list/null procedure never requires these URLs.
        self.document_base, self.procedure_base, self.transport = document_base, procedure_base, transport

    @classmethod
    def from_env(cls):
        return cls(os.getenv("DOCUMENT_SERVICE_URL", ""), os.getenv("PROCEDURE_SERVICE_URL", ""))

    def fetch(self, client, base, path, service, headers, deadline):
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise unavailable(service)
            with client.stream("GET", base + path, headers=headers, timeout=min(5, remaining)) as response:
                raw = bytearray()
                for chunk in response.iter_bytes(chunk_size=16384):
                    raw.extend(chunk)
                    if len(raw) > 262144 or time.monotonic() > deadline:
                        raise unavailable(service)
                body = json.loads(raw, object_pairs_hook=unique_object,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if not isinstance(body, dict) or not isinstance(body.get("request_id"), str) or not body["request_id"]:
                    raise unavailable(service)
                error = body.get("error", True)
                if response.status_code == 404 and isinstance(error, dict) and error.get("code") == service.upper() + "_NOT_FOUND":
                    raise ContractError(service.upper() + "_NOT_FOUND", "Không tìm thấy tài liệu hoặc thủ tục được chọn.", 404)
                if service == "document" and response.status_code == 401:
                    raise ContractError("UNAUTHORIZED", "Access token không còn hợp lệ.", 401)
                if response.status_code != 200 or error is not None or not isinstance(body.get("data"), dict):
                    raise unavailable(service)
                return body["data"]
        except (httpx.HTTPError, ValueError, TypeError, RecursionError):
            raise unavailable(service) from None

    def ensure(self, owner, token, request_id, documents, procedure=None):
        deadline = time.monotonic() + 15
        with httpx.Client(follow_redirects=False, trust_env=False, transport=self.transport) as client:
            if documents:
                try:
                    base = service_base(self.document_base, "document-service")
                except ContractError:
                    raise unavailable("document") from None
                for document in documents:
                    data = self.fetch(client, base, "/api/v1/documents/" + str(document), "document",
                        {"Authorization":"Bearer " + token, "X-Request-ID":request_id}, deadline)
                    try:
                        valid = str(UUID(data.get("document_id", ""))) == str(document) and str(UUID(data.get("owner_user_id", ""))) == owner
                    except (ValueError, TypeError, AttributeError):
                        raise unavailable("document") from None
                    if not valid:
                        raise unavailable("document")
                    if not isinstance(data.get("file_status"), str):
                        raise unavailable("document")
                    if data.get("file_status") in {"pending_check", "rejected"}:
                        raise ContractError("DOCUMENT_NOT_AVAILABLE", "Tài liệu chưa sẵn sàng hoặc bị từ chối.", 409)
                    if data.get("file_status") != "available":
                        raise unavailable("document")
            if procedure is not None:
                try:
                    base = service_base(self.procedure_base, "procedure-service")
                except ContractError:
                    raise unavailable("procedure") from None
                # Public Procedure API; do not forward private JWT/internal token.
                data = self.fetch(client, base, "/api/v1/procedures/" + str(procedure), "procedure",
                    {"X-Request-ID":request_id}, deadline)
                try:
                    valid = str(UUID(data.get("id", ""))) == str(procedure)
                except (ValueError, TypeError, AttributeError):
                    raise unavailable("procedure") from None
                if not valid or not isinstance(data.get("requirements"), list) or not isinstance(data.get("name"), str) or not data["name"].strip():
                    raise unavailable("procedure")
