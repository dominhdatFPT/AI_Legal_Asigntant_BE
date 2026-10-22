"""Opt-in Goong V2 probe for two public areas and three office keywords.

The probe is bounded to at most ten requests, may consume provider quota, and
never prints the API key, request URLs, raw provider payloads, or private input.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import httpx
from dotenv import load_dotenv


SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))

from app.core.config import ConfigurationError, LocationSettings  # noqa: E402


BASE_URL = "https://rsapi.goong.io"
AREAS = (
    ("Hà Nội", "91 Trung Kính, Hà Nội"),
    ("TP.HCM", "12 Lê Duẩn, Thành phố Hồ Chí Minh"),
)
KEYWORDS = (
    "UBND",
    "Trung tâm phục vụ hành chính công",
    "Văn phòng đăng ký đất đai",
)
MAX_CALLS = 10


class ProbeError(RuntimeError):
    pass


class Probe:
    def __init__(self, settings: LocationSettings):
        self.settings = settings
        self.calls = 0
        self.client = httpx.Client(
            base_url=BASE_URL,
            timeout=settings.goong_timeout_seconds,
            follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": "legal-assistant-location-probe/1"},
        )

    def close(self) -> None:
        self.client.close()

    def get(self, path: str, params: dict[str, object]) -> dict:
        if self.calls >= MAX_CALLS:
            raise ProbeError("Đã chạm giới hạn an toàn 10 request.")
        self.calls += 1
        safe_params = dict(params)
        safe_params["api_key"] = self.settings.goong_api_key
        try:
            response = self.client.get(path, params=safe_params)
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                raise ProbeError("Goong trả response vượt giới hạn 2 MB.")
            payload = response.json()
        except httpx.TimeoutException:
            raise ProbeError("Goong quá thời gian chờ.") from None
        except httpx.HTTPStatusError as exc:
            raise ProbeError(f"Goong trả HTTP {exc.response.status_code}.") from None
        except (httpx.HTTPError, ValueError):
            raise ProbeError("Không đọc được response hợp lệ từ Goong.") from None
        if not isinstance(payload, dict) or payload.get("status") != "OK":
            raise ProbeError("Goong trả trạng thái nghiệp vụ không thành công.")
        return payload

    def geocode(self, address: str) -> tuple[float, float]:
        payload = self.get("/v2/geocode", {"address": address})
        results = payload.get("results")
        try:
            location = results[0]["geometry"]["location"]
            latitude, longitude = float(location["lat"]), float(location["lng"])
        except (IndexError, KeyError, TypeError, ValueError):
            raise ProbeError("Geocode không trả tọa độ có thể kiểm tra.") from None
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ProbeError("Geocode trả tọa độ ngoài phạm vi.")
        return latitude, longitude

    def autocomplete(self, keyword: str, latitude: float, longitude: float) -> list[dict]:
        payload = self.get(
            "/v2/place/autocomplete",
            {
                "input": keyword,
                "location": f"{latitude},{longitude}",
                "origin": f"{latitude},{longitude}",
                "radius": 10,
                "limit": 5,
            },
        )
        predictions = payload.get("predictions")
        if not isinstance(predictions, list):
            raise ProbeError("Autocomplete không trả danh sách predictions.")
        return [item for item in predictions if isinstance(item, dict)]

    def detail(self, place_id: str) -> tuple[str, str]:
        payload = self.get("/v2/place/detail", {"place_id": place_id})
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ProbeError("Place Detail không trả result hợp lệ.")
        name, address = result.get("name"), result.get("formatted_address")
        if not isinstance(name, str) or not name.strip() or not isinstance(address, str) or not address.strip():
            raise ProbeError("Place Detail thiếu tên hoặc địa chỉ.")
        return name.strip(), address.strip()


def run_probe(settings: LocationSettings) -> int:
    probe = Probe(settings)
    first_place_ids: list[str] = []
    try:
        for label, address in AREAS:
            latitude, longitude = probe.geocode(address)
            print(f"PASS geocode {label}: tọa độ hợp lệ.")
            for keyword in KEYWORDS:
                predictions = probe.autocomplete(keyword, latitude, longitude)
                print(f"{label} | {keyword}: {len(predictions)} kết quả.")
                if predictions and len(first_place_ids) < 2:
                    place_id = predictions[0].get("place_id")
                    if isinstance(place_id, str) and place_id:
                        first_place_ids.append(place_id)
        for place_id in first_place_ids[:2]:
            name, address = probe.detail(place_id)
            print(f"PASS detail: {name} | {address}")
        print(f"Hoàn tất: {probe.calls} request; kiểm tra quota thực tế trong tài khoản Goong.")
        return 0
    finally:
        probe.close()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-probe",
        action="store_true",
        help="Gửi tối đa 10 request thật và có thể tiêu tốn quota Goong.",
    )
    args = parser.parse_args()
    load_dotenv(SERVICE.parents[1] / ".env", override=False)
    try:
        settings = LocationSettings.from_env()
        if not settings.goong_api_key:
            raise ConfigurationError("Thiếu GOONG_API_KEY trong môi trường hoặc .env.")
        print("PASS: cấu hình Goong hợp lệ; giá trị key được ẩn.")
        if not args.run_probe:
            print("Chưa gọi mạng. Dùng --run-probe để chạy kiểm tra thật có giới hạn.")
            return 0
        return run_probe(settings)
    except (ConfigurationError, ProbeError) as exc:
        print(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
