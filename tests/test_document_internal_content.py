"""API nội bộ GET /internal/v1/documents/{id}/content (Lawyer Service lấy giấy tờ cho admin xem)."""
from uuid import uuid4

import pytest

from test_document_foundation import foundation, config_env  # noqa: F401 (fixture)
from test_document_upload import upload_database, admission_tools  # noqa: F401 (fixture)
from test_document_download_delete import files, create_file, api  # noqa: F401 (fixture)

TOKEN = {"X-Internal-Token": "internal-secret"}


@pytest.fixture(autouse=True)
def internal_token(monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "internal-secret")


def _url(document_id, owner):
    return f"/internal/v1/documents/{document_id}/content?owner_user_id={owner}"


def test_returns_owner_file(api, create_file):
    owner, document_id, content = create_file()
    res = api.get(_url(document_id, owner), headers=TOKEN)
    assert res.status_code == 200 and res.content == content
    assert res.headers["Content-Type"] == "application/pdf"
    assert res.headers["X-Content-Type-Options"] == "nosniff" and res.headers["Cache-Control"] == "no-store"


def test_still_checks_owner_and_scan_status(api, create_file):
    owner, document_id, _ = create_file()
    assert api.get(_url(document_id, uuid4()), headers=TOKEN).status_code == 404
    pending_owner, pending_id, _ = create_file("pending")
    res = api.get(_url(pending_id, pending_owner), headers=TOKEN)
    assert res.status_code == 409 and res.json()["error"]["code"] == "FILE_NOT_READY"


@pytest.mark.parametrize("headers", [{}, {"X-Internal-Token": "wrong"}])
def test_requires_internal_token(api, create_file, headers):
    owner, document_id, _ = create_file()
    assert api.get(_url(document_id, owner), headers=headers).status_code == 401


def test_closed_when_token_not_configured(api, create_file, monkeypatch):
    owner, document_id, _ = create_file()
    monkeypatch.setenv("INTERNAL_TOKEN", "")
    assert api.get(_url(document_id, owner), headers={"X-Internal-Token": ""}).status_code == 401
