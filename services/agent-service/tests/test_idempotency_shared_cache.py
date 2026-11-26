import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.api.v1 import conversations
from app.services import cache
from tests.test_cost_gate_and_budget import BASE, SIMPLE_Q, USER_A, client, fake_agent, new_conversation  # noqa: F401

KEY = "idempotency-shared-test"


@pytest.fixture
def store(monkeypatch):
    backend = cache.TTLCache(1000, 300)
    make = Mock(return_value=backend)
    monkeypatch.setattr(cache, "make_cache", make)
    return backend, make


def test_post_stores_json_data(client, fake_agent, store):
    backend, make = store
    cid = new_conversation(client)
    response = client.post(f"{BASE}/{cid}/messages", json={"content": SIMPLE_Q}, headers={"Idempotency-Key": KEY})
    assert response.status_code == 200, response.text
    stored = json.loads(json.dumps(backend.get((USER_A, cid, KEY))))
    assert set(stored) == {"digest", "data"}
    assert stored["digest"] == hashlib.sha256(SIMPLE_Q.encode()).hexdigest()
    assert stored["data"]["assistant_message"]["id"] == response.json()["data"]["assistant_message"]["id"]
    make.assert_called_once_with("idempotency", conversations.IDEMPOTENCY_MAX, conversations.IDEMPOTENCY_TTL)


def test_post_replays_a_record_restored_from_json(client, fake_agent, store):
    backend, _ = store
    cid = new_conversation(client)
    message = {"created_at": datetime.now(timezone.utc).isoformat(), "metadata": None}
    data = {
        "user_message": {**message, "id": str(uuid4()), "role": "user", "content": SIMPLE_Q},
        "assistant_message": {**message, "id": str(uuid4()), "role": "assistant", "content": "Saved answer"},
    }
    entry = {"digest": hashlib.sha256(SIMPLE_Q.encode()).hexdigest(), "data": data}
    backend.set((USER_A, cid, KEY), json.loads(json.dumps(entry)))

    response = client.post(f"{BASE}/{cid}/messages", json={"content": SIMPLE_Q}, headers={"Idempotency-Key": KEY})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["assistant_message"]["id"] == data["assistant_message"]["id"]
    assert response.json()["data"]["assistant_message"]["content"] == "Saved answer"
    assert fake_agent == []
    conflict = client.post(f"{BASE}/{cid}/messages", json={"content": "Other"}, headers={"Idempotency-Key": KEY})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert fake_agent == []


def test_post_replays_locally_and_backfills_shared_cache_after_redis_recovers(client, fake_agent, monkeypatch):
    recovered = cache.TTLCache(1000, 300)
    shared = Mock(get=recovered.get, set=Mock(return_value=False))
    monkeypatch.setattr(cache, "make_cache", Mock(return_value=shared))
    cid = new_conversation(client)
    path = f"{BASE}/{cid}/messages"
    headers = {"Idempotency-Key": KEY}
    body = {"content": SIMPLE_Q}

    first = client.post(path, json=body, headers=headers)
    assert first.status_code == 200, first.text
    assert shared.set.call_count == 1
    assert recovered.get((USER_A, cid, KEY)) is None
    shared.set.side_effect = recovered.set

    second = client.post(path, json=body, headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["data"] == first.json()["data"]
    assert len(fake_agent) == 1
    assert len(client.get(f"{BASE}/{cid}").json()["data"]["messages"]) == 2
    assert recovered.get((USER_A, cid, KEY))["data"] == first.json()["data"]


def test_stream_saves_shared_result_and_rejects_conflicts_before_streaming(client, monkeypatch, store):
    backend, _ = store
    cid = new_conversation(client)
    path = f"{BASE}/{cid}/messages"
    headers = {"Idempotency-Key": KEY}
    body = {"content": SIMPLE_Q}

    generate = AsyncMock(return_value={"content": "Saved stream answer", "metadata": {}})
    monkeypatch.setattr(conversations.agent, "prepare", generate)
    original = client.post(f"{path}/stream", json=body, headers=headers)
    assert original.status_code == 200, original.text
    replay = client.post(f"{path}/stream", json=body, headers=headers)
    assert replay.status_code == 200
    assert replay.text == original.text
    data = next(
        json.loads(frame.split("data: ", 1)[1])
        for frame in original.text.split("\n\n")
        if frame.startswith("event: done\n")
    )
    assert backend.get((USER_A, cid, KEY))["data"] == data

    post = client.post(path, json=body, headers=headers)
    assert post.status_code == 200
    assert post.json()["data"] == data
    conflict = client.post(f"{path}/stream", json={"content": "Other"}, headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert conflict.headers["content-type"].startswith("application/json")
    assert len(client.get(f"{BASE}/{cid}").json()["data"]["messages"]) == 2
    generate.assert_awaited_once()


@pytest.mark.parametrize("entry", ["invalid", {"data": {}}, {"digest": "abc"}, {"digest": "abc", "data": {}}])
def test_corrupt_cache_entry_is_a_miss(client, fake_agent, store, entry):
    backend, _ = store
    cid = new_conversation(client)
    backend.set((USER_A, cid, KEY), entry)
    response = client.post(f"{BASE}/{cid}/messages", json={"content": SIMPLE_Q}, headers={"Idempotency-Key": KEY})
    assert response.status_code == 200, response.text
    assert len(fake_agent) == 1


def test_reset_idempotency_builds_a_new_backend(monkeypatch):
    first, second = Mock(), Mock()
    make = Mock(side_effect=[first, second])
    monkeypatch.setattr(cache, "make_cache", make)
    assert conversations._idempotency_cache() is first
    assert conversations._idempotency_cache() is first
    local = conversations._local_idempotency_cache()
    local.set(KEY, {"saved": True})
    conversations.reset_idempotency()
    assert conversations._idempotency_cache() is second
    assert conversations._local_idempotency_cache().get(KEY) is None
    assert make.call_count == 2
