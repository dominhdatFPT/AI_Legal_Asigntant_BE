import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from common_auth import CurrentUser, decode_access_token

spec = importlib.util.spec_from_file_location(
    "user_security", Path(__file__).resolve().parents[1] / "app" / "core" / "security.py"
)
security = importlib.util.module_from_spec(spec)
spec.loader.exec_module(security)


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    for name in ("JWT_PRIVATE_KEY_PATH", "JWT_PUBLIC_KEY_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JWT_PRIVATE_KEY", private_pem)
    monkeypatch.setenv("JWT_PUBLIC_KEY", public_pem)


def test_token_ky_o_user_service_duoc_common_auth_chap_nhan():
    token = security.create_access_token("5b1e2c3d", "citizen")
    assert decode_access_token(token) == CurrentUser(id="5b1e2c3d", role="citizen")


def test_moi_token_co_jti_rieng():
    assert security.create_access_token("1", "citizen") != security.create_access_token("1", "citizen")


def test_refresh_token_chi_luu_hash():
    raw, token_hash, expires_at = security.new_refresh_token()
    assert len(raw) >= 60
    assert token_hash == security.hash_refresh_token(raw)
    assert raw not in token_hash
    assert expires_at > datetime.now(timezone.utc)


def test_hai_refresh_token_khac_nhau():
    assert security.new_refresh_token()[0] != security.new_refresh_token()[0]


def test_token_pair_dung_format_openapi():
    pair = security.token_pair("a", "r")
    assert pair == {"access_token": "a", "refresh_token": "r", "token_type": "Bearer", "expires_in": 900}


def test_thieu_private_key_bao_loi(monkeypatch):
    monkeypatch.delenv("JWT_PRIVATE_KEY")
    with pytest.raises(RuntimeError):
        security.create_access_token("1", "citizen")
