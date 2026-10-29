"""One bounded office search: geocode, discover, dedupe, detail, distance."""
import asyncio
import re
import unicodedata
from types import SimpleNamespace

from app.core.errors import LocationError
from app.services.address_normalizer import normalize_address
from app.services.distance import haversine_meters
from app.services.maps_links import directions_url, location_urls
from app.services.location_identity import same_street_address, street_identity
from app.services.official_directory import LOCATED_STATUSES, load_directory, load_profiles, load_verified_offices, load_location_conflicts


def _province_key(value: str) -> str:
    value = _query_key(value)
    value = re.sub(r"^(?:tinh|thanh pho|tp|t p)\s+", "", value)
    value = re.sub(r"\s+(?:city|\d{5,6})$", "", value)
    return {"hcm": "ho chi minh", "tphcm": "ho chi minh"}.get(value, value)


def _query_key(value: str) -> str:
    value = unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", "".join(
        char for char in value if not unicodedata.combining(char))).split())


QUERY_ALIASES = (
    ("Trung tâm phục vụ hành chính công", "Trung tâm hành chính công"),
    ("Văn phòng đăng ký kinh doanh", "Phòng đăng ký kinh doanh"),
)
MAX_DETAIL_CANDIDATES = 30


def query_alternatives(query: str) -> list[str]:
    key = _query_key(query)
    for aliases in QUERY_ALIASES:
        for alias in aliases:
            base = _query_key(alias)
            if key == base or key.startswith(base + " "):
                suffix = key[len(base):].strip()
                return [f"{value} {suffix}".strip() for value in aliases]
    return [query]


def matches_province(detail, province: str) -> bool:
    """Require current province evidence, never a name/address substring match."""
    segments = [part.strip() for part in detail.address.split(",") if part.strip()]
    while segments and _query_key(segments[-1]) in {"viet nam", "vietnam", "vn"}:
        segments.pop()
    declared = getattr(detail, "province", None)
    if declared:
        if _province_key(declared) != _province_key(province):
            return False
        # V2 formatted addresses are current and province-qualified; conflicting
        # metadata must not silently override an explicitly different address.
        return len(segments) < 2 or _province_key(segments[-1]) == _province_key(province)
    return bool(segments) and _province_key(segments[-1]) == _province_key(province)


def discovery_queries(query: str, province: str, locality: str | None = None) -> list[str]:
    """Localize first; only expand known equivalent names, never office types."""
    query = " ".join(query.split())
    province = re.sub(r"^(tỉnh|thành phố)\s+", "", province, flags=re.IGNORECASE).strip()
    key = _query_key(query)

    def localize(value):
        return value if f" {_query_key(province)} " in f" {_query_key(value)} " else f"{value}, {province}"

    def localize_area(value):
        if not locality:
            return localize(value)
        area = re.sub(r"^(phường|xã|thị trấn|quận|huyện|thị xã)\s+", "", locality,
                      flags=re.IGNORECASE).strip()
        return f"{value}, {area}, {province}"

    # The selected ward is the strongest hint. Province-only and raw fallbacks
    # remain for providers whose POI name still uses an older administrative name.
    queries = [localize_area(query), localize(query)]
    # Preserve suffixes (e.g. ward or specific office) when expanding known names.
    for alternative in query_alternatives(query):
        if _query_key(alternative) != key:
            queries.extend((localize_area(alternative), localize(alternative)))
    queries.append(query)  # Legacy place names may not contain the current province.
    return list(dict.fromkeys(queries))


def _administrative_value(value, field):
    data = value if isinstance(value, dict) else {}
    item = data.get(field)
    return item.strip() if isinstance(item, str) and item.strip() else None


def same_historical_region(origin, detail=None, geocodes=()) -> bool:
    """Keep post-merger searches inside the user's former province when known.

    Current addresses can legitimately label both central HCMC and former Bình
    Dương as Hồ Chí Minh. Goong exposes the pre-merger unit separately; comparing
    it prevents a Quận 1 search from leaking into Thủ Dầu Một or Bến Cát.
    """
    expected = _administrative_value(getattr(origin, "deprecated_compound", None), "province")
    if not expected:
        return True
    observed = []
    if detail is not None:
        value = _administrative_value(getattr(detail, "deprecated_compound", None), "province")
        if value:
            observed.append(value)
    for candidate in geocodes:
        value = _administrative_value(getattr(candidate, "deprecated_compound", None), "province")
        if value:
            observed.append(value)
    return not observed or all(_province_key(value) == _province_key(expected) for value in observed)


