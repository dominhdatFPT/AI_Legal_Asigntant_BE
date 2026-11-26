import time

import jwt
import pytest
from common_auth import revocation
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.auth import AuthError, _ISSUER, _load_public_key, decode_access_token


@pytest.fixture
def access_token(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key_path = tmp_path / "jwt_public.pem"
    public_key_path.write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", str(public_key_path))
    monkeypatch.setenv("RABBITMQ_ENABLED", "false")
    monkeypatch.setattr(revocation, "CHECK_BLOCKED", True)
    _load_public_key.cache_clear()
    revocation.clear()
    now = int(time.time())
    try:
        yield jwt.encode({
            "sub": "rag-user", "role": "admin", "type": "access", "iss": _ISSUER,
            "iat": now - 5, "exp": now + 60, "jti": "rag-test-jti",
        }, key, algorithm="RS256")
    finally:
        _load_public_key.cache_clear()
        revocation.clear()


def test_valid_access_token_decodes(access_token):
    assert decode_access_token(access_token).sub == "rag-user"


def test_revoked_jti_is_rejected(access_token):
    revocation.revoke_jti("rag-test-jti", time.time() + 60)
    with pytest.raises(AuthError) as exc:
        decode_access_token(access_token)
    assert exc.value.code == "UNAUTHORIZED"


def test_blocked_user_token_is_rejected(access_token):
    revocation.block_user("rag-user")
    with pytest.raises(AuthError) as exc:
        decode_access_token(access_token)
    assert exc.value.code == "UNAUTHORIZED"
