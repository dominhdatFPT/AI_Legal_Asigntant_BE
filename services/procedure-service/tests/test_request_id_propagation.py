from unittest.mock import Mock

import pytest
from common_auth.context import request_id_var

from app.services import profile_binding
from app.services.profile_binding import load_profile_values as REAL_LOAD


@pytest.mark.parametrize("request_id", [None, "req-profile"])
def test_profile_service_propagates_context_request_id(monkeypatch, request_id):
    monkeypatch.setenv("INTERNAL_TOKEN", "test-profile-token")
    response = Mock()
    response.json.return_value = {"data": {"id_number": "012345678901"}}
    http = Mock()
    http.get.return_value = response
    monkeypatch.setattr(profile_binding, "http", http)
    token = request_id_var.set(request_id)
    try:
        assert REAL_LOAD("user")["profile.cccd_number"] == "012345678901"
    finally:
        request_id_var.reset(token)
    expected = {"X-Internal-Token": "test-profile-token"}
    if request_id:
        expected["X-Request-ID"] = request_id
    assert http.get.call_args.kwargs["headers"] == expected