def matches_query(detail, query: str) -> bool:
    """Reject fuzzy suggestions that do not contain the requested office terms."""
    def words(value):
        return set(_query_key(value).replace("ubnd", "uy ban nhan dan").split())

    alternatives = query_alternatives(query)
    business_prefixes = ("van phong dang ky kinh doanh", "phong dang ky kinh doanh", "dang ky kinh doanh")
    if any(_query_key(query).startswith(prefix) for prefix in business_prefixes):
        if not re.match(r"^(?:chi nhanh )?(?:van )?phong dang ky kinh doanh(?: |$)", _query_key(detail.name)):
            return False
    name_words = words(detail.name)
    available = words(f"{detail.name} {detail.address}")
    categories = ("trung tam hanh chinh cong", "trung tam phuc vu hanh chinh cong",
                  "van phong cong chung", "van phong dang ky dat dai", "uy ban nhan dan",
                  "phong dang ky kinh doanh", "van phong dang ky kinh doanh", "chi cuc thue")
    for value in alternatives:
        category = next((c for c in categories if _query_key(value).startswith(c)), None)
        if category and words(category) <= name_words and words(value) <= available:
            return True
        if not category and words(value) <= name_words:
            return True
    return False


def attach_location(item, origin, destination_name=None, google_place_id=None):
    """All links are derived from the final card, after source/address corrections."""
    item["map_destination_name"] = destination_name or item["name"]
    item["google_place_id"] = google_place_id
    item["directions_url"] = directions_url(item["latitude"], item["longitude"],
        (origin.latitude, origin.longitude),
        item["map_destination_name"] if google_place_id else None,
        getattr(origin, "formatted_address", None), item["address"], google_place_id)
    item.update(location_urls(item["map_destination_name"], item["address"],
        item["latitude"], item["longitude"], google_place_id))
    return item


def apply_reviewed_profile(item, origin):
    """Reuse reviewed office identity/hours across UBND and public-centre searches."""
    name = _query_key(item["name"]).replace("ubnd", "uy ban nhan dan")
    directory = load_directory()
    candidates = list(load_verified_offices())
    if _province_key(item.get("province", "")) == _province_key(directory["province"]):
        profiles = load_profiles()
        candidates.extend({**entry, **profiles[entry["id"]], "province": directory["province"],
                           "source_url": directory["source_url"],
                           "source_checked_at": directory["source_checked_at"]}
                          for entry in directory["entries"] if entry["id"] in profiles)
    for entry in candidates:
        if (_province_key(entry["province"]) != _province_key(item.get("province", ""))
                or not same_street_address(item["address"], entry["address"])):
            continue
        aliases = (entry["name"], entry.get("map_destination_name", ""))
        if not any(name == _query_key(alias).replace("ubnd", "uy ban nhan dan") for alias in aliases):
            continue
        for field in ("source_url", "source_checked_at", "opening_hours", "contact_phone",
                      "contact_label", "contact_source_url", "website_url", "photo"):
            if entry.get(field) is not None:
                item[field] = entry[field]
        if entry.get("google_place_id"):
            item["location_verification"] = "official_address_and_google_place"
        return attach_location(item, origin, entry.get("map_destination_name"), entry.get("google_place_id"))
    return attach_location(item, origin)


def verified_results(address, origin, query):
    query_key = _query_key(query)
    def applies(entry):
        if _province_key(entry["province"]) != _province_key(address.province_name):
            return False
        for alias in entry["queries"]:
            base = _query_key(alias)
            if query_key == base:
                return True
            if query_key.startswith(base + " "):
                suffix = set(query_key[len(base):].split())
                if suffix <= set(_query_key(entry["name"] + ' ' + entry["address"]).split()):
                    return True
        return False
    entries = [e for e in load_verified_offices() if applies(e)]
    if not entries:
        return None
    items = []
    for entry in entries:
        item = {k: v for k, v in entry.items() if k not in {"id", "queries"}}
        item.update(provider="official_directory", place_id=entry["id"],
                    location_verification=("official_address_and_google_place"
                                           if entry.get("google_place_id") else "official_address_geocoded"),
                    distance_meters=haversine_meters(origin.latitude, origin.longitude, entry["latitude"], entry["longitude"]),
                    distance_type="straight_line")
        items.append(attach_location(item, origin, entry.get("map_destination_name"),
                                     entry.get("google_place_id")))
    items.sort(key=lambda i: (i["distance_meters"], i["place_id"]))
    return {"items": items[:5], "warnings": [], "is_partial": False,
            "coverage": {"source": "official_directory", "province": entries[0]["province"]}}


