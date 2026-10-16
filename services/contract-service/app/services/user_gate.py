"""Prepared User status contract from the User owner's message, not a live claim."""
import json
import os
from uuid import UUID

import httpx

from app.core.config import service_base
from app.core.errors import ContractError


def unavailable():
    return ContractError("USER_SERVICE_UNAVAILABLE", "Không thể kiểm tra trạng thái tài khoản; vui lòng thử lại.", 503)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


class UserGate:
    def __init__(self, base, token, *, transport=None):
        try:
            self.base = service_base(base, "user-service")
        except ContractError:
            raise unavailable() from None
        if not token or any(ch.isspace() or ord(ch) < 32 or ord(ch) > 126 for ch in token):
            raise unavailable()
        self._token, self.transport = token, transport

    @classmethod
    def from_env(cls):
        return cls(os.getenv("USER_SERVICE_URL", ""), os.getenv("INTERNAL_TOKEN", ""))

    def ensure_can_create(self, user_id, request_id):
        try:
            owner = str(UUID(str(user_id)))
            with httpx.Client(timeout=5, follow_redirects=False, trust_env=False, transport=self.transport) as client:
                with client.stream("GET", self.base + "/internal/v1/users/" + owner + "/status",
                    headers={"X-Internal-Token": self._token, "X-Request-ID": request_id}) as response:
                    if response.status_code != 200:
                        raise unavailable()
                    raw = bytearray()
                    for chunk in response.iter_bytes(chunk_size=16384):
                        raw.extend(chunk)
                        if len(raw) > 16384:
                            raise unavailable()
                    body = json.loads(raw, object_pairs_hook=unique_object,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if (not isinstance(body, dict) or "error" not in body or body["error"] is not None
                or not isinstance(body.get("request_id"), str) or not body["request_id"]):
                raise unavailable()
            data = body.get("data")
            if not isinstance(data, dict) or type(data.get("can_create_dossier")) is not bool:
                raise unavailable()
            reason = data.get("block_reason")
            if reason == "ACCOUNT_LOCKED" or data["can_create_dossier"] is False and reason == "PROFILE_NOT_SUBMITTED":
                message = data.get("block_message")
                if (not isinstance(message, str) or not message.strip() or len(message) > 2000
                    or any(ord(ch) < 32 and ch not in "\r\n\t" for ch in message)):
                    raise unavailable()
                raise ContractError(reason, message, 403)
            if data["can_create_dossier"] is True and reason is None:
                return
            raise unavailable()
        except (httpx.HTTPError, ValueError, TypeError, KeyError, RecursionError):
            raise unavailable() from None
