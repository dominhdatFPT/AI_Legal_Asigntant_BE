from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from common_auth import ALGORITHM, ISSUER


def _new_key_pair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


@pytest.fixture(scope="session")
def key_pair():
    return _new_key_pair()


@pytest.fixture(scope="session")
def other_private_key():
    return _new_key_pair()[0]


@pytest.fixture(autouse=True)
def public_key_env(monkeypatch, key_pair):
    monkeypatch.delenv("JWT_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.setenv("JWT_PUBLIC_KEY", key_pair[1])


@pytest.fixture
def make_token(key_pair):
    def _make(signing_key=None, drop=(), **overrides):
        now = datetime.now(timezone.utc)
        payload = {
            "sub": "123",
            "role": "citizen",
            "type": "access",
            "iss": ISSUER,
            "iat": now,
            "exp": now + timedelta(minutes=15),
        }
        payload.update(overrides)
        for field in drop:
            payload.pop(field, None)
        return jwt.encode(payload, signing_key or key_pair[0], algorithm=ALGORITHM)

    return _make


@pytest.fixture
def expired():
    return datetime.now(timezone.utc) - timedelta(minutes=1)