def official_results(address, origin, query):
    """Rank official centers, including centers missing from the map POI index."""
    center_terms = set("trung tam hanh chinh cong".split())
    if not center_terms <= set(_query_key(query).split()):
        return None
    data = load_directory()
    province = address.province_name
    if getattr(address, "administrative_version", "current") == "legacy":
        province = origin.compound.get("province") or province
    if _province_key(province) != _province_key(data["province"]):
        return None
    matches = [e for e in data["entries"] if matches_query(SimpleNamespace(**e), query)]
    located = [e for e in matches if e["coordinate_status"] in LOCATED_STATUSES]
    located = [e for e in located if e["coordinate_status"] == "office_poi_match"
               or same_street_address(e["address"], e.get("geocoded_address", ""))]
    profiles = load_profiles()
    items = []
    for entry in located:
        lat, lng = entry["latitude"], entry["longitude"]
        profile = profiles.get(entry["id"], {})
        items.append({"provider": "official_directory", "place_id": entry["id"],
                      "name": entry["name"], "address": entry["address"],
                      "latitude": lat, "longitude": lng,
                      "distance_meters": haversine_meters(origin.latitude, origin.longitude, lat, lng),
                      "distance_type": "straight_line",
                      "directions_url": directions_url(lat, lng, (origin.latitude, origin.longitude),
                                                       profile.get("directions_name"),
                                                       getattr(origin, "formatted_address", None)),
                      "source_url": data["source_url"],
                      "source_published_at": data["source_published_at"],
                      "source_checked_at": data["source_checked_at"]})
        items[-1].update(unit_name=entry["unit_name"], category_label="Trung tâm phục vụ hành chính công",
                         contact_phone=profile.get("contact_phone"),
                         contact_label=profile.get("contact_label", "Liên hệ cơ quan"),
                         contact_source_url=profile.get("contact_source_url"),
                         website_url=profile.get("website_url"),
                         appointment_url=profile.get("appointment_url"),
                         appointment_source_url=profile.get("appointment_source_url"),
                         photo=profile.get("photo"), opening_hours=profile.get("opening_hours"),
                         maps_url=profile.get("maps_url"), map_embed_url=profile.get("map_embed_url"))
        items[-1]["location_verification"] = "official_address_and_google_place" if profile.get("google_place_id") else "official_address_geocoded"
        attach_location(items[-1], origin, profile.get("map_destination_name"), profile.get("google_place_id"))
        # Only a reviewed establishment preview may override the shared address query.
        if profile.get("google_place_id") and profile.get("map_embed_url"):
            items[-1]["map_embed_url"] = profile["map_embed_url"]
    items.sort(key=lambda item: (item["distance_meters"], item["place_id"]))
    missing = len(matches) - len(located)
    return {"items": items[:5], "is_partial": bool(missing),
            "warnings": ([{"code": "OFFICIAL_COORDINATES_INCOMPLETE",
                           "message": f"Danh mục chính thức còn {missing} cơ quan chưa xác minh tọa độ; kết quả chỉ xếp theo các địa chỉ đã định vị."}]
                         if missing else []),
            "coverage": {"source": "official_directory", "province": data["province"],
                         "matched_offices": len(matches), "located_offices": len(located),
                         "source_url": data["source_url"], "checked_at": data["source_checked_at"]}}


