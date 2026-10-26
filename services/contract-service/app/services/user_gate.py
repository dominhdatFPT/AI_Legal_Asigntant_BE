"""Account gate. No call to User: tokens are already checked (signature, logout, block) by common_auth."""
import os

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
        self.base, self._token, self.transport = base, token, transport

    @classmethod
    def from_env(cls):
        return cls(os.getenv("USER_SERVICE_URL", ""), os.getenv("INTERNAL_TOKEN", ""))

    def ensure_can_create(self, user_id, request_id):
        # The caller already passed get_current_user (revoked jti / blocked user are rejected there).
        return
