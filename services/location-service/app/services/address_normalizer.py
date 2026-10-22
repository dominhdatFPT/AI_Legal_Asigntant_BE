"""Validate administrative codes and build an address without rewriting user input."""
from dataclasses import dataclass
import re

from app.core.errors import LocationError
from app.services import administrative_catalog as catalog


@dataclass(frozen=True)
class NormalizedAddress:
    input_address: str
    search_address: str
    administrative_version: str
    province_name: str
    district_name: str | None
    ward_name: str | None


def _invalid(field: str):
    raise LocationError("VALIDATION_ERROR", "Mã hoặc địa chỉ hành chính không hợp lệ.", 422,
                        details=[{"field": field, "message": "Không hợp lệ hoặc không thuộc cấp cha đã chọn."}])


def _code(value, field: str, *, optional: bool = False):
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _invalid(field)
    return value


def _name(data: dict, code: int, field: str):
    for item in data["items"]:
        if item["code"] == code:
            return item["name"]
    _invalid(field)


def _clean(segment: str) -> str:
    return re.sub(r"\s+", " ", segment).strip()


def _unit_aliases(name: str) -> set[str]:
    aliases = {name.casefold()}
    short = re.sub(r"^(phường|xã|thị trấn|quận|huyện|thị xã|tỉnh|thành phố)\s+", "", name,
                   flags=re.IGNORECASE)
    aliases.add(short.casefold())
    return aliases


def normalize_address(address: dict) -> NormalizedAddress:
    """Resolve names by code and remove only complete, comma-delimited duplicate tails."""
    if not isinstance(address, dict):
        _invalid("address")
    version = address.get("administrative_version")
    if version not in ("legacy", "current"):
        _invalid("address.administrative_version")
    detail = address.get("detail")
    if not isinstance(detail, str) or not 1 <= len(detail) <= 300 or not detail.strip():
        _invalid("address.detail")
    segments = [_clean(part) for part in detail.split(",")]
    segments = [part for part in segments if part]
    if not segments:
        _invalid("address.detail")
    province_code = _code(address.get("province_code"), "address.province_code")
    ward_code = _code(address.get("ward_code"), "address.ward_code", optional=version == "legacy")

    if version == "legacy":
        district_code = _code(address.get("district_code"), "address.district_code")
        province = _name(catalog.legacy_provinces(), province_code, "address.province_code")
        district = _name(catalog.legacy_districts(province_code), district_code, "address.district_code")
        ward = (_name(catalog.legacy_wards(district_code), ward_code, "address.ward_code")
                if ward_code is not None else None)
    else:
        if address.get("district_code") is not None:
            _invalid("address.district_code")
        province = _name(catalog.current_provinces(), province_code, "address.province_code")
        district = None
        ward = _name(catalog.current_wards(province_code), ward_code, "address.ward_code")

    names = [name for name in (ward, district, province) if name]
    # A plain street or hamlet name is never dropped: only a full segment after a comma.
    for name in reversed(names):
        if len(segments) > 1 and segments[-1].casefold() in _unit_aliases(name):
            segments.pop()
    search_address = ", ".join([*segments, *names, "Việt Nam"])
    return NormalizedAddress(
        input_address=detail, search_address=search_address,
        administrative_version=version, province_name=province,
        district_name=district, ward_name=ward,
    )
