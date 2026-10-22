"""L4: versioned catalogue cache, parent validation and safe address assembly."""
import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


SERVICE = Path(__file__).resolve().parents[1] / "services/location-service"


class AddressTests(unittest.TestCase):
    def setUp(self):
        self.previous = {key: value for key, value in sys.modules.items()
                         if key == "app" or key.startswith("app.")}
        for key in self.previous:
            del sys.modules[key]
        sys.path.insert(0, str(SERVICE))
        self.catalog = importlib.import_module("app.services.administrative_catalog")
        self.normalizer = importlib.import_module("app.services.address_normalizer")
        self.errors = importlib.import_module("app.core.errors")
        self.catalog._cached_upstream.cache_clear()
        self.patches = [
            patch.object(self.catalog, "legacy_provinces", return_value={"items": [{"code": 1, "name": "Tỉnh Hưng Yên"}]}),
            patch.object(self.catalog, "legacy_districts", side_effect=lambda code: {"items": [{"code": 2, "name": "Huyện Tiên Lữ"}]} if code == 1 else {"items": []}),
            patch.object(self.catalog, "legacy_wards", side_effect=lambda code: {"items": [{"code": 3, "name": "Thị trấn Vương"}]} if code == 2 else {"items": []}),
            patch.object(self.catalog, "current_provinces", return_value={"items": [{"code": 4, "name": "Thành phố Đà Nẵng"}]}),
            patch.object(self.catalog, "current_wards", side_effect=lambda code: {"items": [{"code": 5, "name": "Phường Hòa Minh"}]} if code == 4 else {"items": []}),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        sys.path.remove(str(SERVICE))
        for key in [key for key in sys.modules if key == "app" or key.startswith("app.")]:
            del sys.modules[key]
        sys.modules.update(self.previous)

    def test_legacy_ward_can_be_absent_and_hamlet_is_preserved(self):
        result = self.normalizer.normalize_address({
            "administrative_version": "legacy", "detail": "Khu Tiên Xá, Thị trấn Vương",
            "province_code": 1, "district_code": 2, "ward_code": None,
        })
        self.assertEqual(result.search_address, "Khu Tiên Xá, Thị trấn Vương, Huyện Tiên Lữ, Tỉnh Hưng Yên, Việt Nam")
        self.assertEqual(result.input_address, "Khu Tiên Xá, Thị trấn Vương")

    def test_current_removes_only_full_trailing_ward_segment(self):
        result = self.normalizer.normalize_address({
            "administrative_version": "current", "detail": "16 Hòa Mỹ 3, Hòa Minh",
            "province_code": 4, "ward_code": 5,
        })
        self.assertEqual(result.search_address, "16 Hòa Mỹ 3, Phường Hòa Minh, Thành phố Đà Nẵng, Việt Nam")

    def test_street_name_that_matches_ward_is_kept(self):
        result = self.normalizer.normalize_address({
            "administrative_version": "current", "detail": "Đường Phường Hòa Minh",
            "province_code": 4, "ward_code": 5,
        })
        self.assertEqual(result.search_address, "Đường Phường Hòa Minh, Phường Hòa Minh, Thành phố Đà Nẵng, Việt Nam")

    def test_rejects_wrong_parent_and_mixed_versions(self):
        cases = [
            {"administrative_version": "legacy", "detail": "1", "province_code": 1, "district_code": 99, "ward_code": 3},
            {"administrative_version": "legacy", "detail": "1", "province_code": 1, "district_code": 2, "ward_code": 5},
            {"administrative_version": "current", "detail": "1", "province_code": 4, "district_code": 2, "ward_code": 5},
            {"administrative_version": "current", "detail": "1", "province_code": 4, "ward_code": 3},
        ]
        for address in cases:
            with self.subTest(address=address), self.assertRaises(self.errors.LocationError) as raised:
                self.normalizer.normalize_address(address)
            self.assertEqual(raised.exception.status, 422)

    def test_cache_expires_and_does_not_store_failures(self):
        self.catalog._cached_upstream.cache_clear()
        with patch.object(self.catalog, "urlopen") as open_url, patch.object(self.catalog.time, "monotonic", side_effect=[0, 10, 86401]):
            response = open_url.return_value.__enter__.return_value
            response.headers.get_content_type.return_value = "application/json"
            response.read.return_value = b"[]"
            self.assertEqual(self.catalog._upstream("/api/v1/p/"), [])
            self.assertEqual(self.catalog._upstream("/api/v1/p/"), [])
            self.assertEqual(self.catalog._upstream("/api/v1/p/"), [])
            self.assertEqual(open_url.call_count, 2)
        self.catalog._cached_upstream.cache_clear()
        with patch.object(self.catalog, "urlopen", side_effect=[ValueError(), None]) as open_url:
            with self.assertRaises(self.errors.LocationError):
                self.catalog._upstream("/api/v1/p/")
            self.assertEqual(open_url.call_count, 1)


if __name__ == "__main__":
    unittest.main()
