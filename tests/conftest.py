"""Key JWT chỉ sinh trong RAM để test, không dùng/lưu key thật của nhóm."""
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from common_auth import ALGORITHM, ISSUER


@pytest.fixture(scope="session")
def service_key_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


@pytest.fixture
def service_auth(monkeypatch, service_key_pair):
    monkeypatch.setenv("JWT_PUBLIC_KEY", service_key_pair[1])
    monkeypatch.delenv("JWT_PUBLIC_KEY_PATH", raising=False)

    def mint(**overrides):
        now = datetime.now(timezone.utc)
        payload = {
            "sub": "11111111-1111-4111-8111-111111111111",
            "role": "citizen", "type": "access", "iss": ISSUER,
            "iat": now, "exp": now + timedelta(minutes=15),
        }
        payload.update(overrides)
        return jwt.encode(payload, service_key_pair[0], algorithm=ALGORITHM)

    return mint
