"""Contract kiểm tra tài khoản bằng JWT (role claim + revocation của common_auth), không gọi User Service."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import jwt
import pytest
from common_auth import ALGORITHM, ISSUER
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

SERVICE = Path(__file__).resolve().parents[1]
USER_ID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture(scope="module")
def key_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode()
    public_pem = key.public_key().public_bytes(serialization.Encoding.PEM,
                                               serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return private_pem, public_pem


@pytest.fixture
def mint(monkeypatch, key_pair):
    monkeypatch.setenv("JWT_PUBLIC_KEY", key_pair[1])
    monkeypatch.delenv("JWT_PUBLIC_KEY_PATH", raising=False)

    def make(role):
        now = datetime.now(timezone.utc)
        payload = {"sub": USER_ID, "role": role, "type": "access", "iss": ISSUER,
                   "iat": now, "exp": now + timedelta(minutes=15)}
        return jwt.encode(payload, key_pair[0], algorithm=ALGORITHM)

    return make


@pytest.fixture(autouse=True)
def no_user_service_http(monkeypatch):
    """Mọi request HTTP thật đều raise và được ghi lại: chứng minh không còn request nào tới user-service."""
    calls = []

    def blocked(self, request, *args, **kwargs):
        calls.append(str(request.url))
        raise AssertionError("Không được gọi HTTP tới user-service: " + str(request.url))

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
    yield calls
    assert calls == []


@pytest.fixture
def contract():
    # Nhiều service cùng đặt tên package `app`: nạp bản của Contract rồi khôi phục như fixture foundation của Document.
    previous = {name: module for name, module in list(sys.modules.items()) if name == "app" or name.startswith("app.")}
    for name in previous:
        del sys.modules[name]
    sys.path.insert(0, str(SERVICE))
    try:
        from app.core import security
        from app.core.errors import ContractError, install_handlers
        from app.services.admin_gate import AdminGate
        from app.services.user_gate import UserGate

        app = FastAPI()
        install_handlers(app)

        @app.get("/admin-only")
        def admin_only(user=Depends(security.catalog_admin)):
            return {"role": user.role}

        @app.post("/create-dossier")
        async def create_dossier(user=Depends(security.dossier_creator)):
            return {"id": user.id}

        yield SimpleNamespace(client=TestClient(app), ContractError=ContractError,
                              AdminGate=AdminGate, UserGate=UserGate)
    finally:
        sys.path.remove(str(SERVICE))
        for name in list(sys.modules):
            if name == "app" or name.startswith("app."):
                del sys.modules[name]
        sys.modules.update(previous)


@pytest.mark.parametrize("role", ["citizen", "lawyer"])
def test_admin_gate_rejects_non_admin_role(contract, mint, role):
    with pytest.raises(contract.ContractError) as exc:
        contract.AdminGate("", ).ensure_admin(USER_ID, mint(role), "req-test")
    assert exc.value.status == 403 and exc.value.code == "FORBIDDEN"


def test_admin_gate_accepts_admin_role(contract, mint):
    assert contract.AdminGate("").ensure_admin(USER_ID, mint("admin"), "req-test") is None


def test_admin_gate_rejects_token_of_another_user(contract, mint):
    with pytest.raises(contract.ContractError) as exc:
        contract.AdminGate("").ensure_admin("33333333-3333-4333-8333-333333333333", mint("admin"), "req-test")
    assert exc.value.status == 401 and exc.value.code == "UNAUTHORIZED"


def test_admin_gate_rejects_invalid_token(contract, mint):
    with pytest.raises(contract.ContractError) as exc:
        contract.AdminGate("").ensure_admin(USER_ID, "not-a-jwt", "req-test")
    assert exc.value.status == 401 and exc.value.code == "UNAUTHORIZED"


@pytest.mark.parametrize("role", ["citizen", "lawyer"])
def test_admin_route_forbids_non_admin(contract, mint, role):
    response = contract.client.get("/admin-only", headers={"Authorization": "Bearer " + mint(role)})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_admin_route_allows_admin(contract, mint):
    response = contract.client.get("/admin-only", headers={"Authorization": "Bearer " + mint("admin")})
    assert response.status_code == 200 and response.json() == {"role": "admin"}


def test_dossier_creator_passes_without_user_status_call(contract, mint):
    response = contract.client.post("/create-dossier", headers={"Authorization": "Bearer " + mint("citizen")})
    assert response.status_code == 200 and response.json() == {"id": USER_ID}


def test_user_gate_keeps_signature_and_allows(contract):
    assert contract.UserGate("", "").ensure_can_create(USER_ID, "req-test") is None
