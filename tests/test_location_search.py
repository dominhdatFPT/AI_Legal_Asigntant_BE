"""End-to-end Location route tests with catalogue and Goong fully mocked."""
import importlib
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


SERVICE = Path(__file__).resolve().parents[1] / "services/location-service"
BODY = {"address": {"administrative_version": "current", "detail": "12 Lê Duẩn",
                    "province_code": 1, "district_code": None, "ward_code": 2},
        "query": "Trung tâm phục vụ hành chính công", "radius_meters": 10000, "limit": 5}


class FakeProvider:
    def __init__(self, module):
        self.module = module
        self.calls = []
        self.details = {}
        self.suggestions = []
        self.geocodes = [module.GeocodeCandidate("12 Lê Duẩn, Hà Nội", 21, 105,
                                                  {"province": "Hà Nội"}, {})]

    async def aclose(self):
        pass

    async def geocode(self, address):
        self.calls.append(("geocode", address))
        return self.geocodes

    async def search_places(self, query, latitude, longitude, radius_meters, limit):
        self.calls.append(("search", query, radius_meters, limit))
        return self.suggestions

    async def place_detail(self, place_id):
        self.calls.append(("detail", place_id))
        value = self.details[place_id]
        if isinstance(value, Exception):
            raise value
        return value


class LocationSearchTests(unittest.TestCase):
    def setUp(self):
        self.previous = {key: value for key, value in sys.modules.items()
                         if key == "app" or key.startswith("app.")}
        for key in self.previous:
            del sys.modules[key]
        sys.path.insert(0, str(SERVICE))
        self.main = importlib.import_module("app.main")
        self.goong = importlib.import_module("app.services.goong_client")
        self.errors = importlib.import_module("app.core.errors")
        self.catalog = importlib.import_module("app.services.administrative_catalog")
        self.offices = importlib.import_module("app.api.offices")
        self.auth = importlib.import_module("common_auth")
        self.main.app.dependency_overrides[self.offices.get_current_user] = lambda: self.auth.CurrentUser("u1", "citizen")
        self.provider = FakeProvider(self.goong)
        self.patches = [
            patch.object(self.catalog, "current_provinces", return_value={"items": [{"code": 1, "name": "Thành phố Hà Nội"}]}),
            patch.object(self.catalog, "current_wards", return_value={"items": [{"code": 2, "name": "Phường Trúc Bạch"}]}),
            patch.dict("os.environ", {"LOCATION_SEARCH_ENABLED": "true", "GOONG_API_KEY": "test-key"}),
        ]
        for item in self.patches:
            item.start()
        self.context = TestClient(self.main.app)
        self.client = self.context.__enter__()
        self.main.app.state.goong_client = self.provider

    def tearDown(self):
        self.context.__exit__(None, None, None)
        for item in reversed(self.patches):
            item.stop()
        self.main.app.dependency_overrides.clear()
        sys.path.remove(str(SERVICE))
        for key in [key for key in sys.modules if key == "app" or key.startswith("app.")]:
            del sys.modules[key]
        sys.modules.update(self.previous)

    def post(self, body=BODY):
        return self.client.post("/api/v1/locations/offices/search", json=body)

    def test_empty_and_validation_before_paid_call(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["items"], [])
        self.assertFalse(response.json()["data"]["is_partial"])
        self.provider.calls.clear()
        invalid = self.post({**BODY, "query": " "})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(self.provider.calls, [])

    def test_wrong_administrative_parent_and_disabled_search_never_call_provider(self):
        wrong = self.post({**BODY, "address": {**BODY["address"], "ward_code": 99}})
        self.assertEqual(wrong.status_code, 422)
        self.assertEqual(self.provider.calls, [])
        state = self.main.app.state
        state.location_settings = replace(state.location_settings, search_enabled=False)
        disabled = self.post()
        self.assertEqual(disabled.status_code, 503)
        self.assertEqual(disabled.json()["error"]["code"], "LOCATION_SEARCH_DISABLED")
        self.assertEqual(self.provider.calls, [])
        state.location_settings = replace(state.location_settings, search_enabled=True, goong_api_key="")
        no_key = self.post()
        self.assertEqual(no_key.json()["error"]["code"], "GOONG_NOT_CONFIGURED")
        self.assertEqual(self.provider.calls, [])

    def test_unauthenticated_search_rejected_with_safe_request_id(self):
        self.main.app.dependency_overrides.clear()
        response = self.client.post("/api/v1/locations/offices/search", json=BODY,
                                    headers={"X-Request-ID": "unsafe value / path"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "UNAUTHORIZED")
        self.assertEqual(response.json()["request_id"], response.headers["X-Request-ID"])
        self.assertTrue(response.json()["request_id"].startswith("req-"))
        self.assertEqual(self.provider.calls, [])

    def test_dedupe_sort_radius_and_directions(self):
        candidate = self.goong.PlaceCandidate
        detail = self.goong.PlaceDetail
        self.provider.suggestions = [candidate("far", "Far"), candidate("near", "Near"), candidate("near", "Near")]
        self.provider.details = {
            "far": detail("far", "Xa", "Địa chỉ xa", 21.2, 105),
            "near": detail("near", "Gần", "Địa chỉ gần", 21.001, 105),
        }
        data = self.post().json()["data"]
        self.assertEqual([item["place_id"] for item in data["items"]], ["near"])
        self.assertEqual(data["items"][0]["distance_type"], "straight_line")
        self.assertIn("destination=21.001%2C105", data["items"][0]["directions_url"])
        self.assertEqual([item for item in self.provider.calls if item[0] == "detail"],
                         [("detail", "far"), ("detail", "near")])

    def test_partial_and_total_detail_failure(self):
        candidate = self.goong.PlaceCandidate
        self.provider.suggestions = [candidate("bad", "Bad"), candidate("good", "Good")]
        self.provider.details = {
            "bad": self.errors.LocationError("GOONG_TIMEOUT", "timeout", 504),
            "good": self.goong.PlaceDetail("good", "Tốt", "Địa chỉ", 21.001, 105),
        }
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["data"]["is_partial"])
        self.assertEqual(len(response.json()["data"]["items"]), 1)
        self.provider.details["good"] = self.errors.LocationError("GOONG_TIMEOUT", "timeout", 504)
        response = self.post()
        self.assertEqual(response.status_code, 504)

    def test_ambiguous_address_never_searches_places(self):
        self.provider.geocodes.append(self.goong.GeocodeCandidate("Khác", 20, 105, {}, {}))
        response = self.post()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "ADDRESS_AMBIGUOUS")
        self.assertEqual([call[0] for call in self.provider.calls], ["geocode"])

    def test_per_user_rate_limit(self):
        for _ in range(5):
            self.assertEqual(self.post().status_code, 200)
        self.provider.calls.clear()
        response = self.post()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(self.provider.calls, [])


if __name__ == "__main__":
    unittest.main()