async def search_offices(client, request: dict) -> dict:
    address = await asyncio.to_thread(normalize_address, request["address"])
    query = request["query"].strip()
    candidates = await client.geocode(address.search_address)
    if not candidates:
        raise LocationError("ADDRESS_NOT_FOUND", "Không định vị được địa chỉ đã nhập.", 422)
    if street_identity(address.input_address):
        candidates = [c for c in candidates if same_street_address(address.input_address, c.formatted_address)]
        if not candidates:
            raise LocationError("ADDRESS_AMBIGUOUS", "Bản đồ chưa khớp số nhà và tên đường đã nhập. Vui lòng kiểm tra địa chỉ.", 422)
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

    origin_data = {"input_address": address.search_address,
                   "resolved_address": origin.formatted_address,
                   "latitude": origin.latitude, "longitude": origin.longitude}
    official = verified_results(address, origin, query) or official_results(address, origin, query)
    if official is not None:
        return {"origin": origin_data, **official}

    items = []
    failures = []
    completed_details = 0
    outside_province = 0
    outside_historical_region = 0
    unverified_locations = 0
    address_checks = {}
    conflicts = {(_query_key(e["name"]), _province_key(e["province"])) for e in load_location_conflicts()}
    province = origin.compound.get("province") or address.province_name
    seen = set()
    for discovery_query in discovery_queries(query, address.province_name, address.ward_name):
        if completed_details >= MAX_DETAIL_CANDIDATES:
            break
        # Merge localized synonyms before ranking, so an earlier distant match
        # does not hide a closer office under an equivalent name.
        try:
            suggestions = await client.search_places(discovery_query, origin.latitude, origin.longitude, 10)
        except LocationError as exc:
            if exc.code in ("GOONG_ACCESS_DENIED", "GOONG_QUOTA_EXCEEDED"):
                raise
            failures.append(exc)
            continue
        # Autocomplete can ignore the geographic bias. Always verify actual coordinates.
        for suggestion in suggestions[:10]:
            if completed_details >= MAX_DETAIL_CANDIDATES:
                break
            place_id = suggestion.place_id
            if place_id in seen:
                continue
            seen.add(place_id)
            try:
                detail = await client.place_detail(place_id)
            except LocationError as exc:
                if exc.code in ("GOONG_ACCESS_DENIED", "GOONG_QUOTA_EXCEEDED"):
                    raise
                failures.append(exc)
                continue
            completed_details += 1
            if not matches_province(detail, province):
                outside_province += 1
                continue
            if not same_historical_region(origin, detail):
                outside_historical_region += 1
                continue
            if not matches_query(detail, query):
                continue
            if (_query_key(detail.name), _province_key(province)) in conflicts:
                unverified_locations += 1
                continue
            # A map POI can retain an old address or point to a different tenant.
            # Independently geocode the claimed street before using its coordinates.
            identity = street_identity(detail.address)
            if not identity:
                unverified_locations += 1
                continue
            address_key = (identity, _province_key(province))
            if address_key not in address_checks:
                try:
                    checks = await client.geocode(detail.address)
                    checks = [c for c in checks if same_street_address(detail.address, c.formatted_address)
                              and (not c.compound.get("province") or _province_key(c.compound["province"]) == _province_key(province))]
                    address_checks[address_key] = checks
                except LocationError as exc:
                    if exc.code in ("GOONG_ACCESS_DENIED", "GOONG_QUOTA_EXCEEDED"):
                        raise
                    failures.append(exc)
                    address_checks[address_key] = []
            checks = address_checks[address_key]
            if not same_historical_region(origin, detail, checks):
                outside_historical_region += 1
                continue
            if (not checks or any(haversine_meters(detail.latitude, detail.longitude, c.latitude, c.longitude) > 150 for c in checks)):
                unverified_locations += 1
                continue
            distance = haversine_meters(origin.latitude, origin.longitude, detail.latitude, detail.longitude)
            items.append({
                "provider": "goong", "place_id": detail.place_id, "name": detail.name,
                "address": detail.address, "latitude": detail.latitude, "longitude": detail.longitude,
                "province": detail.province or province,
                "distance_meters": distance, "distance_type": "straight_line",
                "directions_url": directions_url(detail.latitude, detail.longitude,
                                                   (origin.latitude, origin.longitude),
                                                   origin_address=origin.formatted_address),
            })
            items[-1]["location_verification"] = "provider_address_geocoded"
            apply_reviewed_profile(items[-1], origin)
    if failures and completed_details == 0:
        raise failures[0]
    items.sort(key=lambda item: (item["distance_meters"], item["place_id"]))
    unique_items = []
    for item in items:
        name = _query_key(item["name"]).replace("ubnd", "uy ban nhan dan")
        if any(name == _query_key(other["name"]).replace("ubnd", "uy ban nhan dan")
               and haversine_meters(item["latitude"], item["longitude"], other["latitude"], other["longitude"]) <= 25
               for other in unique_items):
            continue
        unique_items.append(item)
    return {
        "origin": origin_data,
        "coverage": {"source": "goong_candidates", "province": province,
                     "excluded_outside_province": outside_province,
                     "excluded_outside_historical_region": outside_historical_region,
                     "excluded_unverified_locations": unverified_locations},
        "items": unique_items[:5], "is_partial": bool(failures),
        "warnings": ([{"code": "GOONG_PARTIAL_RESULTS", "message": "Một số địa điểm chưa thể tải chi tiết."}]
                     if failures else []) + ([{"code": "UNVERIFIED_LOCATIONS_EXCLUDED",
                         "message": "Một số địa điểm được bỏ qua vì chưa đối chiếu được số nhà, tên đường hoặc vị trí."}]
                         if unverified_locations else []) + ([{"code": "LOCAL_RESULTS_UNAVAILABLE",
                         "message": f"Chưa có địa điểm phù hợp đã xác minh gần khu vực đã chọn tại {province}. Gợi ý ở khu vực hành chính khác không được hiển thị."}]
                         if not items else []),
    }


async def bounded_search(client, request: dict, timeout: float) -> dict:
    try:
        return await asyncio.wait_for(search_offices(client, request), timeout=timeout)
    except asyncio.TimeoutError:
        raise LocationError("GOONG_TIMEOUT", "Tìm kiếm quá thời gian cho phép.", 504) from None
