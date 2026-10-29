"""End-to-end Location route tests with catalogue and Goong fully mocked."""
import asyncio
import importlib
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient


SERVICE = Path(__file__).resolve().parents[1] / "services/location-service"
BODY = {"address": {"administrative_version": "current", "detail": "12 Lê Duẩn",
                    "province_code": 1, "district_code": None, "ward_code": 2},
        "query": "Trung tâm phục vụ hành chính công"}


class FakeProvider:
    def __init__(self, module):
        self.module = module
        self.calls = []
        self.details = {}
        self.suggestions = []
        self.search_results = {}
        self.geocodes = [module.GeocodeCandidate("12 Lê Duẩn, Hà Nội", 21, 105,
                                                  {"province": "Hà Nội"}, {})]

    async def aclose(self):
        pass

    async def geocode(self, address):
        self.calls.append(("geocode", address))
        for detail in self.details.values():
            if not isinstance(detail, Exception) and address == detail.address:
                return [self.module.GeocodeCandidate(detail.address, detail.latitude, detail.longitude,
                    {"province": detail.province or "Hà Nội"}, {})]
        return self.geocodes

    async def search_places(self, query, latitude, longitude, limit):
        self.calls.append(("search", query, limit))
        value = self.search_results.get(query, self.suggestions)
        if isinstance(value, Exception):
            raise value
        return value

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

    def post(self, body=None):
        return self.client.post("/api/v1/locations/offices/search", json=body or getattr(self, "body", BODY))

    def test_empty_and_validation_before_paid_call(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["items"], [])
        self.assertFalse(response.json()["data"]["is_partial"])
        self.provider.calls.clear()
        invalid = self.post({**BODY, "query": " "})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(self.provider.calls, [])

    def test_contact_lookup_endpoint_only_accepts_user_bound_handles(self):
        web = importlib.import_module("app.services.web_contacts")
        lookups = web.ContactLookups(None, enabled=True)
        self.main.app.state.contact_lookups = lookups
        token = lookups.register("another-user", [{"place_id": "x", "name": "Office", "address": "Hà Nội"}])
        url = "/api/v1/locations/offices/enrich"
        self.assertEqual(self.client.post(url, json={"lookup_id": token}).status_code, 422)
        self.assertEqual(self.client.post(url, json={"lookup_id": token, "url": "https://evil.test"}).status_code, 422)
        self.main.app.dependency_overrides.clear()
        self.assertEqual(self.client.post(url, json={"lookup_id": token}).status_code, 401)

    def test_search_returns_handle_without_calling_web_provider(self):
        web = importlib.import_module("app.services.web_contacts")
        lookups = web.ContactLookups(None, enabled=True)
        self.main.app.state.contact_lookups = lookups
        self.use_danang()
        data = self.post().json()["data"]
        self.assertEqual(len(data["items"]), 5)
        self.assertIn(data["web_lookup_id"], lookups.handles)
        stored = lookups.handles[data["web_lookup_id"]][2]
        self.assertNotIn("latitude", stored[0])
        self.assertNotIn("origin", stored[0])

    def test_google_places_hours_require_the_same_house_and_street(self):
        web = importlib.import_module("app.services.web_contacts")
        payload = {"places": [
            {"id": "ChIJwrongaddress123", "formattedAddress": "1 Quang Trung, Thủ Dầu Một, Hồ Chí Minh",
             "regularOpeningHours": {"weekdayDescriptions": ["Thứ Hai: 08:00–17:00"]}},
            {"id": "ChIJcorrectaddress1", "formattedAddress": "120 Nguyễn Thị Minh Khai, Cầu Ông Lãnh, Hồ Chí Minh",
             "displayName": {"text": "Trung tâm hành chính công Quận 1"},
             "regularOpeningHours": {"weekdayDescriptions": ["Thứ Hai: 07:30–17:00", "Thứ Ba: 07:30–17:00"]}},
        ]}
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=payload)
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                provider = web.GooglePlacesHours("secret-key", client=client)
                return await provider.lookup([{"place_id": "office", "name": "Trung tâm hành chính công Quận 1",
                    "address": "120 Nguyễn Thị Minh Khai, Cầu Ông Lãnh, Hồ Chí Minh"}])
        result = asyncio.run(run())["items"][0]
        self.assertEqual(result["web_lookup_status"], "verified")
        self.assertIn("07:30", result["opening_hours"]["text"])
        self.assertIn("ChIJcorrectaddress1", result["opening_hours"]["source_url"])
        self.assertEqual(result["google_place_id"], "ChIJcorrectaddress1")
        self.assertEqual(result["map_identity_source"], "google_places")
        self.assertEqual(requests[0].headers["x-goog-fieldmask"], web.GooglePlacesHours.fields)

    def test_google_identity_without_hours_and_rejects_other_tenants_and_cities(self):
        web = importlib.import_module("app.services.web_contacts")
        office = {"place_id": "office", "name": "Trung tâm hành chính công phường Bến Thành",
                  "address": "92 Nguyễn Trãi, Bến Thành, Hồ Chí Minh"}
        for name, address, verified in [
            ("UBND Phường Bến Thành", office["address"], True),
            ("UBND Phường Bến Thành", "92 Nguyễn Trãi, Hải Châu, Đà Nẵng", False),
            ("Ngân hàng Bến Thành", office["address"], False),
            ("UBND Phường Tân Định", office["address"], False),
            ("UBND Phường Bến Thành", "94 Nguyễn Trãi, Bến Thành, Hồ Chí Minh", False),
        ]:
            with self.subTest(name=name, address=address):
                def handler(request):
                    return httpx.Response(200, json={"places": [{"id": "ChIJp79jqT0vdTER_Bxv4VTO6lY",
                        "displayName": {"text": name}, "formattedAddress": address}]})
                async def run():
                    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                        return await web.GooglePlacesHours("test", client=client).lookup([office])
                item = asyncio.run(run())["items"][0]
                self.assertEqual(item["web_lookup_status"], "verified" if verified else "unverified")
                self.assertNotIn("opening_hours", item)
                self.assertEqual("google_place_id" in item, verified)

    def test_one_failed_google_lookup_keeps_other_verified_offices(self):
        web = importlib.import_module("app.services.web_contacts")
        offices = [{"place_id": "bad", "name": "Bad office", "address": "1 Đường A, Hà Nội"},
                   {"place_id": "good", "name": "Good office", "address": "2 Đường B, Hà Nội"}]
        def handler(request):
            if "Bad office" in request.content.decode():
                return httpx.Response(503, json={})
            return httpx.Response(200, json={"places": [{"id": "ChIJgoodplace12345",
                "displayName": {"text": "Good office"}, "formattedAddress": "2 Đường B, Hà Nội"}]})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await web.GooglePlacesHours("test", client=client).lookup(offices)
        items = asyncio.run(run())["items"]
        self.assertEqual([item["web_lookup_status"] for item in items], ["unavailable", "verified"])

    def test_reviewed_google_id_reads_place_details_and_falls_back_to_regular_hours(self):
        web = importlib.import_module("app.services.web_contacts")
        pid = "ChIJR3N1XAAZQjERXyT442XL2Cc"
        office = {"place_id": "lien-chieu", "name": "Trung tâm Phục vụ hành chính công phường Liên Chiểu",
                  "map_destination_name": "UBND Phường Liên Chiểu", "google_place_id": pid,
                  "address": "68 Lạc Long Quân, phường Liên Chiểu, Đà Nẵng"}
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"id": pid, "displayName": {"text": "UBND Phường Liên Chiểu"},
                "formattedAddress": "34GW+J54, Ngũ Hành Sơn, Đà Nẵng 550000, Việt Nam",
                "currentOpeningHours": {"openNow": False},
                "regularOpeningHours": {"weekdayDescriptions": ["Thứ Hai: 07:30–17:00"]}})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await web.GooglePlacesHours("test", client=client).lookup([office])
        result = asyncio.run(run())["items"][0]
        self.assertEqual(requests[0].method, "GET")
        self.assertEqual(requests[0].url.path, "/v1/places/" + pid)
        self.assertNotIn("places.", requests[0].headers["x-goog-fieldmask"])
        self.assertEqual(result["google_place_id"], pid)
        self.assertIn("07:30", result["opening_hours"]["text"])

    def test_current_google_hours_take_precedence_over_regular_hours(self):
        web = importlib.import_module("app.services.web_contacts")
        office = {"place_id": "office", "name": "UBND Phường Thanh Khê", "address": "503 Trần Cao Vân, Thanh Khê, Đà Nẵng"}
        def handler(request):
            return httpx.Response(200, json={"places": [{"id": "ChIJtcQGjlUYQjERXJP7YmtEGeU",
                "displayName": {"text": office["name"]}, "formattedAddress": office["address"],
                "currentOpeningHours": {"weekdayDescriptions": ["Thứ Hai: 08:00–11:00"]},
                "regularOpeningHours": {"weekdayDescriptions": ["Thứ Hai: 08:00–17:00"]}}]})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await web.GooglePlacesHours("test", client=client).lookup([office])
        hours = asyncio.run(run())["items"][0]["opening_hours"]["text"]
        self.assertEqual(hours, "Thứ Hai: 08:00–11:00")

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

    def test_dedupe_sort_and_directions_without_radius(self):
        candidate = self.goong.PlaceCandidate
        detail = self.goong.PlaceDetail
        self.provider.suggestions = [candidate("far", "Far"), candidate("near", "Near"), candidate("near", "Near")]
        self.provider.details = {
            "far": detail("far", "Trung tâm hành chính công Xa", "20 Đường Xa, Hà Nội", 21.2, 105),
            "near": detail("near", "Trung tâm hành chính công Gần", "10 Đường Gần, Hà Nội", 21.001, 105),
        }
        data = self.post().json()["data"]
        self.assertEqual([item["place_id"] for item in data["items"]], ["near", "far"])
        self.assertEqual(data["items"][0]["distance_type"], "straight_line")
        from urllib.parse import parse_qs, urlparse
        params = parse_qs(urlparse(data["items"][0]["directions_url"]).query)
        self.assertEqual(params["destination"], ["10 Đường Gần, Hà Nội"])
        self.assertEqual(params["origin"], ["12 Lê Duẩn, Hà Nội"])
        self.assertEqual([item for item in self.provider.calls if item[0] == "detail"],
                         [("detail", "far"), ("detail", "near")])

    def test_partial_and_total_detail_failure(self):
        candidate = self.goong.PlaceCandidate
        self.provider.suggestions = [candidate("bad", "Bad"), candidate("good", "Good")]
        self.provider.details = {
            "bad": self.errors.LocationError("GOONG_TIMEOUT", "timeout", 504),
            "good": self.goong.PlaceDetail("good", "Trung tâm hành chính công Tốt", "12 Đường A, Hà Nội", 21.001, 105),
        }
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["data"]["is_partial"])
        self.assertEqual(len(response.json()["data"]["items"]), 1)
        self.provider.details["good"] = self.errors.LocationError("GOONG_TIMEOUT", "timeout", 504)
        response = self.post()
        self.assertEqual(response.status_code, 504)

    def test_ambiguous_address_never_searches_places(self):
        self.provider.geocodes.append(self.goong.GeocodeCandidate("12 Lê Duẩn, Hà Nội", 20, 105, {}, {}))
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

    def test_localized_alias_recovers_remote_only_results(self):
        self.provider.search_results = {
            "Trung tâm phục vụ hành chính công, Hà Nội": [self.goong.PlaceCandidate("remote", "Remote")],
            "Trung tâm hành chính công, Hà Nội": [self.goong.PlaceCandidate("local", "Local")],
        }
        self.provider.details = {
            "remote": self.goong.PlaceDetail("remote", "Trung tâm hành chính công Xa", "Vĩnh Long", 10, 106),
            "local": self.goong.PlaceDetail("local", "Trung tâm hành chính công Trung tâm", "12 Đường A, Hà Nội", 21.01, 105),
        }
        data = self.post().json()["data"]
        self.assertEqual([item["place_id"] for item in data["items"]], ["local"])
        self.assertEqual([c[1] for c in self.provider.calls if c[0] == "search"],
                         importlib.import_module("app.services.office_search").discovery_queries(
                             BODY["query"], "Thành phố Hà Nội", "Phường Trúc Bạch"))

    def test_raw_query_recovers_legacy_names_and_does_not_repeat_details(self):
        self.provider.suggestions = [self.goong.PlaceCandidate("remote", "Remote")]
        self.provider.search_results[BODY["query"]] = [self.goong.PlaceCandidate("near", "Near")]
        self.provider.details = {
            "remote": self.goong.PlaceDetail("remote", "Trung tâm hành chính công Xa", "Xa", 10, 106),
            "near": self.goong.PlaceDetail("near", "Trung tâm hành chính công Gần", "12 Đường A, Hà Nội", 21.001, 105),
        }
        self.assertEqual(self.post().json()["data"]["items"][0]["place_id"], "near")
        self.assertEqual([c for c in self.provider.calls if c[0] == "detail"],
                         [("detail", "remote"), ("detail", "near")])

    def test_fixed_five_results_sorted_across_all_queries(self):
        self.provider.suggestions = [self.goong.PlaceCandidate(str(i), "Office") for i in range(10)]
        self.provider.details = {str(i): self.goong.PlaceDetail(str(i), "Trung tâm hành chính công Office", f"{i+1} Đường A, Hà Nội", 21 + (10-i)*0.001, 105)
                                 for i in range(10)}
        data = self.post().json()["data"]
        self.assertEqual([x["place_id"] for x in data["items"]], ["9", "8", "7", "6", "5"])
        self.assertEqual(len([c for c in self.provider.calls if c[0] == "search"]), 5)

    def test_post_merger_hcm_search_excludes_former_binh_duong_region(self):
        self.catalog.current_provinces.return_value = {"items": [{"code": 1, "name": "Thành phố Hồ Chí Minh"}]}
        self.catalog.current_wards.return_value = {"items": [{"code": 2, "name": "Phường Cầu Ông Lãnh"}]}
        self.provider.geocodes = [self.goong.GeocodeCandidate(
            "120 Nguyễn Thị Minh Khai, Cầu Ông Lãnh, Hồ Chí Minh", 10.768, 106.692,
            {"province": "Hồ Chí Minh", "commune": "Cầu Ông Lãnh"},
            {"province": "Hồ Chí Minh", "district": "Quận 1"})]
        local_query = "Văn phòng công chứng, Cầu Ông Lãnh, Hồ Chí Minh"
        self.provider.search_results[local_query] = [
            self.goong.PlaceCandidate("q1", "Quận 1"),
            self.goong.PlaceCandidate("tdm", "Thủ Dầu Một"),
        ]
        self.provider.details = {
            "q1": self.goong.PlaceDetail("q1", "Văn phòng công chứng Quận 1",
                "120 Nguyễn Thị Minh Khai, Cầu Ông Lãnh, Hồ Chí Minh", 10.768, 106.692,
                "Hồ Chí Minh", {"province": "Hồ Chí Minh"},
                {"province": "Hồ Chí Minh", "district": "Quận 1"}),
            "tdm": self.goong.PlaceDetail("tdm", "Văn phòng công chứng Thủ Dầu Một",
                "1 Quang Trung, Thủ Dầu Một, Hồ Chí Minh", 10.98, 106.65,
                "Hồ Chí Minh", {"province": "Hồ Chí Minh"},
                {"province": "Bình Dương", "district": "Thủ Dầu Một"}),
        }
        data = self.post({**BODY, "address": {**BODY["address"], "detail": "120 Nguyễn Thị Minh Khai"},
                          "query": "Văn phòng công chứng"}).json()["data"]
        self.assertEqual([item["place_id"] for item in data["items"]], ["q1"])
        self.assertEqual(data["coverage"]["excluded_outside_historical_region"], 1)
        self.assertEqual([call[1] for call in self.provider.calls if call[0] == "search"][0], local_query)

    def test_hcm_public_service_centers_include_district_one_before_thu_duc(self):
        self.catalog.current_provinces.return_value = {"items": [{"code": 1, "name": "Thành phố Hồ Chí Minh"}]}
        self.catalog.current_wards.return_value = {"items": [{"code": 2, "name": "Phường Cầu Ông Lãnh"}]}
        self.provider.geocodes = [self.goong.GeocodeCandidate(
            "120 Nguyễn Thị Minh Khai, Cầu Ông Lãnh, Hồ Chí Minh", 10.768, 106.692,
            {"province": "Hồ Chí Minh"}, {"province": "Hồ Chí Minh", "district": "Quận 1"})]
        data = self.post({**BODY, "address": {**BODY["address"], "detail": "120 Nguyễn Thị Minh Khai"}}).json()["data"]
        self.assertEqual(len(data["items"]), 5)
        self.assertEqual({item["place_id"] for item in data["items"][:4]}, {
            "hcm-public-service-sai-gon", "hcm-public-service-ben-thanh",
            "hcm-public-service-tan-dinh", "hcm-public-service-cau-ong-lanh"})
        self.assertEqual(data["items"][-1]["place_id"], "hcm-public-service-center-headquarters")
        distances = [item["distance_meters"] for item in data["items"]]
        self.assertEqual(distances, sorted(distances))
        self.assertEqual(data["items"][0]["location_verification"], "official_address_and_google_place")
        self.assertIn("07:30", data["items"][0]["opening_hours"]["text"])
        from urllib.parse import parse_qs, urlparse
        route = parse_qs(urlparse(data["items"][0]["directions_url"]).query)
        self.assertEqual(route["destination_place_id"], [data["items"][0]["google_place_id"]])
        self.assertIn(data["items"][0]["address"], route["destination"][0])
        self.assertEqual([call[0] for call in self.provider.calls], ["geocode"])

    def test_closer_localized_synonym_is_not_hidden_by_first_match(self):
        self.provider.search_results = {
            "Trung tâm phục vụ hành chính công, Hà Nội": [self.goong.PlaceCandidate("farther", "Office")],
            "Trung tâm hành chính công, Hà Nội": [self.goong.PlaceCandidate("nearer", "Office")],
        }
        self.provider.details = {
            "farther": self.goong.PlaceDetail("farther", "Trung tâm hành chính công Office", "24 Đường A, Hà Nội", 21.02, 105),
            "nearer": self.goong.PlaceDetail("nearer", "Trung tâm hành chính công Office", "12 Đường A, Hà Nội", 21.001, 105),
        }
        items = self.post().json()["data"]["items"]
        self.assertEqual([i["place_id"] for i in items], ["nearer", "farther"])

    def test_synonym_timeout_keeps_existing_results_with_warning(self):
        self.provider.search_results = {
            "Trung tâm phục vụ hành chính công, Hà Nội": [self.goong.PlaceCandidate("near", "Office")],
            "Trung tâm hành chính công, Hà Nội": self.errors.LocationError("GOONG_TIMEOUT", "timeout", 504),
        }
        self.provider.details = {"near": self.goong.PlaceDetail("near", "Trung tâm hành chính công Office", "12 Đường A, Hà Nội", 21.001, 105)}
        data = self.post().json()["data"]
        self.assertTrue(data["is_partial"])
        self.assertEqual(len(data["items"]), 1)

    def test_empty_search_is_bounded_and_detail_candidates_capped(self):
        calls = [0]
        async def suggestions(*args):
            calls[0] += 1
            return [self.goong.PlaceCandidate(f"{calls[0]}-{i}", "Remote") for i in range(15)]
        self.provider.search_places = suggestions
        self.provider.details = {f"{batch}-{i}": self.goong.PlaceDetail(f"{batch}-{i}", "Trung tâm hành chính công Xa", "Xa", 10, 106)
                                 for batch in range(1, 4) for i in range(15)}
        self.assertEqual(self.post().json()["data"]["items"], [])
        self.assertEqual(calls[0], 3)
        self.assertEqual(len([c for c in self.provider.calls if c[0] == "detail"]), 30)

    def test_provider_failure_does_not_become_empty_success(self):
        for code, status in [("GOONG_ACCESS_DENIED", 503), ("GOONG_QUOTA_EXCEEDED", 503),
                             ("GOONG_TIMEOUT", 504), ("GOONG_BAD_RESPONSE", 502)]:
            with self.subTest(code=code):
                self.provider.search_results["Trung tâm phục vụ hành chính công, Hà Nội"] = self.errors.LocationError(code, "error", status)
                response = self.post()
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["error"]["code"], code)

    def test_fatal_detail_errors_are_not_partial_results(self):
        self.provider.suggestions = [self.goong.PlaceCandidate("bad", "Bad")]
        for code in ["GOONG_ACCESS_DENIED", "GOONG_QUOTA_EXCEEDED"]:
            with self.subTest(code=code):
                self.provider.details["bad"] = self.errors.LocationError(code, "error", 503)
                self.assertEqual(self.post().status_code, 503)

    def test_query_plans_across_provinces_accents_and_office_types(self):
        plan = importlib.import_module("app.services.office_search").discovery_queries
        for province in ["Thành phố Đà Nẵng", "Thành phố Hà Nội", "Thành phố Hồ Chí Minh", "Tỉnh An Giang"]:
            for query in ["Trung tâm phục vụ hành chính công", "trung tam phuc vu hanh chinh cong",
                          "  TRUNG TÂM   HÀNH CHÍNH CÔNG  ", "Văn phòng công chứng", "Chi cục thuế", "UBND phường Liên Chiểu"]:
                with self.subTest(province=province, query=query):
                    queries = plan(query, province)
                    self.assertLessEqual(len(queries), 3)
                    self.assertEqual(len(queries), len(set(queries)))
                    self.assertEqual(queries[-1], " ".join(query.split()))
                    if "UBND" not in query:
                        self.assertFalse(any("UBND" in value for value in queries))
        self.assertEqual(plan("UBND Đà Nẵng", "Thành phố Đà Nẵng"), ["UBND Đà Nẵng"])
        self.assertEqual(plan("UBND Da Nang", "Thành phố Đà Nẵng"), ["UBND Da Nang"])
        self.assertEqual(plan("Trung tâm hành chính công Điện Bàn", "Thành phố Đà Nẵng"),
                         ["Trung tâm hành chính công Điện Bàn, Đà Nẵng", "Trung tâm phục vụ hành chính công dien ban, Đà Nẵng", "Trung tâm hành chính công Điện Bàn"])

    def test_empty_geocode_and_overall_timeout(self):
        self.provider.geocodes = []
        self.assertEqual(self.post().json()["error"]["code"], "ADDRESS_NOT_FOUND")
        async def slow(*args):
            import asyncio
            await asyncio.sleep(0.1)
        self.provider.geocode = slow
        state = self.main.app.state
        state.location_settings = replace(state.location_settings, search_timeout_seconds=0.01)
        self.assertEqual(self.post().json()["error"]["code"], "GOONG_TIMEOUT")

    def test_removed_radius_and_limit_are_rejected_before_provider_calls(self):
        for extra in [{"radius_meters": 1000}, {"limit": 10}]:
            self.assertEqual(self.post({**BODY, **extra}).status_code, 422)
        self.assertEqual(self.provider.calls, [])

    def test_far_offices_are_still_ranked_and_returned(self):
        self.provider.suggestions = [self.goong.PlaceCandidate("far", "Office")]
        self.provider.details = {"far": self.goong.PlaceDetail("far", "Trung tâm hành chính công Office", "22 Đường Xa, Hà Nội", 22, 105)}
        data = self.post().json()["data"]
        self.assertEqual(len(data["items"]), 1)
        self.assertGreater(data["items"][0]["distance_meters"], 50000)

    def test_unrelated_fuzzy_suggestions_do_not_fill_five_slots(self):
        self.provider.suggestions = [self.goong.PlaceCandidate("wrong", "Unrelated")]
        self.provider.details = {"wrong": self.goong.PlaceDetail("wrong", "Cơ khí tổng hợp", "Đà Nẵng", 21.001, 105)}
        self.assertEqual(self.post().json()["data"]["items"], [])

    def test_office_terms_match_accents_abbreviations_and_specific_location(self):
        matches = importlib.import_module("app.services.office_search").matches_query
        detail = self.goong.PlaceDetail("id", "Ủy ban nhân dân phường Liên Chiểu", "Đà Nẵng", 16, 108)
        self.assertTrue(matches(detail, "UBND phuong Lien Chieu"))
        self.assertFalse(matches(detail, "UBND phường Hòa Minh"))
        self.assertFalse(matches(detail, "Văn phòng công chứng"))

    def use_danang(self):
        self.body = {**BODY, "address": {**BODY["address"], "detail": "16 Hòa Mỹ 3"}}
        self.catalog.current_provinces.return_value = {"items": [{"code": 1, "name": "Thành phố Đà Nẵng"}]}
        self.catalog.current_wards.return_value = {"items": [{"code": 2, "name": "Phường Liên Chiểu"}]}
        self.provider.geocodes = [self.goong.GeocodeCandidate("16 Hòa Mỹ 3, Hòa Khánh, Đà Nẵng",
            16.058648144000074, 108.16337713600007, {"province": "Đà Nẵng"}, {})]

    def test_ubnd_provider_results_reuse_reviewed_identity_and_hours(self):
        self.use_danang()
        self.provider.suggestions = [self.goong.PlaceCandidate("tk", "UBND Thanh Khê"),
                                     self.goong.PlaceCandidate("lc", "UBND Liên Chiểu")]
        self.provider.details = {
            "tk": self.goong.PlaceDetail("tk", "Ủy ban nhân dân Phường Thanh Khê",
                "503 Trần Cao Vân, Thanh Khê, Đà Nẵng", 16.071179, 108.191244, "Đà Nẵng"),
            "lc": self.goong.PlaceDetail("lc", "UBND Phường Liên Chiểu",
                "68 Lạc Long Quân, Liên Chiểu, Đà Nẵng", 16.0753648, 108.1435487, "Đà Nẵng"),
        }
        data = self.post({**self.body, "query": "Ủy ban nhân dân"}).json()["data"]
        self.assertEqual(len(data["items"]), 2)
        expected = {"tk": "ChIJtcQGjlUYQjERXJP7YmtEGeU", "lc": "ChIJR3N1XAAZQjERXyT442XL2Cc"}
        for item in data["items"]:
            self.assertEqual(item["google_place_id"], expected[item["place_id"]])
            self.assertIn("destination_place_id=" + expected[item["place_id"]], item["directions_url"])
            self.assertIn("Thứ Hai", item["opening_hours"]["text"])
            self.assertIn(".gov.vn/", item["source_url"])

    def test_other_tenant_at_reviewed_address_does_not_inherit_ubnd_hours(self):
        self.use_danang()
        self.provider.suggestions = [self.goong.PlaceCandidate("tenant", "Công chứng")]
        self.provider.details = {"tenant": self.goong.PlaceDetail("tenant", "Văn phòng công chứng Thanh Khê",
            "503 Trần Cao Vân, Thanh Khê, Đà Nẵng", 16.071179, 108.191244, "Đà Nẵng")}
        item = self.post({**self.body, "query": "Văn phòng công chứng"}).json()["data"]["items"][0]
        self.assertIsNone(item["google_place_id"])
        self.assertNotIn("opening_hours", item)

    def test_official_centers_missing_from_goong_appear_before_remote_matches(self):
        self.use_danang()
        data = self.post().json()["data"]
        self.assertEqual(len(data["items"]), 5)
        self.assertIn("Hòa Khánh", data["items"][0]["name"])
        self.assertIn("Liên Chiểu", data["items"][1]["name"])
        self.assertAlmostEqual(data["items"][0]["distance_meters"], 1638, delta=50)
        self.assertAlmostEqual(data["items"][1]["distance_meters"], 2819, delta=50)
        self.assertTrue(all(item["distance_meters"] < 10000 for item in data["items"]))
        self.assertEqual(data["coverage"]["source"], "official_directory")
        self.assertEqual([c[0] for c in self.provider.calls], ["geocode"])
        self.assertEqual(data["items"][0]["contact_phone"], "02363730807")
        self.assertEqual(data["items"][0]["contact_label"], "Đường dây thủ tục hành chính")
        self.assertEqual(data["items"][0]["contact_source_url"], "https://hoakhanh.danang.gov.vn/")
        self.assertEqual(data["items"][0]["photo"]["url"], "/images/offices/hoa-khanh.jpg")
        self.assertIn("07:30", data["items"][0]["opening_hours"]["text"])
        self.assertEqual(data["items"][0]["opening_hours"]["source_type"], "google_maps_reviewed")
        self.assertIn("xephangqrcode.web.app", data["items"][0]["appointment_url"])
        self.assertEqual(data["items"][1]["contact_phone"], "02363777998")
        self.assertEqual((data["items"][0]["latitude"], data["items"][0]["longitude"]),
                         (16.0724872, 108.1581356))
        self.assertIn("destination=UBND+Ph%C6%B0%E1%BB%9Dng+H%C3%B2a+Kh%C3%A1nh", data["items"][0]["directions_url"])
        self.assertIn("query_place_id=ChIJN5dxZQAZQjER70_vuqR-Ut0", data["items"][0]["maps_url"])
        self.assertIn("0xdd527ea4baef4fef", data["items"][0]["map_embed_url"])
        from urllib.parse import parse_qs, urlparse
        for item in data["items"]:
            params = parse_qs(urlparse(item["directions_url"]).query)
            self.assertEqual(params["origin"], ["16 Hòa Mỹ 3, Hòa Khánh, Đà Nẵng"])
        ankhe = next(item for item in data["items"] if item["unit_name"] == "Phường An Khê")
        self.assertIsNone(ankhe["photo"])
        for item in data["items"]:
            self.assertEqual(item["provider"], "official_directory")
            self.assertTrue(item["source_url"].startswith("https://thangan.danang.gov.vn/"))

    def test_official_directory_preserves_specific_center_query_and_accents(self):
        self.use_danang()
        for query in ["Trung tâm hành chính công phường Hòa Khánh", "trung tam phuc vu hanh chinh cong phuong Hoa Khanh"]:
            with self.subTest(query=query):
                data = self.post({**self.body, "query": query}).json()["data"]
                self.assertEqual(len(data["items"]), 1)
                self.assertIn("168", data["items"][0]["address"])
                self.assertIn("Hòa Khánh", data["items"][0]["name"])

    def test_other_office_types_do_not_return_service_centers(self):
        self.use_danang()
        data = self.post({**self.body, "query": "Văn phòng công chứng"}).json()["data"]
        self.assertEqual(data["items"], [])
        self.assertEqual(data["coverage"]["source"], "goong_candidates")

    def test_missing_office_pin_is_reported_and_never_uses_commune_centroid(self):
        self.use_danang()
        offices = importlib.import_module("app.services.office_search")
        directory = importlib.import_module("app.services.official_directory")
        import copy
        snapshot = copy.deepcopy(directory.load_directory())
        for e in snapshot["entries"]:
            e["coordinate_status"] = "needs_verified_office_pin"
        with patch.object(offices, "load_directory", return_value=snapshot):
            data = self.post().json()["data"]
        self.assertEqual(data["items"], [])
        self.assertTrue(data["is_partial"])
        self.assertEqual(data["warnings"][0]["code"], "OFFICIAL_COORDINATES_INCOMPLETE")
        self.assertEqual([c[0] for c in self.provider.calls], ["geocode"])

    def test_official_snapshot_has_distinct_center_address_and_coordinate_provenance(self):
        data = importlib.import_module("app.services.official_directory").load_directory()
        self.assertEqual(len(data["entries"]), 93)
        self.assertEqual(len({e["id"] for e in data["entries"]}), 93)
        tamky = next(e for e in data["entries"] if e["unit_name"] == "Phường Tam Kỳ")
        self.assertTrue(tamky["address"].startswith("159"))
        self.assertNotIn("70 đường Hùng Vương", tamky["address"])
        for e in data["entries"]:
            if e["coordinate_status"] == "street_address_match":
                self.assertEqual(e["coordinate_provider"], "goong")
                self.assertIn("geocoded_at", e)
                self.assertRegex(e["address"], r"^\d+")
            if e["coordinate_status"] == "office_poi_match":
                self.assertEqual(e["coordinate_provider"], "goong_office_poi")
                self.assertIn("coordinate_source_url", e)
                self.assertIn("matched_poi_address", e)

    def test_geocode_review_rejects_alley_house_and_wrong_street(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("official_builder", SERVICE / "scripts/build_official_directory.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        match = builder.street_address_matches
        self.assertTrue(match("06 đường Nguyễn Phan Vinh, Sơn Trà", "6 Nguyễn Phan Vinh, Sơn Trà"))
        self.assertFalse(match("06 đường Nguyễn Phan Vinh, Sơn Trà", "K62H02-6 Nguyễn Phan Vinh, Sơn Trà"))
        self.assertFalse(match("168 Nguyễn Sinh Sắc, Hòa Khánh", "68 Nguyễn Sinh Sắc, Hòa Khánh"))
        self.assertFalse(match("168 Nguyễn Sinh Sắc, Hòa Khánh", "168 Tôn Đức Thắng, Hòa Khánh"))
        self.assertFalse(match("thôn Nam Hà, Thăng An", "Thăng An, Đà Nẵng"))

    def test_maps_links_keep_house_street_and_full_coordinate_precision(self):
        from urllib.parse import parse_qs, urlparse
        links = importlib.import_module("app.services.maps_links")
        origin = (16.058648144, 108.163377136)
        for label in ["16 Hòa Mỹ 3, Hòa Khánh, Đà Nẵng", "27 Hòa Mỹ 4, Hòa Khánh, Đà Nẵng"]:
            result = links.directions_url(16.0724872, 108.1581356, origin, origin_address=label)
            params = parse_qs(urlparse(result).query)
            self.assertEqual(params["origin"], [label])
            self.assertEqual(params["destination"], ["16.0724872,108.1581356"])
        coordinate_link = parse_qs(urlparse(links.directions_url(16.0724872, 108.1581356, origin)).query)
        self.assertEqual(coordinate_link["origin"], ["16.0586481,108.1633771"])

    def test_old_directory_mobile_is_not_used_as_current_office_phone(self):
        self.use_danang()
        offices = importlib.import_module("app.services.office_search")
        with patch.object(offices, "load_profiles", return_value={}):
            data = self.post().json()["data"]
        self.assertTrue(all(item["contact_phone"] is None for item in data["items"]))

    def test_every_located_official_center_ranks_first_when_user_is_at_its_address(self):
        from types import SimpleNamespace
        offices = importlib.import_module("app.services.office_search")
        directory = importlib.import_module("app.services.official_directory").load_directory()
        for e in directory["entries"]:
            if e["coordinate_status"] not in {"street_address_match", "office_poi_match"}:
                continue
            with self.subTest(unit=e["unit_name"]):
                origin = SimpleNamespace(latitude=e["latitude"], longitude=e["longitude"])
                result = offices.official_results(SimpleNamespace(province_name="Thành phố Đà Nẵng"), origin, BODY["query"])
                self.assertEqual(result["items"][0]["place_id"], e["id"])
                self.assertEqual(result["items"][0]["distance_meters"], 0)
                self.assertEqual(len(result["items"]), 5)

    def test_legacy_quangnam_uses_current_province_from_geocode_for_official_directory(self):
        from types import SimpleNamespace
        offices = importlib.import_module("app.services.office_search")
        origin = self.goong.GeocodeCandidate("Tam Kỳ, Đà Nẵng", 15.57, 108.47,
                                             {"province": "Đà Nẵng"}, {"province": "Quảng Nam"})
        result = offices.official_results(SimpleNamespace(administrative_version="legacy", province_name="Tỉnh Quảng Nam"),
                                         origin, BODY["query"])
        self.assertEqual(result["coverage"]["source"], "official_directory")
        self.assertIn("Tam Kỳ", result["items"][0]["name"])

    def test_business_registration_repro_filters_hcm_and_finds_local_phong(self):
        self.use_danang()
        self.provider.suggestions = [self.goong.PlaceCandidate("hcm", "Wrong city")]
        self.provider.search_results["Phòng đăng ký kinh doanh, Đà Nẵng"] = [self.goong.PlaceCandidate("local", "Local")]
        self.provider.details = {
            "hcm": self.goong.PlaceDetail("hcm", "Văn phòng đăng ký kinh doanh", "82a-82b Dân Tộc, Tân Sơn Nhì, Hồ Chí Minh", 10.8, 106.6, "Hồ Chí Minh"),
            "local": self.goong.PlaceDetail("local", "Phòng Đăng Ký Kinh Doanh", "Hải Châu, Đà Nẵng", 16.07, 108.22, "Đà Nẵng"),
        }
        data = self.post({**self.body, "query": "Văn phòng đăng ký kinh doanh"}).json()["data"]
        self.assertEqual([x["place_id"] for x in data["items"]], ["danang-business-registration"])
        self.assertLess(data["items"][0]["distance_meters"], 10000)
        self.assertIn("24 Trần Phú", data["items"][0]["address"])
        self.assertEqual(data["items"][0]["google_place_id"], "ChIJTZV8WToYQjER8dD77Ei90ic")

    def test_remote_only_business_results_are_empty_with_locality_explanation(self):
        self.use_danang()
        self.provider.suggestions = [self.goong.PlaceCandidate("hcm", "Wrong city")]
        self.provider.details = {"hcm": self.goong.PlaceDetail("hcm", "Văn phòng đăng ký kinh doanh", "Dân Tộc, Hồ Chí Minh", 10.8, 106.6, "Hồ Chí Minh")}
        data = self.post({**self.body, "query": "Văn phòng đăng ký kinh doanh"}).json()["data"]
        self.assertEqual(len(data["items"]), 1)
        self.assertIn("24 Trần Phú", data["items"][0]["address"])
        self.assertEqual(data["coverage"]["province"], "Đà Nẵng")

    def test_province_filter_for_all_categories_and_current_address_formats(self):
        match = importlib.import_module("app.services.office_search").matches_province
        for query in ["Văn phòng đăng ký kinh doanh", "Văn phòng công chứng", "Văn phòng đăng ký đất đai", "UBND", "Chi cục thuế"]:
            for province in ["Đà Nẵng", "Hà Nội", "An Giang", "Hồ Chí Minh"]:
                for suffix in [province, f"Thành phố {province}", f"{province}, Việt Nam", f"{province} 550000", f"{province.upper()}, VIETNAM"]:
                    with self.subTest(query=query, province=province, suffix=suffix):
                        d = self.goong.PlaceDetail("id", query, f"12 Đường A, Phường B, {suffix}", 16, 108)
                        self.assertTrue(match(d, province))
                wrong = "Hồ Chí Minh" if province != "Hồ Chí Minh" else "Đà Nẵng"
                d = self.goong.PlaceDetail("id", query, f"Đường {province}, {wrong}", 16, 108, wrong)
                self.assertFalse(match(d, province))
        self.assertFalse(match(self.goong.PlaceDetail("id", "Office", "12 Đường A, Hồ Chí Minh", 16, 108, "Đà Nẵng"), "Đà Nẵng"))
        self.assertFalse(match(self.goong.PlaceDetail("id", "Office", "Không rõ địa chỉ", 16, 108), "Đà Nẵng"))

    def test_business_aliases_preserve_area_and_reject_consulting_businesses(self):
        offices = importlib.import_module("app.services.office_search")
        for query in ["Văn phòng đăng ký kinh doanh", "van phong dang ky kinh doanh", "Phòng đăng ký kinh doanh"]:
            with self.subTest(query=query):
                self.assertTrue(offices.matches_query(self.goong.PlaceDetail("id", "Phòng Đăng Ký Kinh Doanh", "Đà Nẵng", 16, 108), query))
                self.assertFalse(offices.matches_query(self.goong.PlaceDetail("id", "Công ty tư vấn Văn phòng đăng ký kinh doanh", "Đà Nẵng", 16, 108), query))
        planned = offices.discovery_queries("Văn phòng đăng ký kinh doanh Hải Châu", "Thành phố Đà Nẵng")
        self.assertTrue(all("hai chau" in offices._query_key(q) for q in planned))

    def test_duplicate_provider_ids_for_same_named_office_and_pin_are_merged(self):
        self.provider.suggestions = [self.goong.PlaceCandidate(x, "Office") for x in ["a", "b", "other-branch"]]
        self.provider.details = {
            "a": self.goong.PlaceDetail("a", "Phòng Đăng Ký Kinh Doanh", "12 Đường A, Hà Nội", 21.001, 105, "Hà Nội"),
            "b": self.goong.PlaceDetail("b", "Phòng đăng ký kinh doanh", "12 Đường A, Hà Nội", 21.00101, 105, "Hà Nội"),
            "other-branch": self.goong.PlaceDetail("other-branch", "Phòng đăng ký kinh doanh", "24 Đường B, Hà Nội", 21.02, 105, "Hà Nội"),
        }
        data = self.post({**BODY, "query": "Văn phòng đăng ký kinh doanh"}).json()["data"]
        self.assertEqual([x["place_id"] for x in data["items"]], ["a", "other-branch"])


if __name__ == "__main__":
    unittest.main()
