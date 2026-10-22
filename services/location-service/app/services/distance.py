"""Great-circle distance in meters, not driving distance."""
import math


def haversine_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    a1, a2 = math.radians(lat1), math.radians(lat2)
    dlat, dlng = a2 - a1, math.radians(lng2 - lng1)
    h = math.sin(dlat / 2) ** 2 + math.cos(a1) * math.cos(a2) * math.sin(dlng / 2) ** 2
    return round(6_371_000 * 2 * math.asin(min(1, math.sqrt(h))))
