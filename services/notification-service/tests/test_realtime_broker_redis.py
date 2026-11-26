import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.core.realtime_broker import RealtimeBroker, start_redis_listener, CHANNEL, INSTANCE_ID


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_realtime_broker_push_to_user_without_redis(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    broker = RealtimeBroker()
    sub_u1 = broker.subscribe("u1", "user")
    sub_u2 = broker.subscribe("u2", "user")

    broker.push_to_user("u1", {"title": "Hello User 1"})
    assert not sub_u1[2].empty()
    assert sub_u1[2].get_nowait()["title"] == "Hello User 1"
    assert sub_u2[2].empty()


def test_realtime_broker_push_to_user_with_redis(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    broker = RealtimeBroker()
    sub = broker.subscribe("u1", "user")

    mock_client = MagicMock()
    with patch("redis.Redis.from_url", return_value=mock_client) as mock_from_url:
        broker.push_to_user("u1", {"title": "Hello User 1 with Redis"})
        broker.push_to_user("u1", {"title": "Second push"})
        mock_from_url.assert_called_once()
        assert mock_client.publish.call_count == 2
        args = mock_client.publish.call_args_list[0][0]
        assert args[0] == CHANNEL
        payload = json.loads(args[1])
        assert payload["kind"] == "user"
        assert payload["target"] == "u1"
        assert payload["notification"]["title"] == "Hello User 1 with Redis"
        mock_client.close.assert_not_called()
        broker.close()
        mock_client.close.assert_called_once()

    # Local subscriber in same process still received both notifications
    assert not sub[2].empty()
    assert sub[2].get_nowait()["title"] == "Hello User 1 with Redis"
    assert sub[2].get_nowait()["title"] == "Second push"


def test_realtime_broker_recovers_after_publish_error(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    broker = RealtimeBroker()

    bad_client = MagicMock()
    bad_client.publish.side_effect = ConnectionError("redis dropped")
    good_client = MagicMock()

    with patch("redis.Redis.from_url", side_effect=[bad_client, good_client]) as mock_from_url:
        broker.push_to_user("u1", {"title": "First"})
        bad_client.close.assert_called_once()
        broker.push_to_user("u1", {"title": "Second"})
        assert mock_from_url.call_count == 2
        good_client.publish.assert_called_once()
    broker.close()


def test_realtime_broker_push_to_users_uses_pipeline(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    broker = RealtimeBroker()
    sub1 = broker.subscribe("u1", "citizen")
    sub2 = broker.subscribe("u2", "lawyer")

    mock_pipe = MagicMock()
    mock_client = MagicMock()
    mock_client.pipeline.return_value = mock_pipe

    with patch("redis.Redis.from_url", return_value=mock_client):
        broker.push_to_users([("u1", {"id": "n1", "title": "A"}), ("u2", {"id": "n2", "title": "B"})])
        mock_client.pipeline.assert_called_once_with(transaction=False)
        assert mock_pipe.publish.call_count == 2
        mock_pipe.execute.assert_called_once()

    assert sub1[2].get_nowait()["id"] == "n1"
    assert sub2[2].get_nowait()["id"] == "n2"
    broker.close()


def test_realtime_broker_push_to_audience_with_redis(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    broker = RealtimeBroker()
    sub_user = broker.subscribe("u1", "citizen")
    sub_lawyer = broker.subscribe("u2", "lawyer")

    mock_client = MagicMock()
    with patch("redis.Redis.from_url", return_value=mock_client):
        broker.push_to_audience("lawyer", {"title": "Notice to Lawyers"})
        mock_client.publish.assert_called_once()
        args = mock_client.publish.call_args[0]
        payload = json.loads(args[1])
        assert payload["kind"] == "audience"
        assert payload["target"] == "lawyer"

    assert sub_user[2].empty()
    assert not sub_lawyer[2].empty()
    assert sub_lawyer[2].get_nowait()["title"] == "Notice to Lawyers"


@pytest.mark.anyio
async def test_start_redis_listener_processes_incoming_messages(monkeypatch):
    from app.core import realtime_broker as rb_module

    test_sub = rb_module.realtime_broker.subscribe("test_user_listener", "user")

    messages = [
        # Message from self (should be ignored)
        {"type": "message", "data": json.dumps({"origin": rb_module.INSTANCE_ID, "kind": "user", "target": "test_user_listener", "notification": {"id": 1}})},
        # Message from worker instance (should be delivered)
        {"type": "message", "data": json.dumps({"origin": "other-worker-inst", "kind": "user", "target": "test_user_listener", "notification": {"id": 2, "title": "From Worker"}})},
        # Invalid json message (should be safely ignored)
        {"type": "message", "data": "invalid json"},
    ]

    async def mock_listen():
        for m in messages:
            yield m
        # Cancel after yielding all messages
        raise asyncio.CancelledError()

    mock_pubsub = MagicMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.listen = mock_listen
    mock_pubsub.aclose = AsyncMock()

    mock_client = MagicMock()
    mock_client.pubsub.return_value = mock_pubsub
    mock_client.aclose = AsyncMock()

    with patch("redis.asyncio.Redis.from_url", return_value=mock_client):
        with pytest.raises(asyncio.CancelledError):
            await start_redis_listener("redis://localhost:6379/0")

    mock_pubsub.subscribe.assert_awaited_once_with(CHANNEL)
    assert not test_sub[2].empty()
    received = test_sub[2].get_nowait()
    assert received["id"] == 2
    assert received["title"] == "From Worker"
    assert test_sub[2].empty()
    rb_module.realtime_broker.unsubscribe(test_sub)
