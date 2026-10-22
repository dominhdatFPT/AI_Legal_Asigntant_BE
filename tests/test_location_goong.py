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


if __name__ == "__main__":
    unittest.main()
