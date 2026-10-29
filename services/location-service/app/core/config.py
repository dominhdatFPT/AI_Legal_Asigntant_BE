"""Lazy, backend-only Location configuration with secret-safe validation."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
import re


class ConfigurationError(ValueError):
    """Raised for invalid Location environment values without echoing secrets."""


def _boolean(name: str, default: str) -> bool:
    value = os.getenv(name, default).strip().lower()
    if value not in {"true", "false"}:
        raise ConfigurationError(f"{name} phải là true hoặc false.")
    return value == "true"


def _number(name: str, default: str, *, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, default))
    except (TypeError, ValueError):
        raise ConfigurationError(f"{name} phải là số hợp lệ.") from None
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} phải từ {minimum:g} đến {maximum:g}.")
    return value


def _integer(name: str, default: str, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, default))
    except (TypeError, ValueError):
        raise ConfigurationError(f"{name} phải là số nguyên hợp lệ.") from None
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} phải từ {minimum} đến {maximum}.")
    return value


@dataclass(frozen=True)
class LocationSettings:
    """Settings are parsed only when explicitly requested; importing is side-effect free."""

    goong_api_key: str = field(default="", repr=False)
    search_enabled: bool = False
    goong_timeout_seconds: float = 5.0
    search_timeout_seconds: float = 20.0
    goong_max_rps: float = 4.0
    goong_daily_budget: int = 1000
    web_lookup_enabled: bool = False
    web_api_key: str = field(default="", repr=False)
    google_maps_api_key: str = field(default="", repr=False)
    web_model: str = "gemini-2.5-flash"
    web_daily_budget: int = 20

    @classmethod
    def from_env(cls) -> "LocationSettings":
        key = os.getenv("GOONG_API_KEY", "").strip()
        if key and (
            len(key) > 512
            or any(character.isspace() or ord(character) < 33 or ord(character) > 126 for character in key)
        ):
            raise ConfigurationError("GOONG_API_KEY không hợp lệ.")

        provider_timeout = _number(
            "LOCATION_GOONG_TIMEOUT_SECONDS", "5", minimum=0.1, maximum=30
        )
        search_timeout = _number(
            "LOCATION_SEARCH_TIMEOUT_SECONDS", "20", minimum=1, maximum=120
        )
        if search_timeout < provider_timeout:
            raise ConfigurationError(
                "LOCATION_SEARCH_TIMEOUT_SECONDS không được nhỏ hơn LOCATION_GOONG_TIMEOUT_SECONDS."
            )

        web_model = os.getenv("LOCATION_WEB_MODEL", os.getenv("GEMINI_MODEL", "gemini-2.5-flash")).strip()
        web_key = os.getenv("GEMINI_API_KEY", "").strip()
        google_maps_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", web_model):
            raise ConfigurationError("LOCATION_WEB_MODEL không hợp lệ.")
        if len(web_key) > 512 or any(ord(c) < 33 or ord(c) > 126 for c in web_key):
            raise ConfigurationError("GEMINI_API_KEY không hợp lệ.")
        if len(google_maps_key) > 512 or any(ord(c) < 33 or ord(c) > 126 for c in google_maps_key):
            raise ConfigurationError("GOOGLE_MAPS_API_KEY không hợp lệ.")
        return cls(
            goong_api_key=key,
            search_enabled=_boolean("LOCATION_SEARCH_ENABLED", "false"),
            goong_timeout_seconds=provider_timeout,
            search_timeout_seconds=search_timeout,
            goong_max_rps=_number("LOCATION_GOONG_MAX_RPS", "4", minimum=0.1, maximum=100),
            goong_daily_budget=_integer("LOCATION_GOONG_DAILY_BUDGET", "1000", minimum=1, maximum=1000000),
            web_lookup_enabled=_boolean("LOCATION_WEB_LOOKUP_ENABLED", "false"),
            web_api_key=web_key,
            google_maps_api_key=google_maps_key,
            web_model=web_model,
            web_daily_budget=_integer("LOCATION_WEB_DAILY_BUDGET", "20", minimum=1, maximum=1000),
        )

    def search_unavailable_code(self) -> str | None:
        """Return the future API error code without contacting the provider."""
        if not self.search_enabled:
            return "LOCATION_SEARCH_DISABLED"
        if not self.goong_api_key:
            return "GOONG_NOT_CONFIGURED"
        return None
