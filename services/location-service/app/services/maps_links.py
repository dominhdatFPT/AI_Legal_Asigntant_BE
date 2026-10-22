"""Google Maps directions link built only from validated coordinates."""
from urllib.parse import urlencode

from app.services.goong_client import coordinates


def directions_url(latitude: float, longitude: float,
                   origin: tuple[float, float] | None = None) -> str:
    lat, lng = coordinates({"lat": latitude, "lng": longitude})
    params = {"api": "1", "destination": f"{lat:g},{lng:g}"}
    if origin is not None:
        o_lat, o_lng = coordinates({"lat": origin[0], "lng": origin[1]})
        params["origin"] = f"{o_lat:g},{o_lng:g}"
    return "https://www.google.com/maps/dir/?" + urlencode(params)
