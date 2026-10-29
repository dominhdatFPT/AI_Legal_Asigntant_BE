"""Authenticated paid office-search endpoint; administrative catalog stays public."""
from fastapi import APIRouter, Depends, Request
from common_auth import CurrentUser
from common_auth.fastapi_dep import get_current_user

from app.core.errors import LocationError, success
from app.services.office_search import bounded_search


router = APIRouter(prefix="/api/v1/locations/offices", tags=["Offices"])


def _invalid(field):
    raise LocationError("VALIDATION_ERROR", "Kiểm tra dữ liệu gửi lên.", 422,
                        details=[{"field": field, "message": "Dữ liệu không hợp lệ."}])


def validate_search_payload(value):
    if not isinstance(value, dict) or set(value) - {"address", "query"}:
        _invalid("body")
    address = value.get("address")
    if not isinstance(address, dict):
        _invalid("address")
    allowed = {"administrative_version", "detail", "province_code", "district_code", "ward_code"}
    if set(address) - allowed:
        _invalid("address")
    query = value.get("query")
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 150:
        _invalid("query")
    detail = address.get("detail")
    if not isinstance(detail, str) or not 1 <= len(detail.strip()) <= 300:
        _invalid("address.detail")
    return {"address": address, "query": query.strip()}


@router.post("/search")
async def search(request: Request, user: CurrentUser = Depends(get_current_user)):
    try:
        payload = await request.json()
    except ValueError:
        raise LocationError("BAD_REQUEST", "JSON gửi lên không đọc được.", 400) from None
    search_request = validate_search_payload(payload)
    state = request.app.state
    settings = state.location_settings
    unavailable = settings.search_unavailable_code()
    if unavailable:
        raise LocationError(unavailable, "Tìm kiếm địa điểm hiện chưa sẵn sàng.", 503)
    await state.search_limiter.check(user.id)
    result = await bounded_search(state.goong_client, search_request, settings.search_timeout_seconds)
    lookups = getattr(state, "contact_lookups", None)
    if lookups:
        lookup_id = lookups.register(user.id, result["items"])
        if lookup_id:
            result["web_lookup_id"] = lookup_id
    return success(request, result)


@router.post("/enrich")
async def enrich(request: Request, user: CurrentUser = Depends(get_current_user)):
    try:
        payload = await request.json()
    except ValueError:
        raise LocationError("BAD_REQUEST", "JSON gửi lên không đọc được.", 400) from None
    if (not isinstance(payload, dict) or set(payload) != {"lookup_id"}
            or not isinstance(payload["lookup_id"], str) or not 20 <= len(payload["lookup_id"]) <= 64):
        _invalid("lookup_id")
    await request.app.state.web_limiter.check(user.id)
    result = await request.app.state.contact_lookups.lookup(payload["lookup_id"], user.id)
    return success(request, result)
