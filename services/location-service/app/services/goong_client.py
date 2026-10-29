"""Bounded, secret-safe async adapter for Goong REST API V2."""
import asyncio
from collections import deque
from dataclasses import dataclass
import json
import math
import time

import httpx

from app.core.errors import LocationError
from app.core.metrics import provider_counter


HOST = "https://rsapi.goong.io"
MAX_BODY_BYTES = 2_000_000


def _bad_response():
    raise LocationError("GOONG_BAD_RESPONSE", "Dữ liệu bản đồ không hợp lệ.", 502)


def coordinates(value):
    if not isinstance(value, dict):
        _bad_response()
    lat, lng = value.get("lat"), value.get("lng")
    if (isinstance(lat, bool) or isinstance(lng, bool) or not isinstance(lat, (int, float))
            or not isinstance(lng, (int, float)) or not math.isfinite(lat) or not math.isfinite(lng)
            or not -90 <= lat <= 90 or not -180 <= lng <= 180):
        _bad_response()
    return float(lat), float(lng)


def _item_location(item):
    geometry = item.get("geometry")
    if not isinstance(geometry, dict):
        _bad_response()
    return coordinates(geometry.get("location"))


def _optional_object(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        _bad_response()
    return value


@dataclass(frozen=True)
class GeocodeCandidate:
    formatted_address: str
    latitude: float
    longitude: float
    compound: dict
    deprecated_compound: dict


@dataclass(frozen=True)
class PlaceCandidate:
    place_id: str
    description: str


@dataclass(frozen=True)
class PlaceDetail:
    place_id: str
    name: str
    address: str
    latitude: float
    longitude: float
    province: str | None = None
    compound: dict | None = None
    deprecated_compound: dict | None = None


class GoongClient:
    def __init__(self, api_key: str, *, timeout: float = 5, max_rps: float = 4,
                 client: httpx.AsyncClient | None = None, daily_budget: int = 1000):
        self._key = api_key
        self._timeout = timeout
        self._max_rps = max_rps
        self._client = client or httpx.AsyncClient(base_url=HOST, follow_redirects=False,
                                                    trust_env=False, timeout=timeout)
        self._owns_client = client is None
        self._lock = asyncio.Lock()
        self._recent = deque()
        self._day = None
        self._daily_calls = 0
        self._daily_budget = daily_budget

    async def aclose(self):
        if self._owns_client:
            await self._client.aclose()

    async def _reserve_call(self):
        while True:
            async with self._lock:
                now = time.monotonic()
                while self._recent and now - self._recent[0] >= 1:
                    self._recent.popleft()
                day = int(time.time() // 86400)
                if self._day != day:
                    self._day, self._daily_calls = day, 0
                if self._daily_calls >= self._daily_budget:
                    raise LocationError("GOONG_QUOTA_EXCEEDED", "Đã hết ngân sách bản đồ hôm nay.", 503)
                if len(self._recent) < self._max_rps:
                    self._recent.append(now)
                    self._daily_calls += 1
                    counter = provider_counter.get()
                    if counter is not None:
                        counter[0] += 1
                    return
                delay = max(0.001, 1 - (now - self._recent[0]))
            await asyncio.sleep(delay)

    async def _get(self, path: str, params: dict):
        for attempt in range(3):
            await self._reserve_call()
            try:
                async with self._client.stream("GET", path, params={**params, "api_key": self._key},
                                               timeout=self._timeout) as response:
                    status_code = response.status_code
                    if status_code in (401, 403):
                        raise LocationError("GOONG_ACCESS_DENIED", "Dịch vụ bản đồ từ chối truy cập.", 503)
                    if status_code == 429:
                        raise LocationError("GOONG_QUOTA_EXCEEDED", "Dịch vụ bản đồ đã hết hạn mức.", 503)
                    if status_code >= 500:
                        if attempt < 2:
                            continue
                        _bad_response()
                    if status_code >= 400:
                        raise LocationError("GOONG_BAD_RESPONSE", "Yêu cầu tới bản đồ không hợp lệ.", 502)
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BODY_BYTES:
                            _bad_response()
            except httpx.TimeoutException:
                if attempt < 2:
                    continue
                raise LocationError("GOONG_TIMEOUT", "Bản đồ phản hồi quá chậm.", 504) from None
            except httpx.RequestError:
                if attempt < 2:
                    continue
                raise LocationError("GOONG_UNAVAILABLE", "Không kết nối được dịch vụ bản đồ.", 503) from None
            try:
                payload = json.loads(body)
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
                _bad_response()
            if not isinstance(payload, dict):
                _bad_response()
            status = payload.get("status")
            if status in ("ZERO_RESULTS", "NOT_FOUND"):
                return {}
            if status in ("OVER_QUERY_LIMIT", "RESOURCE_EXHAUSTED"):
                raise LocationError("GOONG_QUOTA_EXCEEDED", "Dịch vụ bản đồ đã hết hạn mức.", 503)
            if status in ("REQUEST_DENIED", "PERMISSION_DENIED"):
                raise LocationError("GOONG_ACCESS_DENIED", "Dịch vụ bản đồ từ chối truy cập.", 503)
            if status != "OK":
                _bad_response()
            return payload
        _bad_response()

    async def geocode(self, address: str) -> list[GeocodeCandidate]:
        payload = await self._get("/v2/geocode", {"address": address,
                                                    "has_deprecated_administrative_unit": "true"})
        results = payload.get("results", [])
        if not isinstance(results, list):
            _bad_response()
        candidates = []
        for item in results[:10]:
            if not isinstance(item, dict) or not isinstance(item.get("formatted_address"), str):
                _bad_response()
            lat, lng = _item_location(item)
            candidates.append(GeocodeCandidate(item["formatted_address"], lat, lng,
                                                _optional_object(item.get("compound")),
                                                _optional_object(item.get("deprecated_compound"))))
        return candidates

    async def search_places(self, query: str, latitude: float, longitude: float,
                            limit: int) -> list[PlaceCandidate]:
        payload = await self._get("/v2/place/autocomplete", {
            "input": query, "location": f"{latitude},{longitude}",
            "limit": limit,
        })
        predictions = payload.get("predictions", [])
        if not isinstance(predictions, list):
            _bad_response()
        result = []
        for item in predictions[:limit]:
            if not isinstance(item, dict) or not isinstance(item.get("place_id"), str) or not item["place_id"]:
                _bad_response()
            result.append(PlaceCandidate(item["place_id"], item.get("description") or ""))
        return result

    async def place_detail(self, place_id: str) -> PlaceDetail:
        payload = await self._get("/v2/place/detail", {
            "place_id": place_id,
            "has_deprecated_administrative_unit": "true",
        })
        item = payload.get("result")
        if not isinstance(item, dict):
            _bad_response()
        lat, lng = _item_location(item)
        if any(not isinstance(item.get(key), str) or not item[key] for key in ("place_id", "name", "formatted_address")):
            _bad_response()
        compound = _optional_object(item.get("compound"))
        deprecated_compound = _optional_object(item.get("deprecated_compound"))
        province = compound.get("province")
        if province is not None and (not isinstance(province, str) or not province.strip()):
            _bad_response()
        return PlaceDetail(item["place_id"], item["name"], item["formatted_address"], lat, lng,
                           province, compound, deprecated_compound)
