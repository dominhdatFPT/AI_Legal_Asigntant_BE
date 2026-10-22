"""VNPT eKYC đọc trước, OCR local là backup; mọi gọi VNPT ở đây đều qua httpx.MockTransport."""
import time

import httpx
import pytest

from test_document_foundation import foundation
from test_document_identity_card_match import mrz_raw

FRONT = {"id": "099000000001", "name": "TEST PERSON", "birth_day": "01/01/2000", "gender": "Nam",
         "nationality": "Việt Nam", "recent_location": "-", "issue_date": "-", "issue_place": "-",
         "origin_location": "-", "valid_date": "-", "type_id": 6}
BACK = {"issue_date": "01/07/2024", "issue_place": "BỘ CÔNG AN", "expiry_date": "01/01/2030",
        "recent_location": "1 Đường A\nPhường B, TP C", "place_birth": "TP C",
        "mrz": [line["text"] for line in mrz_raw()["lines"]]}
EMPTY = {"full_name": None, "identity_number": None, "date_of_birth": None, "issue_date": None,
         "issue_place": None, "permanent_address": None}
LOCAL_FRONT = {**EMPTY, "identity_number": "099000000002", "full_name": "LOCAL PERSON"}


@pytest.fixture
def ekyc_env(monkeypatch):
    for name, value in {"EKYC_ENABLED": "1", "EKYC_ACCESS_TOKEN": "Bearer access-secret",
                        "EKYC_TOKEN_ID": "token-id", "EKYC_TOKEN": "token-key"}.items():
        monkeypatch.setenv(name, value)


class Local:
    """OCR local giả: trả fields/raw cố định hoặc ném lỗi."""
    request_namespace = "local"

    def __init__(self, fields=None, raw=None, error=None):
        self.fields, self.raw, self.error, self.calls = fields, raw or {"lines": []}, error, 0

    def read_image(self, image, *, deadline, on_response):
        self.calls += 1
        if self.error:
            raise self.error
        on_response(self.raw)
        return dict(self.fields)


def image(side):
    from app.services.identity_images import IdentityImage
    return IdentityImage(side, "image/jpeg", "0" * 64, b"jpeg")


def vnpt(objects, calls, status=200):
    def handler(request):
        calls.append(request)
        if request.url.path == "/file-service/v1/addFile":
            return httpx.Response(200, json={"message": "IDG-00000000", "object": {"hash": "file-hash"}})
        side = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(status, json={"statusCode": status, "object": objects.get(side), "errors": ["x"]})
    return httpx.MockTransport(handler)


def read(local, transport, side="front"):
    from app.services.ekyc_ocr import EkycFirstOcr, EkycSettings
    saved = []
    fields = EkycFirstOcr(local, EkycSettings.from_env(), transport=transport).read_image(
        image(side), deadline=time.monotonic() + 55, on_response=saved.append)
    return fields, saved


def test_disabled_or_missing_token_keeps_local_only(foundation, ekyc_env, monkeypatch):
    from app.services.ekyc_ocr import build_provider
    local = Local()
    monkeypatch.setenv("EKYC_ENABLED", "0")
    assert build_provider(local) is local
    monkeypatch.setenv("EKYC_ENABLED", "1")
    monkeypatch.setenv("EKYC_ACCESS_TOKEN", "")
    assert build_provider(local) is local


def test_vnpt_first_uploads_then_ocr_and_skips_local(foundation, ekyc_env):
    from app.services.identity_parser import parse_additional_fields, parse_fields
    calls, local = [], Local(LOCAL_FRONT)
    fields, saved = read(local, vnpt({"front": FRONT}, calls))
    assert local.calls == 0
    assert [c.url.path for c in calls] == ["/file-service/v1/addFile", "/ai/v1/ocr/id/front"]
    assert calls[1].headers["Authorization"] == "bearer access-secret"
    assert calls[1].headers["Token-id"] == "token-id" and calls[1].headers["Token-key"] == "token-key"
    assert b'"img_front":"file-hash"' in calls[1].content.replace(b" ", b"")
    assert fields == {**EMPTY, "full_name": "TEST PERSON", "identity_number": "099000000001",
                      "date_of_birth": "2000-01-01"}
    assert saved[0]["engine"] == "vnpt-ekyc" and parse_fields(saved[0]) == fields
    assert parse_additional_fields(saved[0])["sex"] == "male"


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_vnpt_unusable_switches_to_local_without_retry(foundation, ekyc_env, status):
    calls, local = [], Local(LOCAL_FRONT)
    fields, saved = read(local, vnpt({"front": FRONT}, calls, status=status))
    assert fields == LOCAL_FRONT and saved == [local.raw] and local.calls == 1 and len(calls) == 2


def test_vnpt_network_error_switches_to_local(foundation, ekyc_env):
    def down(request):
        raise httpx.ConnectError("down")
    local = Local(LOCAL_FRONT)
    assert read(local, httpx.MockTransport(down))[0] == LOCAL_FRONT


def test_vnpt_incomplete_uses_complete_local_else_keeps_vnpt(foundation, ekyc_env):
    partial = {**FRONT, "id": "-"}
    fields, _ = read(Local(LOCAL_FRONT), vnpt({"front": partial}, []))
    assert fields == LOCAL_FRONT
    fields, saved = read(Local({**EMPTY}), vnpt({"front": partial}, []))
    assert fields["full_name"] == "TEST PERSON" and saved[0]["engine"] == "vnpt-ekyc"


def test_both_fail_raises_local_error(foundation, ekyc_env):
    from app.core.errors import DocumentError
    with pytest.raises(DocumentError) as caught:
        read(Local(error=DocumentError("OCR_TIMEOUT", "x", 504)), vnpt({}, [], status=401))
    assert caught.value.code == "OCR_TIMEOUT"


def test_back_from_vnpt_keeps_mrz_pair_check(foundation, ekyc_env):
    from app.services.identity_card_match import ensure_card_pair
    from app.services.identity_parser import parse_additional_fields
    local = Local({**EMPTY})
    fields, saved = read(local, vnpt({"back": BACK}, []), side="back")
    assert local.calls == 0
    assert fields["issue_date"] == "2024-07-01" and fields["identity_number"] is None
    assert fields["permanent_address"] == "1 Đường A, Phường B, TP C"
    assert parse_additional_fields(saved[0])["expiry_date"] == "2030-01-01"
    front = {"identity_number": "099000000001", "full_name": "TEST PERSON", "date_of_birth": "2000-01-01"}
    assert ensure_card_pair(front, {"lines": []}, saved[0])["status"] == "matched"


def test_settings_hide_tokens(foundation, ekyc_env):
    from app.services.ekyc_ocr import EkycSettings
    settings = EkycSettings.from_env()
    assert settings.access_token == "access-secret" and "access-secret" not in repr(settings)
