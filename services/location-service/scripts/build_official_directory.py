"""Build a reviewable official-address snapshot; geocoding is explicit opt-in.

python scripts/build_official_directory.py --fetch-source
python scripts/build_official_directory.py --geocode
The second command uses GOONG_API_KEY. It never prints keys or provider URLs.
Rural hamlet-only addresses need a verified office pin and are not approximated.
"""
import argparse
import asyncio
from datetime import date
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "app/data/danang_public_service_centers.json"
SOURCE = "https://thangan.danang.gov.vn/chi-tiet-tin/group/156/nid/18559/dia-chi-tru-so-va-trung-tam-phuc-vu-hanh-chinh-cong-94-don-vi-cap-xa-cua-tp-da-nang-moi"


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def parse_source(html):
    parser = Text()
    parser.feed(html)
    text = " ".join(" ".join(parser.parts).split())
    rows = re.findall(r"(\d{1,2})\.\s*((?:Phường|Xã|Đặc khu)\s+[^:]+):\s*(.*?)(?=\s+\d{1,2}\.\s*(?:Phường|Xã|Đặc khu)|UBND TP.Đà Nẵng cũng công bố)", text)
    if [int(row[0]) for row in rows] != list(range(1, 95)):
        raise ValueError("Official source changed; review parser before importing")
    entries = []
    for number, unit, body in rows:
        # Hoang Sa lists an administrative office, not a public service center.
        if "Trung tâm Phục vụ hành chính công" not in body:
            continue
        address = body.rsplit("Trung tâm Phục vụ hành chính công tại ", 1)[-1]
        address = re.split(r"[.,]?\s*Số điện thoại:", address)[0].rstrip(". ")
        address = re.sub(r"^số\s+", "", address, flags=re.IGNORECASE)
        contact = re.search(r"Số điện thoại:\s*([0-9. ]+)", body)
        entries.append({"id": f"danang-public-service-{int(number):02d}",
                        "name": f"Trung tâm Phục vụ hành chính công {unit[0].lower() + unit[1:]}",
                        "unit_name": unit, "address": f"{address}, Đà Nẵng",
                        "coordinate_status": "needs_verified_office_pin",
                        "contact_phone": re.sub(r"\D", "", contact[1]) if contact else None,
                        "contact_label": "Liên hệ địa phương"})
    if len(entries) != 93:
        raise ValueError("Expected 93 explicitly listed public service centers")
    return {"province": "Đà Nẵng", "category": "public_service_center", "source_url": SOURCE,
            "source_published_at": "2025-07-14", "source_checked_at": date.today().isoformat(),
            "entries": entries}


def preserve_verified_office_pins(snapshot, previous):
    """Keep reviewed POIs only while the official unit and address are unchanged."""
    old = {entry["id"]: entry for entry in previous["entries"]}
    fields = ("coordinate_status", "latitude", "longitude", "coordinate_provider",
              "coordinate_source_url", "matched_poi_address", "coordinate_checked_at")
    for entry in snapshot["entries"]:
        prior = old.get(entry["id"])
        if (prior and prior["coordinate_status"] == "office_poi_match"
                and prior["name"] == entry["name"] and prior["address"] == entry["address"]):
            entry.update({field: prior[field] for field in fields})
    return snapshot


def key(value):
    value = unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
    tokens = re.findall(r"[a-z0-9]+", "".join(c for c in value if not unicodedata.combining(c)))
    return {str(int(token)) if token.isdigit() else token for token in tokens} - {"duong", "so"}


def street_address_matches(address, resolved):
    from app.services.location_identity import same_street_address
    return same_street_address(address, resolved)


async def geocode(snapshot):
    from app.core.config import LocationSettings
    from app.core.errors import LocationError
    from app.services.distance import haversine_meters
    from app.services.goong_client import GoongClient
    settings = LocationSettings.from_env()
    if not settings.goong_api_key:
        raise ValueError("GOONG_API_KEY is required")
    client = GoongClient(settings.goong_api_key, max_rps=settings.goong_max_rps,
                         daily_budget=settings.goong_daily_budget)
    try:
        for entry in snapshot["entries"]:
            if entry["coordinate_status"] == "office_poi_match":
                continue  # Preserve a reviewed office POI instead of a nearby street-number pin.
            if (entry["coordinate_status"] == "street_address_match"
                    and street_address_matches(entry["address"], entry["geocoded_address"])):
                continue
            address = entry["address"]
            # A hamlet/commune centroid is not a verified office location.
            if not re.match(r"\d+[A-Za-z]?\b", address):
                continue
            try:
                results = await client.geocode(address)
            except LocationError as exc:
                if exc.code in ("GOONG_ACCESS_DENIED", "GOONG_QUOTA_EXCEEDED"):
                    raise
                entry["coordinate_status"] = "geocode_unavailable"
                continue
            matches = [r for r in results if street_address_matches(address, r.formatted_address)
                       and 14.8 <= r.latitude <= 16.4 and 107 <= r.longitude <= 109]
            if len(matches) != 1 or any(haversine_meters(matches[0].latitude, matches[0].longitude,
                                                       r.latitude, r.longitude) > 100 for r in results):
                entry["coordinate_status"] = "needs_review"
                continue
            result = matches[0]
            entry.update(latitude=result.latitude, longitude=result.longitude,
                         coordinate_status="street_address_match", coordinate_provider="goong",
                         geocoded_address=result.formatted_address, geocoded_at=date.today().isoformat())
    finally:
        await client.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fetch-source", action="store_true")
    mode.add_argument("--geocode", action="store_true")
    args = parser.parse_args()
    if args.fetch_source:
        import httpx
        response = httpx.get(SOURCE, timeout=30, follow_redirects=True)
        response.raise_for_status()
        snapshot = parse_source(response.text)
        if OUTPUT.exists():
            snapshot = preserve_verified_office_pins(snapshot, json.loads(OUTPUT.read_text(encoding="utf-8")))
    else:
        snapshot = json.loads(OUTPUT.read_text(encoding="utf-8"))
        asyncio.run(geocode(snapshot))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"entries": len(snapshot["entries"]),
                      "located": sum(e["coordinate_status"] in {"street_address_match", "office_poi_match"}
                                     for e in snapshot["entries"])}))
