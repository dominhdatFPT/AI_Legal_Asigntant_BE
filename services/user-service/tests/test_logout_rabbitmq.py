"""Tests for RabbitMQ event publishing and Token Blacklisting on User Logout."""

import json
from unittest.mock import AsyncMock, patch

import pika
import pytest
from common_auth import ACCESS_COOKIE
from common_auth.core import decode_access_token

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.core import token_blacklist
from app.core.cookies import REFRESH_COOKIE
from app.core.database import get_db
from app.core.rabbitmq import EXCHANGE_NAME, ROUTING_KEY_USER_LOGGED_OUT, get_rabbitmq_url
from app.main import app
from app.models import UserRole
from app.repositories import user_repository as users
from app.services.auth_service import issue_token_pair

AUTH = "/api/v1/users/auth"


def _bearer(pair):
    return {"Authorization": f"Bearer {pair['access_token']}"}


def _cookie_header(res, name: str) -> str:
    for h in res.headers.get_list("set-cookie"):
        if h.strip().startswith(f"{name}="):
            return h
    return ""


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


@pytest.fixture
def client(db):
    def override():
        yield db

    app.dependency_overrides[get_db] = override
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def clean_blacklist():
    token_blacklist.clear_blacklist()
    yield
    token_blacklist.clear_blacklist()


def test_logout_blacklists_access_token(client, db):
    """Sau khi đăng xuất, access token cũ phải bị chặn ngay (401) dù chưa hết hạn 15 phút."""
    user = users.create(db, firebase_uid="fb-bl-1", email="test_bl@example.com")
    pair = issue_token_pair(db, user)
    db.commit()

    # 1. Trước khi logout: gọi /me thành công
    res_me = client.get("/api/v1/users/me", headers=_bearer(pair))
    assert res_me.status_code == 200

    # 2. Thực hiện logout
    res_logout = client.post(f"{AUTH}/logout", json={"refresh_token": pair["refresh_token"]}, headers=_bearer(pair))
    assert res_logout.status_code == 200
    assert res_logout.json()["data"] == {"logged_out": True}

    # 3. Sau khi logout: gọi /me bằng access token cũ bị chặn 401
    res_me_after = client.get("/api/v1/users/me", headers=_bearer(pair))
    assert res_me_after.status_code == 401
    assert res_me_after.json()["error"]["code"] == "UNAUTHORIZED"

    # 4. Refresh token cũng bị thu hồi
    res_refresh = client.post(f"{AUTH}/refresh", json={"refresh_token": pair["refresh_token"]})
    assert res_refresh.status_code == 401


def test_logout_cookie_cleared_and_blacklisted(client, db):
    """Đăng xuất qua HttpOnly cookie: cookie bị xóa (Max-Age=0) và access token bị chặn."""
    user = users.create(db, firebase_uid="fb-cookie-1", email="cookie_user@example.com")
    pair = issue_token_pair(db, user)
    db.commit()

    client.cookies.set(ACCESS_COOKIE, pair["access_token"])
    client.cookies.set(REFRESH_COOKIE, pair["refresh_token"])

    # Gọi /me bằng cookie trước khi logout -> OK
    assert client.get("/api/v1/users/me").status_code == 200

    # Gọi /logout
    res = client.post(f"{AUTH}/logout")
    assert res.status_code == 200
    assert res.json()["data"] == {"logged_out": True}

    # Headers trả về xóa cookie
    assert "max-age=0" in _cookie_header(res, ACCESS_COOKIE).lower()
    assert "max-age=0" in _cookie_header(res, REFRESH_COOKIE).lower()

    # Nếu kẻ gian có cố tình gửi lại access token vừa lấy được -> bị 401
    assert client.get("/api/v1/users/me", headers=_bearer(pair)).status_code == 401


