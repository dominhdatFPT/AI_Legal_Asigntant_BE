"""Bounded internal HTTP delivery; no OCR/file data, redirects, proxies or secrets in logs."""
import json
import os
import time
from urllib.parse import urlsplit

import httpx


class DeliveryError(Exception):
    def __init__(self, code, retryable):
        super().__init__(code)
        self.code, self.retryable = code, retryable


class NotificationDelivery:
    def __init__(self, base, token, *, transport=None, wait=time.sleep):
        try:
            parsed = urlsplit(base)
            valid = (parsed.scheme in {'http', 'https'} and parsed.hostname and not parsed.username
                     and not parsed.password and not parsed.path.strip('/') and not parsed.query and not parsed.fragment
                     and (parsed.scheme == 'https' or parsed.hostname in {'notification-service', 'localhost', '127.0.0.1'}))
            parsed.port  # Validate malformed ports, without echoing config.
        except ValueError:
            valid = False
        if not valid or not token or any(c.isspace() for c in token):
            raise DeliveryError('NOTIFICATION_CONFIG_INVALID', False)
        self.base, self._token = base.rstrip('/'), token
        self.transport, self.wait = transport, wait

    @classmethod
    def from_env(cls):
        if os.getenv('DOCUMENT_NOTIFICATIONS_ENABLED', 'false') != 'true':
            raise DeliveryError('NOTIFICATION_DISABLED', False)
        return cls(os.getenv('NOTIFICATION_SERVICE_URL', ''), os.getenv('INTERNAL_TOKEN', ''))

    def send(self, event):
        snapshot = json.loads(json.dumps(event))
        headers = {'X-Internal-Token': self._token, 'X-Request-ID': 'doc-event-' + event['event_id']}
        code = 'NOTIFICATION_UNAVAILABLE'
        with httpx.Client(timeout=5, follow_redirects=False, trust_env=False, transport=self.transport) as client:
            for attempt in range(3):
                try:
                    with client.stream('POST', self.base + '/internal/v1/notifications/events', json=snapshot, headers=headers) as response:
                        if response.status_code == 200:
                            body = bytearray()
                            for chunk in response.iter_bytes():
                                body.extend(chunk)
                                if len(body) > 16384:
                                    raise DeliveryError('NOTIFICATION_BAD_RESPONSE', True)
                            try:
                                result = json.loads(body)
                                data = result['data']
                                duplicate, count = data['is_duplicate'], data['created_count']
                                valid = (result.get('error') is None and isinstance(result['request_id'], str)
                                    and bool(result['request_id']) and data['event_id'] == snapshot['event_id']
                                    and type(duplicate) is bool and type(count) is int
                                    and count == (0 if duplicate else 1))
                            except (ValueError, KeyError, TypeError):
                                valid = False
                            if valid:
                                return
                            code = 'NOTIFICATION_BAD_RESPONSE'
                        elif response.status_code == 429 or response.status_code >= 500:
                            code = 'NOTIFICATION_HTTP_' + str(response.status_code)
                        else:
                            raise DeliveryError('NOTIFICATION_HTTP_' + str(response.status_code), False)
                except httpx.TransportError:
                    code = 'NOTIFICATION_UNAVAILABLE'
                except DeliveryError as exc:
                    if not exc.retryable:
                        raise
                    code = exc.code
                if attempt < 2:
                    self.wait((0.5, 1)[attempt])
        raise DeliveryError(code, True)
