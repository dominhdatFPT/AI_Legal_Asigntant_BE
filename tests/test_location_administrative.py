"""HTTP regression tests for Location's public administrative catalogue."""
import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import URLError

from fastapi.testclient import TestClient


SERVICE = Path(__file__).resolve().parents[1] / "services/location-service"
PREFIX = "/api/v1/locations"


class AdministrativeApiTests(unittest.TestCase):
    def setUp(self):
        # The backend test suite imports several different packages named `app`.
        # Keep this service's modules only for the lifetime of this test.
        self.previous = {key: value for key, value in sys.modules.items()
                         if key == "app" or key.startswith("app.")}
        for key in self.previous:
            del sys.modules[key]
        sys.path.insert(0, str(SERVICE))
        self.main = importlib.import_module("app.main")
        self.client = TestClient(self.main.app)

    def tearDown(self):
        self.client.close()
        sys.path.remove(str(SERVICE))
        for key in [key for key in sys.modules if key == "app" or key.startswith("app.")]:
            del sys.modules[key]
        sys.modules.update(self.previous)

    def upstream(self, path, value):
        target = sys.modules.get("app.services.administrative_catalog", self.main)
        return patch.object(target, "_upstream", side_effect=lambda actual: value if actual == path else
                            self.fail(f"Unexpected upstream path: {actual}"))

    def test_health_payload_is_unchanged(self):
        response = self.client.get(PREFIX + "/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "location-service"})

    def test_all_five_public_catalogue_routes_preserve_items_and_source(self):
        cases = [
            ("/administrative/legacy/provinces", "/api/v1/p/", [{"code": 1, "name": "Hà Nội"}]),
            ("/administrative/legacy/provinces/1/districts", "/api/v1/p/1?depth=2",
             {"districts": [{"code": 2, "name": "Ba Đình"}]}),
            ("/administrative/legacy/districts/2/wards", "/api/v1/d/2?depth=2",
             {"wards": [{"code": 3, "name": "Phúc Xá"}]}),
            ("/administrative/current/provinces", "/api/v2/p/", [{"code": 1, "name": "Hà Nội"}]),
            ("/administrative/current/provinces/1/wards", "/api/v2/p/1?depth=2",
             {"wards": [{"code": 4, "name": "Ba Đình"}]}),
        ]
        for route, upstream_path, payload in cases:
            with self.subTest(route=route), self.upstream(upstream_path, payload):
                response = self.client.get(PREFIX + route)
                self.assertEqual(response.status_code, 200)
                expected_items = payload if isinstance(payload, list) else next(iter(payload.values()))
                self.assertEqual(response.json()["data"], {
                    "items": expected_items,
                    "source": "provinces.open-api.vn",
                })
                self.assertIsNone(response.json()["error"])

    def test_empty_catalogue_is_successful(self):
        with self.upstream("/api/v2/p/", []):
            response = self.client.get(PREFIX + "/administrative/current/provinces")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["items"], [])

    def test_bad_upstream_shape_fails_closed(self):
        with self.upstream("/api/v2/p/", [{"code": "1", "name": "Hà Nội"}]):
            response = self.client.get(PREFIX + "/administrative/current/provinces")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "ADMINISTRATIVE_SOURCE_BAD_RESPONSE")
        self.assertEqual(response.json()["request_id"], response.headers["X-Request-ID"])

    def test_upstream_failure_has_safe_envelope_without_provider_details(self):
        target = sys.modules["app.services.administrative_catalog"]
        target._cached_upstream.cache_clear()
        with patch.object(target, "urlopen", side_effect=URLError("private upstream detail")):
            response = self.client.get(PREFIX + "/administrative/legacy/provinces",
                                       headers={"X-Request-ID": "loc-req-1"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "ADMINISTRATIVE_SOURCE_BAD_RESPONSE")
        self.assertEqual(response.json()["request_id"], "loc-req-1")
        self.assertEqual(response.headers["X-Request-ID"], "loc-req-1")
        self.assertNotIn("private upstream detail", response.text)

    def test_success_echoes_safe_request_id_and_rejects_unsafe_header(self):
        with self.upstream("/api/v2/p/", []):
            response = self.client.get(PREFIX + "/administrative/current/provinces",
                                       headers={"X-Request-ID": "loc-req-2"})
            unsafe = self.client.get(PREFIX + "/administrative/current/provinces",
                                     headers={"X-Request-ID": "unsafe value / path"})
        self.assertEqual(response.json()["request_id"], "loc-req-2")
        self.assertEqual(response.headers["X-Request-ID"], "loc-req-2")
        self.assertTrue(unsafe.json()["request_id"].startswith("req-"))
        self.assertEqual(unsafe.json()["request_id"], unsafe.headers["X-Request-ID"])

    def test_bad_path_parameter_has_standard_validation_error(self):
        response = self.client.get(PREFIX + "/administrative/current/provinces/not-a-code/wards")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(response.json()["request_id"], response.headers["X-Request-ID"])

    def test_office_search_requires_auth(self):
        response = self.client.post(PREFIX + "/offices/search", json={})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "UNAUTHORIZED")


if __name__ == "__main__":
    unittest.main()
