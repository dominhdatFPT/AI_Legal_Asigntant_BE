"""Live backend account gate. Never cache allow decisions or forward user JWT.

Contract is the status API supplied by User's owner: data.can_create_dossier,
data.block_reason, data.block_message. Document prepares evidence/profile; it
does not create business dossiers, so PROFILE_NOT_SUBMITTED is not a deadlock.
"""
import json
import os
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from app.core.errors import DocumentError


def unavailable():
    return DocumentError('USER_SERVICE_UNAVAILABLE', 'Không thể kiểm tra trạng thái tài khoản; vui lòng thử lại.', 503)


def account_gone():
    # Token còn hạn nhưng tài khoản đã bị xóa mềm: thử lại không giúp được gì, phải đăng nhập lại.
    return DocumentError('UNAUTHORIZED', 'Tài khoản không còn tồn tại. Vui lòng đăng nhập lại.', 401)


def is_user_not_found(raw):
    """Chỉ đúng khi User trả envelope lỗi USER_NOT_FOUND; 404 khác (sai route, proxy...) vẫn là 503."""
    try:
        body = json.loads(raw, object_pairs_hook=unique_object)
    except (ValueError, RecursionError):
        return False
    error = body.get('error') if isinstance(body, dict) else None
    return isinstance(error, dict) and error.get('code') == 'USER_NOT_FOUND'


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate upstream JSON key')
        result[key] = value
    return result


class UserStatus:
    def __init__(self, base, token, *, transport=None):
        try:
            parsed = urlsplit(base)
            valid = (parsed.scheme in {'http', 'https'} and parsed.hostname
                and not parsed.username and not parsed.password and not parsed.path.strip('/')
                and not parsed.query and not parsed.fragment
                and (parsed.scheme == 'https' or parsed.hostname in {'user-service', 'localhost', '127.0.0.1'}))
            parsed.port
        except (ValueError, TypeError):
            valid = False
        if not valid or not token or any(ch.isspace() for ch in token):
            raise unavailable()
        self.base, self._token, self.transport = base.rstrip('/'), token, transport

    @classmethod
    def from_env(cls):
        return cls(os.getenv('USER_SERVICE_URL', ''), os.getenv('INTERNAL_TOKEN', ''))

    def ensure_document_write(self, user_id, request_id):
        try:
            owner = str(UUID(str(user_id)))
        except (ValueError, TypeError):
            raise unavailable() from None
        # One bounded read, no allow cache or retry that multiplies User load.
        try:
            with httpx.Client(timeout=5, follow_redirects=False, trust_env=False, transport=self.transport) as client:
                with client.stream('GET', self.base + '/internal/v1/users/' + owner + '/status',
                    headers={'X-Internal-Token': self._token, 'X-Request-ID': request_id}) as response:
                    if response.status_code == 404:
                        raw = bytearray()
                        for chunk in response.iter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 16384:
                                raise unavailable()
                        if is_user_not_found(bytes(raw)):
                            raise account_gone()
                        raise unavailable()
                    if response.status_code != 200:
                        raise unavailable()
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 16384:
                            raise unavailable()
                    body = json.loads(raw, object_pairs_hook=unique_object,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if (not isinstance(body, dict) or 'error' not in body or body['error'] is not None
                or not isinstance(body.get('request_id'), str) or not body['request_id']):
                raise unavailable()
            data = body.get('data')
            if not isinstance(data, dict) or type(data.get('can_create_dossier')) is not bool:
                raise unavailable()
            reason = data.get('block_reason')
            if reason == 'ACCOUNT_LOCKED':
                message = data.get('block_message')
                if (not isinstance(message, str) or not message.strip() or len(message) > 2000
                    or any(ord(ch) < 32 and ch not in '\n\r\t' for ch in message)):
                    raise unavailable()
                # Explicit own-user message is part of the User contract; never
                # forward arbitrary upstream error responses/tracebacks/tokens.
                raise DocumentError('ACCOUNT_LOCKED', message, 403)
            if data['can_create_dossier'] is True and reason is None:
                return
            if data['can_create_dossier'] is False and reason == 'PROFILE_NOT_SUBMITTED':
                return
            raise unavailable()
        except (httpx.HTTPError, ValueError, TypeError, KeyError, RecursionError):
            raise unavailable() from None
