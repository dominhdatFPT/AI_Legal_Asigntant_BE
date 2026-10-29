"""Location OpenAPI regression checks against the agreed task-plan contract."""
from pathlib import Path
import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = yaml.safe_load((ROOT / "services/location-service/openapi.yaml").read_text(encoding="utf-8"))


def resolve(ref):
    node = SPEC
    for part in ref.removeprefix("#/").split("/"):
        node = node[part]
    return node


def test_gateway_internal_urls_and_legacy_get_remain_explicit():
    assert [item["url"] for item in SPEC["servers"]] == [
        "http://localhost:8080/api/locations", "http://location-service:8000/api/v1/locations"
    ]
    assert SPEC["paths"]["/offices"]["get"]["x-status"] == "planned"


def test_public_catalogue_and_search_status():
    expected = {
        "/administrative/legacy/provinces", "/administrative/legacy/provinces/{province_code}/districts",
        "/administrative/legacy/districts/{district_code}/wards", "/administrative/current/provinces",
        "/administrative/current/provinces/{province_code}/wards",
    }
    assert all(SPEC["paths"][path]["get"]["security"] == [] for path in expected)
    assert all(SPEC["paths"][path]["get"]["x-status"] == "implemented" for path in expected)
    search = SPEC["paths"]["/offices/search"]["post"]
    assert search["x-status"] == "implemented"
    assert search["security"] == [{"BearerAuth": []}, {"CookieAuth": []}]
    assert set(search["responses"]) >= {"200", "400", "401", "422", "429", "502", "503", "504"}


def test_request_versions_and_limits_match_runtime():
    request = SPEC["components"]["schemas"]["OfficeSearchRequest"]
    assert request["required"] == ["address", "query"]
    legacy, current = (resolve(ref["$ref"]) for ref in request["properties"]["address"]["oneOf"])
    assert "ward_code" not in legacy["required"]
    assert current["properties"]["district_code"]["nullable"] is True
    assert "radius_meters" not in request["properties"]
    assert "limit" not in request["properties"]
    assert SPEC["components"]["schemas"]["OfficeSearchResponse"]["properties"]["data"]["properties"]["items"]["maxItems"] == 5
    assert request["properties"]["query"]["maxLength"] == 150


def test_response_has_origin_partial_warnings_and_safe_directions():
    response = SPEC["components"]["schemas"]["OfficeSearchResponse"]
    data = response["properties"]["data"]
    assert set(data["required"]) == {"origin", "items", "is_partial", "warnings"}
    office = SPEC["components"]["schemas"]["Office"]
    assert "directions_url" in office["required"]
    assert "api_key" not in office["properties"]
    assert "destination_place_id" not in office["properties"]
