"""Public legacy and current administrative catalogue endpoints."""
from fastapi import APIRouter, Request

from app.core.errors import success
from app.services import administrative_catalog as catalog


router = APIRouter(prefix="/api/v1/locations/administrative", tags=["Administrative units"])


@router.get("/legacy/provinces")
def legacy_provinces(request: Request):
    return success(request, catalog.legacy_provinces())


@router.get("/legacy/provinces/{province_code}/districts")
def legacy_districts(province_code: int, request: Request):
    return success(request, catalog.legacy_districts(province_code))


@router.get("/legacy/districts/{district_code}/wards")
def legacy_wards(district_code: int, request: Request):
    return success(request, catalog.legacy_wards(district_code))


@router.get("/current/provinces")
def current_provinces(request: Request):
    return success(request, catalog.current_provinces())


@router.get("/current/provinces/{province_code}/wards")
def current_wards(province_code: int, request: Request):
    return success(request, catalog.current_wards(province_code))
