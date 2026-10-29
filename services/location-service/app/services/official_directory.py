"""Versioned official office addresses with reviewed street-level coordinates.

No network requests at search time. Adding provinces requires a sourced snapshot,
not guesses from an autocomplete name or a commune centroid.
"""
from functools import lru_cache
import json
import math
from pathlib import Path


DIRECTORY = Path(__file__).resolve().parents[1] / "data/danang_public_service_centers.json"
PROFILES = DIRECTORY.with_name("office_profiles.json")
VERIFIED_OFFICES = DIRECTORY.with_name("verified_office_locations.json")
CONFLICTS = DIRECTORY.with_name("office_location_conflicts.json")
LOCATED_STATUSES = {"street_address_match", "office_poi_match"}


@lru_cache(maxsize=1)
def load_profiles():
    return json.loads(PROFILES.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_verified_offices():
    data = json.loads(VERIFIED_OFFICES.read_text(encoding="utf-8"))
    for entry in data["entries"]:
        google_place_id = entry.get("google_place_id")
        if (not entry.get("source_url", "").startswith("https://")
                or (google_place_id is not None and not google_place_id.startswith("ChIJ"))
                or not entry.get("address") or not entry.get("name")
                or not 8 <= entry["latitude"] <= 24 or not 102 <= entry["longitude"] <= 110):
            raise ValueError("Invalid verified office location")
    return data["entries"]


@lru_cache(maxsize=1)
def load_location_conflicts():
    return json.loads(CONFLICTS.read_text(encoding="utf-8"))["entries"]


@lru_cache(maxsize=1)
def load_directory():
    data = json.loads(DIRECTORY.read_text(encoding="utf-8"))
    identifiers = set()
    for entry in data["entries"]:
        if entry["id"] in identifiers or not entry["name"] or not entry["address"]:
            raise ValueError("Invalid official directory entry")
        identifiers.add(entry["id"])
        if entry["coordinate_status"] in LOCATED_STATUSES:
            lat, lng = entry["latitude"], entry["longitude"]
            if (isinstance(lat, bool) or isinstance(lng, bool)
                    or not isinstance(lat, (int, float)) or not isinstance(lng, (int, float))
                    or not math.isfinite(lat) or not math.isfinite(lng)
                    or not 14.8 <= lat <= 16.4 or not 107 <= lng <= 109):
                raise ValueError("Invalid official office coordinates")
    return data
