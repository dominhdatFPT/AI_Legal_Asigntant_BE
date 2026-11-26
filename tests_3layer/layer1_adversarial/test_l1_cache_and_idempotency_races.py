import asyncio
import importlib
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import pytest
import redis

from tests_3layer.support import auth_headers, create_keys, response_data, service_module

QUESTION = "What documents are required to register a property?"
BASE = "/api/v1/agent/conversations"
HEADERS = {"Idempotency-Key": "same-submission-key"}


def test_forget_user_invalidates_an_inflight_success(monkeypatch):
    user_id = str(uuid4())
    token = jwt.encode({"sub": user_id}, "synthetic-key-for-test-signing-only", algorithm="HS256")
    checked, release = Event(), Event()
    calls = []
    monkeypatch.setenv("USER_ACTIVE_CACHE_SECONDS", "30")

    with service_module("consultation", "app.services.user_client") as client:
        def upstream(service, method, path, *, bearer):
            calls.append((service, method, path, bearer))
            if len(calls) == 1:
                return {"id": user_id}
            raise client.UpstreamError(403, client.ACCOUNT_LOCKED, "Synthetic account is locked")

        original_decode = client.jwt.decode

        def pause_after_check(*args, **kwargs):
            checked.set()
            assert release.wait(5)
            return original_decode(*args, **kwargs)

        monkeypatch.setattr(client, "call", upstream)
        monkeypatch.setattr(client.jwt, "decode", pause_after_check)
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(client.ensure_active, token)
            try:
                assert checked.wait(5)
                client.forget_user(user_id)
            finally:
                release.set()
            first.result(timeout=5)
        with pytest.raises(client.ApiError) as result:
            client.ensure_active(token)
        assert result.value.code == client.ACCOUNT_LOCKED
        assert calls == [("USER", "GET", "/api/v1/users/me", token)] * 2


class RedisFault:
    def __init__(self):
        self.values = {}
        self.fail_write = False
        self.failed_writes = 0
        self.missed_keys = []

    def get(self, key):
        value = self.values.get(key)
        if value is None:
            self.missed_keys.append(key)
        return value

    def set(self, key, value, **kwargs):
        if self.fail_write:
            self.failed_writes += 1
            raise redis.ConnectionError("Synthetic Redis write outage")
        self.values[key] = value


@pytest.fixture
def agent_app(monkeypatch, tmp_path):
    keys = tmp_path / "jwt"
    create_keys(keys)
    subject = uuid4()
    for key, value in {
        "JWT_PUBLIC_KEY_PATH": str(keys / "jwt_public.pem"),
        "AGENT_DATABASE_URL": f"sqlite:///{(tmp_path / 'agent.sqlite').as_posix()}",
        "DAILY_TOKEN_QUOTA_ENABLED": "false",
        "LLM_BYPASS": "true",
        "RELEVANCE_CHECK": "false",
        "RAG_CACHE_ENABLED": "false",
        "HISTORY_CACHE_ENABLED": "false",
        "RABBITMQ_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    with service_module("agent", "app.main") as module:
        db = importlib.import_module("app.core.database")
        conv = importlib.import_module("app.api.v1.conversations")
        cache = importlib.import_module("app.services.cache")
        db.Base.metadata.create_all(db.get_engine())
        yield module.app, conv, cache, str(subject), auth_headers(keys, subject, "citizen")
        db.get_engine().dispose()


def fake_agent(monkeypatch, conversations):
    calls = []

    async def answer(content, *args, **kwargs):
        calls.append(content)
        await asyncio.sleep(0.15)
        return {"content": "Synthetic response", "metadata": {"tokens_used": 0}}

    monkeypatch.setattr(conversations.agent, "process_message", answer)
    return calls


def test_eight_same_key_requests_write_exactly_one_pair(agent_app, monkeypatch):
    app, conversations, _, _, auth = agent_app
    calls = fake_agent(monkeypatch, conversations)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://agent.test",
            headers=auth,
        ) as client:
            cid = response_data(await client.post(BASE, json={}), 201)["id"]
            barrier = asyncio.Barrier(8)

            async def send():
                await barrier.wait()
                return await client.post(f"{BASE}/{cid}/messages", json={"content": QUESTION}, headers=HEADERS)

            replies = await asyncio.wait_for(asyncio.gather(*(send() for _ in range(8))), 15)
            detail = response_data(await client.get(f"{BASE}/{cid}"), 200)
            assert len(detail["messages"]) == 2, [reply.status_code for reply in replies]
            assert calls == [QUESTION]
            saved_ids = {response_data(reply, 200)["assistant_message"]["id"] for reply in replies}
            assert len(saved_ids) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["write-outage", "corrupt-json", "corrupt-envelope"])
def test_redis_fault_replay_and_changed_payload(agent_app, monkeypatch, fault):
    app, conversations, cache, subject, auth = agent_app
    calls = fake_agent(monkeypatch, conversations)
    redis_client = RedisFault()
    clock = SimpleNamespace(now=500.0)
    monkeypatch.setattr(cache, "_get_client", lambda: redis_client)
    monkeypatch.setattr(cache, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("CACHE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_BREAKER_SECONDS", "30")
    importlib.import_module("app.core.config").get_settings.cache_clear()

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://agent.test",
            headers=auth,
        ) as client:
            cid = response_data(await client.post(BASE, json={}), 201)["id"]
            key = cache.make_key("idempotency", (subject, cid, HEADERS["Idempotency-Key"]))
            if fault == "corrupt-json":
                redis_client.values[key] = "{broken"
            elif fault == "corrupt-envelope":
                redis_client.values[key] = '{"digest":"invalid","data":{"assistant_message":null}}'
            else:
                redis_client.fail_write = True
            path = f"{BASE}/{cid}/messages"
            first = response_data(await client.post(path, json={"content": QUESTION}, headers=HEADERS), 200)
            if fault == "write-outage":
                assert redis_client.failed_writes == 1 and key not in redis_client.values
                redis_client.fail_write = False
                clock.now += 31.0
            replay = response_data(await client.post(path, json={"content": QUESTION}, headers=HEADERS), 200)
            assert replay == first
            assert key in redis_client.values and "assistant_message" in redis_client.values[key]
            if fault == "write-outage":
                assert redis_client.missed_keys.count(key) >= 2
            conflict = await client.post(path, json={"content": QUESTION[:-1] + "!"}, headers=HEADERS)
            assert conflict.status_code == 409, conflict.text
            assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
            assert calls == [QUESTION]
            assert len(response_data(await client.get(f"{BASE}/{cid}"), 200)["messages"]) == 2
    asyncio.run(scenario())
