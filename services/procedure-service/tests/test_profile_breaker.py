from unittest.mock import Mock

import pytest
import requests
from common_auth import breaker
from fastapi import HTTPException

from app.services import profile_binding
from app.services.profile_binding import load_profile_values as REAL_LOAD


@pytest.fixture
def upstream(monkeypatch):
    breaker.reset_all()
    monkeypatch.setenv("INTERNAL_TOKEN", "test-profile-token")
    http = Mock()
    monkeypatch.setattr(profile_binding, "http", http)
    yield http.get
    breaker.reset_all()


def test_sixth_profile_lookup_fails_without_http(upstream):
    upstream.side_effect = requests.ConnectionError("unavailable")
    for _ in range(6):
        with pytest.raises(HTTPException) as caught:
            REAL_LOAD("user")
        assert caught.value.status_code == 503
        assert caught.value.detail == "Chưa xác nhận được hồ sơ cá nhân. Vui lòng thử lại."
    assert upstream.call_count == 5


def test_profile_404_does_not_trip_the_breaker(upstream):
    missing = requests.Response()
    missing.status_code = 404
    upstream.return_value = missing
    for _ in range(6):
        with pytest.raises(HTTPException) as caught:
            REAL_LOAD("user")
        assert caught.value.status_code == 503
    assert upstream.call_count == 6
