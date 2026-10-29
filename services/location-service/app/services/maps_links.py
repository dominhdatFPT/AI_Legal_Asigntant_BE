"""Google Maps links preserving address labels and coordinate precision."""
from urllib.parse import urlencode

from app.services.goong_client import coordinates
import re


def directions_url(latitude: float, longitude: float,
                   origin: tuple[float, float] | None = None,
                   destination_name: str | None = None,
                   origin_address: str | None = None,
                   destination_address: str | None = None,
                   google_place_id: str | None = None) -> str:
    lat, lng = coordinates({"lat": latitude, "lng": longitude})
    if destination_name is not None and (not isinstance(destination_name, str)
                                         or not 1 <= len(destination_name.strip()) <= 200):
        raise ValueError("Invalid verified map destination name")
    if origin_address is not None and (not isinstance(origin_address, str)
                                       or not 1 <= len(origin_address.strip()) <= 500):
        raise ValueError("Invalid map origin address")
    if destination_address is not None and (not isinstance(destination_address, str)
                                            or not 1 <= len(destination_address.strip()) <= 500):
        raise ValueError("Invalid destination address")
    if google_place_id is not None and not re.fullmatch(r"ChIJ[A-Za-z0-9_-]{10,200}", google_place_id):
        raise ValueError("Invalid Google place ID")
    # A Goong coordinate is not a Google establishment ID: directions to a bare
    # coordinate can be relabelled as an unrelated business. Carry the card's
    # exact address, and pin a Google identity only after it has been reviewed.
    label = ", ".join(v.strip() for v in (destination_name, destination_address) if v)
    params = {"api": "1", "destination": label or f"{lat:.7f},{lng:.7f}"}
    if google_place_id:
        params["destination_place_id"] = google_place_id
    if origin is not None:
        o_lat, o_lng = coordinates({"lat": origin[0], "lng": origin[1]})
        params["origin"] = origin_address.strip() if origin_address else f"{o_lat:.7f},{o_lng:.7f}"
    return "https://www.google.com/maps/dir/?" + urlencode(params)


def location_urls(name, address, latitude, longitude, google_place_id=None):
    """Use exactly the same destination identity for search, preview and routing."""
    coordinates({"lat": latitude, "lng": longitude})
    # Without a reviewed Google identity, an office name can make Maps select a
    # similarly named POI at another address. Pin the visible street address and
    # leave name discovery to a separate web-search action in the frontend.
    query = f"{name}, {address}" if google_place_id else address
    params = {"api": "1", "query": query}
    if google_place_id:
        if not re.fullmatch(r"ChIJ[A-Za-z0-9_-]{10,200}", google_place_id):
            raise ValueError("Invalid Google place ID")
        params["query_place_id"] = google_place_id
    return {"maps_url": "https://www.google.com/maps/search/?" + urlencode(params),
            "map_embed_url": "https://www.google.com/maps?" + urlencode({"q": query, "z": 17, "output": "embed"})}
