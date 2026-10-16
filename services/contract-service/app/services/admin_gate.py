"""Live role/account check through the existing User /me API; no User DB access."""
import json
import os
from uuid import UUID

import httpx

from app.core.config import service_base
from app.core.errors import ContractError
from app.services.user_gate import unavailable, unique_object


class AdminGate:
    def __init__(self, base, *, transport=None):
        try:
            self.base = service_base(base, "user-service")
        except ContractError:
            raise unavailable() from None
        self.transport = transport

    @classmethod
    def from_env(cls):
        return cls(os.getenv("USER_SERVICE_URL", ""))

    def ensure_admin(self, user_id, token, request_id):
        try:
            with httpx.Client(timeout=5, follow_redirects=False, trust_env=False, transport=self.transport) as client:
                with client.stream("GET", self.base + "/api/v1/users/me", headers={
                    "Authorization": "Bearer " + token, "X-Request-ID": request_id,
                }) as response:
                    raw = bytearray()
                    for chunk in response.iter_bytes(chunk_size=16384):
                        raw.extend(chunk)
                        if len(raw) > 16384:
                            raise unavailable()
                    body = json.loads(raw, object_pairs_hook=unique_object,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                    if not isinstance(body, dict):
                        raise unavailable()
                    if not isinstance(body.get("request_id"), str) or not body["request_id"]:
                        raise unavailable()
                    if response.status_code == 401:
                        raise ContractError("UNAUTHORIZED", "Tài khoản không còn hợp lệ.", 401)
                    if response.status_code == 403:
                        error = body.get("error")
                        if isinstance(error, dict) and error.get("code") == "ACCOUNT_LOCKED":
                            raise ContractError("ACCOUNT_LOCKED", "Tài khoản của bạn đã bị khóa.", 403)
                        raise unavailable()
                    if response.status_code != 200 or body.get("error", True) is not None:
                        raise unavailable()
                    data = body.get("data")
                    if not isinstance(data, dict) or str(UUID(data.get("id", ""))) != user_id:
                        raise unavailable()
                    if data.get("status") == "locked":
                        raise ContractError("ACCOUNT_LOCKED", "Tài khoản của bạn đã bị khóa.", 403)
                    if data.get("status") != "active":
                        raise unavailable()
                    if data.get("role") not in {"admin", "citizen", "lawyer"}:
                        raise unavailable()
                    if data["role"] != "admin":
                        raise ContractError("FORBIDDEN", "Tài khoản không còn quyền quản trị.", 403)
        except (httpx.HTTPError, ValueError, TypeError, AttributeError, RecursionError):
            raise unavailable() from None
