"""Mocked Goong V2 status, schema, timeout and quota tests."""
import asyncio
import importlib
from pathlib import Path
import sys
import unittest

import httpx


SERVICE = Path(__file__).resolve().parents[1] / "services/location-service"


class GoongTests(unittest.TestCase):
    def setUp(self):
        self.previous = {key: value for key, value in sys.modules.items()
                         if key == "app" or key.startswith("app.")}
        for key in self.previous:
            del sys.modules[key]
        sys.path.insert(0, str(SERVICE))
        self.module = importlib.import_module("app.services.goong_client")
        self.errors = importlib.import_module("app.core.errors")

    def tearDown(self):
        sys.path.remove(str(SERVICE))
        for key in [key for key in sys.modules if key == "app" or key.startswith("app.")]:
            del sys.modules[key]
        sys.modules.update(self.previous)

    def call(self, handler, operation="geocode"):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://rsapi.goong.io") as http:
                client = self.module.GoongClient("secret-key", client=http, max_rps=100)
                return await getattr(client, operation)("Hà Nội")
        return asyncio.run(run())

    def test_success_and_empty(self):
        def ok(request):
            self.assertEqual(request.url.path, "/v2/geocode")
            self.assertEqual(request.url.params["api_key"], "secret-key")
            return httpx.Response(200, json={"status": "OK", "results": [{
                "formatted_address": "Hà Nội", "geometry": {"location": {"lat": 21.0, "lng": 105.0}}
            }]})
        self.assertEqual(self.call(ok)[0].latitude, 21)
        self.assertEqual(self.call(lambda _: httpx.Response(200, json={"status": "ZERO_RESULTS", "results": []})), [])

    def test_access_quota_status_and_invalid_json(self):
        cases = [
            (httpx.Response(403), "GOONG_ACCESS_DENIED"),
            (httpx.Response(429), "GOONG_QUOTA_EXCEEDED"),
            (httpx.Response(200, json={"status": "REQUEST_DENIED"}), "GOONG_ACCESS_DENIED"),
            (httpx.Response(200, json={"status": "OK", "results": "wrong"}), "GOONG_BAD_RESPONSE"),
            (httpx.Response(200, text="not-json"), "GOONG_BAD_RESPONSE"),
            (httpx.Response(200, json={"status": "OK", "results": [{"formatted_address": "x", "geometry": {"location": {"lat": 999, "lng": 105}}}]}), "GOONG_BAD_RESPONSE"),
        ]
        for response, code in cases:
            with self.subTest(code=code), self.assertRaises(self.errors.LocationError) as raised:
                self.call(lambda _: response)
            self.assertEqual(raised.exception.code, code)
            self.assertNotIn("secret-key", raised.exception.message)

    def test_temporary_5xx_retries_only_twice(self):
        calls = []
        def handler(request):
            calls.append(request)
            if len(calls) < 3:
                return httpx.Response(503)
            return httpx.Response(200, json={"status": "OK", "results": []})
        self.assertEqual(self.call(handler), [])
        self.assertEqual(len(calls), 3)

    def test_autocomplete_without_radius_coordinates_limit_and_schema(self):
        async def run(payload):
            def handler(request):
                self.assertEqual(request.url.path, "/v2/place/autocomplete")
                self.assertNotIn("radius", request.url.params)
                self.assertEqual(request.url.params["location"], "16.05,108.16")
                self.assertEqual(request.url.params["limit"], "10")
                return httpx.Response(200, json=payload)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://rsapi.goong.io") as http:
                client = self.module.GoongClient("secret-key", client=http, max_rps=100)
                return await client.search_places("Trung tâm, Đà Nẵng", 16.05, 108.16, 10)
        self.assertEqual(asyncio.run(run({"status": "ZERO_RESULTS"})), [])
        self.assertEqual(len(asyncio.run(run({"status": "OK", "predictions": [
            {"place_id": str(i), "description": "Office"} for i in range(15)]}))), 10)
        for predictions in [None, {}, [{"place_id": ""}], [None]]:
            with self.subTest(predictions=predictions), self.assertRaises(self.errors.LocationError):
                asyncio.run(run({"status": "OK", "predictions": predictions}))

    def test_timeout_and_network_error_retry_then_fail_safely(self):
        for exception, code in [(httpx.ReadTimeout("secret-key"), "GOONG_TIMEOUT"),
                                (httpx.ConnectError("secret-key"), "GOONG_UNAVAILABLE")]:
            calls = []
            def handler(request):
                calls.append(request)
                raise exception
            with self.subTest(code=code), self.assertRaises(self.errors.LocationError) as raised:
                self.call(handler)
            self.assertEqual(raised.exception.code, code)
            self.assertNotIn("secret-key", raised.exception.message)
            self.assertEqual(len(calls), 3)

    def test_place_detail_preserves_current_province_and_rejects_malformed_compound(self):
        async def run(compound):
            payload = {"status": "OK", "result": {"place_id": "id", "name": "Phòng Đăng Ký Kinh Doanh",
                "formatted_address": "Hải Châu, Đà Nẵng", "geometry": {"location": {"lat": 16, "lng": 108}},
                "compound": compound}}
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)), base_url="https://rsapi.goong.io") as http:
                return await self.module.GoongClient("secret-key", client=http).place_detail("id")
        self.assertEqual(asyncio.run(run({"province": "Đà Nẵng"})).province, "Đà Nẵng")
        self.assertIsNone(asyncio.run(run(None)).province)
        for value in [[], "Đà Nẵng", {"province": 48}, {"province": " "}]:
            with self.subTest(value=value), self.assertRaises(self.errors.LocationError):
                asyncio.run(run(value))

    def test_place_detail_keeps_current_and_pre_merger_units(self):
        seen = []
        payload = {"status": "OK", "result": {"place_id": "id", "name": "Trung tâm",
            "formatted_address": "1 Quang Trung, Thủ Dầu Một, Hồ Chí Minh",
            "geometry": {"location": {"lat": 10.98, "lng": 106.65}},
            "compound": {"province": "Hồ Chí Minh"},
            "deprecated_compound": {"province": "Bình Dương", "district": "Thủ Dầu Một"}}}
        async def run():
            def handler(request):
                seen.append(request)
                return httpx.Response(200, json=payload)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://rsapi.goong.io") as http:
                return await self.module.GoongClient("secret-key", client=http).place_detail("id")
        detail = asyncio.run(run())
        self.assertEqual(detail.compound["province"], "Hồ Chí Minh")
        self.assertEqual(detail.deprecated_compound["province"], "Bình Dương")
        self.assertEqual(seen[0].url.params["has_deprecated_administrative_unit"], "true")


if __name__ == "__main__":
    unittest.main()
