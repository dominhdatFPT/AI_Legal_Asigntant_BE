"""Existing public administrative catalogue adapter, kept behavior compatible."""
from functools import lru_cache
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.errors import LocationError


UPSTREAM = "https://provinces.open-api.vn"
CATALOG_TTL_SECONDS = 24 * 60 * 60


@lru_cache(maxsize=256)
def _cached_upstream(version: str, path: str, epoch: int):
    """Fetch a bounded, fixed-host public administrative catalogue response."""
    request = Request(UPSTREAM + path, headers={"Accept": "application/json", "User-Agent": "legal-assistant-location/1"})
    try:
        with urlopen(request, timeout=10) as response:
            if response.headers.get_content_type() != "application/json":
                raise ValueError
            payload = response.read(2_000_001)
            if len(payload) > 2_000_000:
                raise ValueError
            return json.loads(payload)
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        raise LocationError("ADMINISTRATIVE_SOURCE_BAD_RESPONSE", "Không tải được danh mục hành chính.", 502) from None


def _upstream(path: str):
    """Separate catalogue versions and expire successful responses after a day."""
    if path.startswith("/api/v1/"):
        version = "legacy"
    elif path.startswith("/api/v2/"):
        version = "current"
    else:
        raise ValueError("Unsupported administrative catalogue path")
    return _cached_upstream(version, path, int(time.monotonic() // CATALOG_TTL_SECONDS))


def _items(values):
    if not isinstance(values, list):
        raise LocationError("ADMINISTRATIVE_SOURCE_BAD_RESPONSE", "Danh mục hành chính không hợp lệ.", 502)
    result = []
    for item in values:
        if (not isinstance(item, dict) or not isinstance(item.get("code"), int)
                or not isinstance(item.get("name"), str) or not 1 <= len(item["name"]) <= 100):
            raise LocationError("ADMINISTRATIVE_SOURCE_BAD_RESPONSE", "Danh mục hành chính không hợp lệ.", 502)
        result.append({"code": item["code"], "name": item["name"]})
    return {"items": result, "source": "provinces.open-api.vn"}


def legacy_provinces():
    return _items(_upstream("/api/v1/p/"))


def legacy_districts(province_code: int):
    payload = _upstream(f"/api/v1/p/{province_code}?depth=2")
    return _items(payload.get("districts") if isinstance(payload, dict) else None)


def legacy_wards(district_code: int):
    payload = _upstream(f"/api/v1/d/{district_code}?depth=2")
    return _items(payload.get("wards") if isinstance(payload, dict) else None)


def current_provinces():
    return _items(_upstream("/api/v2/p/"))


def current_wards(province_code: int):
    payload = _upstream(f"/api/v2/p/{province_code}?depth=2")
    return _items(payload.get("wards") if isinstance(payload, dict) else None)
