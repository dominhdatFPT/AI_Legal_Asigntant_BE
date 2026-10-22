"""One bounded office search: geocode, discover, dedupe, detail, distance."""
import asyncio
import re

from app.core.errors import LocationError
from app.services.address_normalizer import normalize_address
from app.services.distance import haversine_meters
from app.services.maps_links import directions_url


def _province_key(value: str) -> str:
    value = value.casefold().strip()
    value = re.sub(r"^(tỉnh|thành phố)\s+", "", value)
    return " ".join(value.split())


async def search_offices(client, request: dict) -> dict:
    address = await asyncio.to_thread(normalize_address, request["address"])
    query = request["query"].strip()
    radius = request["radius_meters"]
    limit = request["limit"]
    candidates = await client.geocode(address.search_address)
    if not candidates:
        raise LocationError("ADDRESS_NOT_FOUND", "Không định vị được địa chỉ đã nhập.", 422)
    origin = candidates[0]
    # Do not silently choose one of distant results for an underspecified address.
    if any(haversine_meters(origin.latitude, origin.longitude, other.latitude, other.longitude) > 500
           for other in candidates[1:]):
        raise LocationError("ADDRESS_AMBIGUOUS", "Địa chỉ chưa đủ rõ, vui lòng bổ sung.", 422)
    # Compare only current official province; old administrative names can have changed.
    if address.administrative_version == "current":
        provider_province = origin.compound.get("province")
        if isinstance(provider_province, str) and _province_key(provider_province) != _province_key(address.province_name):
            raise LocationError("ADDRESS_AMBIGUOUS", "Địa chỉ chưa khớp tỉnh/thành đã chọn.", 422)

    suggestions = await client.search_places(query, origin.latitude, origin.longitude, radius, 10)
    unique = list(dict.fromkeys(item.place_id for item in suggestions))[:10]
    items = []
    failures = []
    completed_details = 0
    for place_id in unique:
        try:
            detail = await client.place_detail(place_id)
        except LocationError as exc:
            if exc.code in ("GOONG_ACCESS_DENIED", "GOONG_QUOTA_EXCEEDED"):
                raise
            failures.append(exc)
            continue
        completed_details += 1
        distance = haversine_meters(origin.latitude, origin.longitude, detail.latitude, detail.longitude)
        if distance > radius:
            continue
        items.append({
            "provider": "goong", "place_id": detail.place_id, "name": detail.name,
            "address": detail.address, "latitude": detail.latitude, "longitude": detail.longitude,
            "distance_meters": distance, "distance_type": "straight_line",
            "directions_url": directions_url(detail.latitude, detail.longitude,
                                               (origin.latitude, origin.longitude)),
        })
    if failures and completed_details == 0:
        raise failures[0]
    items.sort(key=lambda item: (item["distance_meters"], item["place_id"]))
    return {
        "origin": {"input_address": address.search_address,
                   "resolved_address": origin.formatted_address,
                   "latitude": origin.latitude, "longitude": origin.longitude},
        "items": items[:limit], "is_partial": bool(failures),
        "warnings": ([{"code": "GOONG_PARTIAL_RESULTS", "message": "Một số địa điểm chưa thể tải chi tiết."}]
                     if failures else []),
    }


async def bounded_search(client, request: dict, timeout: float) -> dict:
    try:
        return await asyncio.wait_for(search_offices(client, request), timeout=timeout)
    except asyncio.TimeoutError:
        raise LocationError("GOONG_TIMEOUT", "Tìm kiếm quá thời gian cho phép.", 504) from None
