"""Process-wide pooled httpx.Client: keep-alive + TLS reuse instead of one handshake per call."""
from contextlib import contextmanager
from http.cookiejar import DefaultCookiePolicy
import threading

import httpx

_clients: dict[tuple, httpx.Client] = {}
_lock = threading.Lock()
# Below Uvicorn's 5s default keep-alive timeout so pooled POSTs do not hit a half-closed socket.
_DEFAULT_LIMITS = httpx.Limits(keepalive_expiry=2.0)


@contextmanager
def http_client(transport=None, **options):
    """Yield a shared client for these options (shortcut: process-lifetime, closed at process exit).

    An explicit transport (tests: httpx.MockTransport) gets a private client closed afterwards, as before.
    """
    if transport is not None:
        with httpx.Client(transport=transport, **options) as client:
            yield client
        return
    key = tuple(sorted(options.items()))
    with _lock:
        client = _clients.get(key)
        if client is None:
            client_opts = {"limits": _DEFAULT_LIMITS, **options}
            client = httpx.Client(**client_opts)
            # Shared by every user/thread: never store or replay cookies set by an upstream response.
            client.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))
            _clients[key] = client
    yield client
