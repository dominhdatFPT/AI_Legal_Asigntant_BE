"""Admin check from the verified JWT `role` claim; no call to User."""
import os

from common_auth.core import AuthError, decode_access_token

from app.core.errors import ContractError


class AdminGate:
    def __init__(self, base, *, transport=None):
        self.base, self.transport = base, transport

    @classmethod
    def from_env(cls):
        return cls(os.getenv("USER_SERVICE_URL", ""))

    def ensure_admin(self, user_id, token, request_id):
        try:
            current = decode_access_token(token)
        except AuthError:
            raise ContractError("UNAUTHORIZED", "Tài khoản không còn hợp lệ.", 401) from None
        if current.id != user_id:
            raise ContractError("UNAUTHORIZED", "Tài khoản không còn hợp lệ.", 401)
        if current.role != "admin":
            raise ContractError("FORBIDDEN", "Tài khoản không còn quyền quản trị.", 403)
