"""One shared requests.Session per process (shortcut: process-lifetime, closed at exit): keep-alive + TLS reuse."""
from http.cookiejar import DefaultCookiePolicy

import requests
from requests.adapters import HTTPAdapter

session = requests.Session()
# Shared by every user/thread: never store or replay cookies set by an upstream response.
session.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))
_adapter = HTTPAdapter(pool_connections=4, pool_maxsize=16)
session.mount('http://', _adapter)
session.mount('https://', _adapter)