def test_logout_publishes_event_to_rabbitmq(client, db):
    """Kiểm tra sự kiện user.logged_out được gửi lên RabbitMQ thật khi logout."""
    user = users.create(db, firebase_uid="fb-rmq-1", email="rmq_user@example.com")
    pair = issue_token_pair(db, user)
    db.commit()

    # Tạo một test consumer queue tạm thời bind vào topic exchange
    url = get_rabbitmq_url()
    try:
        conn = pika.BlockingConnection(pika.URLParameters(url))
        channel = conn.channel()
        channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="topic", durable=True)
        q = channel.queue_declare(queue="test_verify_logout_queue", exclusive=True)
        channel.queue_bind(
            exchange=EXCHANGE_NAME,
            queue=q.method.queue,
            routing_key=ROUTING_KEY_USER_LOGGED_OUT,
        )
    except Exception as e:
        pytest.skip(f"RabbitMQ không khả dụng trong môi trường test này: {e}")

    # Gọi logout
    res = client.post(
        f"{AUTH}/logout",
        json={"refresh_token": pair["refresh_token"]},
        headers={**_bearer(pair), "X-Request-ID": "req-rmq-verify"},
    )
    assert res.status_code == 200

    # Đọc message từ queue kiểm tra nội dung
    method_frame, header_frame, body = channel.basic_get(queue=q.method.queue, auto_ack=True)
    assert method_frame is not None, "Không nhận được message nào từ RabbitMQ sau khi logout!"

    msg = json.loads(body.decode("utf-8"))
    assert msg["event_type"] == ROUTING_KEY_USER_LOGGED_OUT
    payload = msg["payload"]
    assert payload["user_id"] == str(user.id)
    assert payload["role"] == "citizen"
    assert payload["request_id"] == "req-rmq-verify"
    assert payload["jti"] is not None

    conn.close()


def test_logout_graceful_degradation_when_rabbitmq_down(client, db):
    """Khi RabbitMQ gặp sự cố, API logout vẫn trả về 200, xóa cookie và hủy refresh token bình thường."""
    user = users.create(db, firebase_uid="fb-down-1", email="down_user@example.com")
    pair = issue_token_pair(db, user)
    db.commit()

    with patch("app.core.rabbitmq.publisher.publish", new_callable=AsyncMock) as mock_pub:
        mock_pub.side_effect = Exception("RabbitMQ connection refused")

        res = client.post(
            f"{AUTH}/logout",
            json={"refresh_token": pair["refresh_token"]},
            headers=_bearer(pair),
        )

        assert res.status_code == 200
        assert res.json()["data"] == {"logged_out": True}
        assert "max-age=0" in _cookie_header(res, ACCESS_COOKIE).lower()

        # Token vẫn được bảo vệ (blacklist + revoked refresh token)
        assert client.get("/api/v1/users/me", headers=_bearer(pair)).status_code == 401
        assert client.post(f"{AUTH}/refresh", json={"refresh_token": pair["refresh_token"]}).status_code == 401


def test_logout_blocks_citizen_profile_endpoints(client, db):
    """Sau khi logout, các endpoint citizen-profile cũng phải từ chối token cũ (401)."""
    user = users.create(db, firebase_uid="fb-prof-1", email="prof_user@example.com")
    pair = issue_token_pair(db, user)
    db.commit()

    # Trước khi logout -> truy cập bình thường
    assert client.get("/api/v1/users/me/citizen-profile", headers=_bearer(pair)).status_code == 200

    # Thực hiện logout
    res_logout = client.post(f"{AUTH}/logout", json={"refresh_token": pair["refresh_token"]}, headers=_bearer(pair))
    assert res_logout.status_code == 200

    # Sau khi logout -> cả GET và PUT đều bị chặn 401
    res_get = client.get("/api/v1/users/me/citizen-profile", headers=_bearer(pair))
    assert res_get.status_code == 401
    assert res_get.json()["error"]["code"] == "UNAUTHORIZED"

    valid_body = {
        "id_number": "001095012345",
        "full_name": "NGUYỄN VĂN AN",
        "date_of_birth": "1995-08-15",
        "gender": "Nam",
        "nationality": "Việt Nam",
        "issue_date": "2021-09-20",
        "issue_place": "Cục Cảnh sát QLHC về TTXH",
        "expiry_date": "2035-08-15",
        "permanent_address": {
            "detail": "Số 45 ngõ 120 Hoàng Hoa Thám",
            "ward": "Phường Ngọc Hà",
            "province": "Thành phố Hà Nội",
        },
        "current_same_as_permanent": True,
        "current_address": None,
        "phone": "0912345678",
        "source": "manual",
        "declared": True,
        "consented": True,
    }
    res_put = client.put(
        "/api/v1/users/me/citizen-profile",
        json=valid_body,
        headers=_bearer(pair),
    )
    assert res_put.status_code == 401
    assert res_put.json()["error"]["code"] == "UNAUTHORIZED"
