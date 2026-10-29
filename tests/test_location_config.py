"""Offline checks for Location Task 2 configuration and the opt-in Goong probe."""
import importlib.util
from pathlib import Path
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "services/location-service/app/core/config.py"
SCRIPT_PATH = ROOT / "services/location-service/scripts/check_goong.py"


def load_config_module():
    name = "location_task2_config"
    spec = importlib.util.spec_from_file_location(name, CONFIG_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(name, None)


def clear_location_env(monkeypatch):
    for name in (
        "GOONG_API_KEY",
        "LOCATION_SEARCH_ENABLED",
        "LOCATION_GOONG_TIMEOUT_SECONDS",
        "LOCATION_SEARCH_TIMEOUT_SECONDS",
        "LOCATION_GOONG_MAX_RPS",
        "LOCATION_GOONG_DAILY_BUDGET",
        "GOOGLE_MAPS_API_KEY",
        "GEMINI_API_KEY",
        "LOCATION_WEB_LOOKUP_ENABLED",
        "LOCATION_WEB_MODEL",
        "LOCATION_WEB_DAILY_BUDGET",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_disabled_lazy_and_do_not_require_a_key(monkeypatch):
    clear_location_env(monkeypatch)
    module = load_config_module()
    settings = module.LocationSettings.from_env()
    assert settings.search_enabled is False
    assert settings.goong_api_key == ""
    assert settings.goong_timeout_seconds == 5
    assert settings.search_timeout_seconds == 20
    assert settings.goong_max_rps == 4
    assert settings.goong_daily_budget == 1000
    assert settings.google_maps_api_key == ""
    assert settings.search_unavailable_code() == "LOCATION_SEARCH_DISABLED"


def test_enabled_search_without_key_reports_not_configured(monkeypatch):
    clear_location_env(monkeypatch)
    monkeypatch.setenv("LOCATION_SEARCH_ENABLED", "true")
    settings = load_config_module().LocationSettings.from_env()
    assert settings.search_unavailable_code() == "GOONG_NOT_CONFIGURED"


def test_ready_settings_hide_key_from_repr(monkeypatch):
    clear_location_env(monkeypatch)
    monkeypatch.setenv("LOCATION_SEARCH_ENABLED", "true")
    monkeypatch.setenv("GOONG_API_KEY", "secret-test-key")
    settings = load_config_module().LocationSettings.from_env()
    assert settings.search_unavailable_code() is None
    assert "secret-test-key" not in repr(settings)
    assert "goong_api_key" not in repr(settings)
    assert "google_maps_api_key" not in repr(settings)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LOCATION_SEARCH_ENABLED", "yes"),
        ("LOCATION_GOONG_TIMEOUT_SECONDS", "0"),
        ("LOCATION_GOONG_TIMEOUT_SECONDS", "nan"),
        ("LOCATION_SEARCH_TIMEOUT_SECONDS", "121"),
        ("LOCATION_GOONG_MAX_RPS", "0"),
        ("LOCATION_GOONG_DAILY_BUDGET", "0"),
        ("GOONG_API_KEY", "secret with spaces"),
    ],
)
def test_invalid_values_fail_without_echoing_secret(monkeypatch, name, value):
    clear_location_env(monkeypatch)
    monkeypatch.setenv(name, value)
    module = load_config_module()
    with pytest.raises(module.ConfigurationError) as caught:
        module.LocationSettings.from_env()
    if name == "GOONG_API_KEY":
        assert value not in str(caught.value)


def test_total_deadline_cannot_be_shorter_than_one_provider_call(monkeypatch):
    clear_location_env(monkeypatch)
    monkeypatch.setenv("LOCATION_GOONG_TIMEOUT_SECONDS", "6")
    monkeypatch.setenv("LOCATION_SEARCH_TIMEOUT_SECONDS", "5")
    module = load_config_module()
    with pytest.raises(module.ConfigurationError, match="không được nhỏ hơn"):
        module.LocationSettings.from_env()


def test_compose_passes_only_expected_location_settings():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    environment = compose["services"]["location-service"]["environment"]
    assert environment["GOONG_API_KEY"] == "${GOONG_API_KEY:-}"
    assert environment["LOCATION_SEARCH_ENABLED"] == "${LOCATION_SEARCH_ENABLED:-false}"
    assert environment["LOCATION_GOONG_TIMEOUT_SECONDS"] == "${LOCATION_GOONG_TIMEOUT_SECONDS:-5}"
    assert environment["LOCATION_SEARCH_TIMEOUT_SECONDS"] == "${LOCATION_SEARCH_TIMEOUT_SECONDS:-20}"
    assert environment["LOCATION_GOONG_MAX_RPS"] == "${LOCATION_GOONG_MAX_RPS:-4}"
    assert environment["LOCATION_GOONG_DAILY_BUDGET"] == "${LOCATION_GOONG_DAILY_BUDGET:-1000}"
    assert environment["GOOGLE_MAPS_API_KEY"] == "${GOOGLE_MAPS_API_KEY:-}"


def test_example_and_requirements_include_location_only_dependencies():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for line in (
        "GOONG_API_KEY=",
        "LOCATION_SEARCH_ENABLED=false",
        "LOCATION_GOONG_TIMEOUT_SECONDS=5",
        "LOCATION_SEARCH_TIMEOUT_SECONDS=20",
        "LOCATION_GOONG_MAX_RPS=4",
        "LOCATION_GOONG_DAILY_BUDGET=1000",
        "GOOGLE_MAPS_API_KEY=",
    ):
        assert line in example.splitlines()
    requirements = (ROOT / "services/location-service/requirements.txt").read_text(encoding="utf-8")
    assert "httpx>=0.27,<1" in requirements
    assert "python-dotenv>=1,<2" in requirements


def test_probe_is_opt_in_bounded_and_never_prints_request_urls_or_keys():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert '"--run-probe"' in source
    assert "MAX_CALLS = 10" in source
    assert "api_key" in source
    assert "response.url" not in source
    assert "safe_params" not in source.partition("def get")[0]
    for keyword in ("UBND", "Trung tâm phục vụ hành chính công", "Văn phòng đăng ký đất đai"):
        assert keyword in source
    for path in ("/v2/geocode", "/v2/place/autocomplete", "/v2/place/detail"):
        assert path in source
