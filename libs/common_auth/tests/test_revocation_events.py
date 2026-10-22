import asyncio
import os
import time

import pytest

from common_auth import AuthError, decode_access_token, events, revocation


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(revocation, "CHECK_BLOCKED", True)  # user-service tắt cờ này khi import app
    revocation.clear()
    yield
    revocation.clear()


def test_token_da_logout_bi_tu_choi(make_token):
    token = make_token(jti="jti-1")
    decode_access_token(token)
    revocation.revoke_jti("jti-1", time.time() + 60)
    with pytest.raises(AuthError):
        decode_access_token(token)
    # token khác của cùng user vẫn dùng được
    decode_access_token(make_token(jti="jti-2"))


def test_user_bi_khoa_mat_token_cu_nhung_dang_nhap_lai_duoc(make_token):
    old = make_token(sub="u1", iat=int(time.time()) - 60)
    revocation.block_user("u1", time.time() - 30)
    with pytest.raises(AuthError):
        decode_access_token(old)
    decode_access_token(make_token(sub="u1", iat=int(time.time())))  # phát hành sau lúc khoá
    decode_access_token(make_token(sub="u2", iat=int(time.time()) - 60))  # user khác không ảnh hưởng


def test_apply_event_tu_payload():
    asyncio.run(revocation.apply_event("user.logged_out", {"payload": {"jti": "x", "exp": time.time() + 60}}))
    assert revocation.is_jti_revoked("x")
    event = events.make_event("user.blocked", {"user_id": "u9"})
    asyncio.run(revocation.apply_event("user.blocked", event))
    assert revocation.is_revoked({"sub": "u9", "iat": int(time.time()) - 5})


def test_khong_co_url_thi_khong_phat(monkeypatch):
    monkeypatch.delenv("RABBITMQ_URL", raising=False)
    assert asyncio.run(events.EventPublisher().publish("user.logged_out", {})) is False
    assert events.masked("amqp://u:secret@host:5672/") == "host:5672/"


# ---- Chạy với broker thật: RABBITMQ_TEST_URL=amqp://user:pass@localhost:5672/ pytest ----
BROKER = os.getenv("RABBITMQ_TEST_URL")
broker = pytest.mark.skipif(not BROKER, reason="Không đặt RABBITMQ_TEST_URL")


@broker
def test_event_logout_toi_listener_va_handler_loi_vao_dlq(monkeypatch):
    import aio_pika

    monkeypatch.setenv("RABBITMQ_URL", BROKER)

    async def scenario():
        async def broken(routing_key, event):
            raise RuntimeError("handler hỏng")

        listeners = [
            asyncio.create_task(events.consume_forever(None, ["user.logged_out"], revocation.apply_event)),
            asyncio.create_task(events.consume_forever("q.test.broken", ["test.broken"], broken)),
        ]
        await asyncio.sleep(1)
        connection = await aio_pika.connect(BROKER)
        channel = await connection.channel()
        dlq = await channel.declare_queue(events.DEAD_LETTER_QUEUE, passive=True)
        await dlq.purge()

        publisher = events.EventPublisher()
        assert await publisher.publish("user.logged_out", {"jti": "live-jti", "exp": time.time() + 60})
        assert await publisher.publish("test.broken", {"n": 1})
        await asyncio.sleep(1)
        assert revocation.is_jti_revoked("live-jti")

        dead = await dlq.get(no_ack=True, fail=False)
        assert dead is not None and b"test.broken" in dead.body

        await publisher.close()
        await channel.queue_delete("q.test.broken")
        await connection.close()
        for task in listeners:
            task.cancel()
        await asyncio.gather(*listeners, return_exceptions=True)

    asyncio.run(scenario())


@broker
def test_publish_sync_va_publisher_tu_phuc_hoi(monkeypatch):
    monkeypatch.setenv("RABBITMQ_URL", BROKER)
    assert events.publish_event_sync("test.sync", {"ok": True})

    async def scenario():
        publisher = events.EventPublisher()
        assert await publisher.publish("test.x", {})
        await publisher._connection.close()  # broker cắt kết nối
        assert await publisher.publish("test.x", {})  # tự mở kết nối mới
        await publisher.close()

    asyncio.run(scenario())
