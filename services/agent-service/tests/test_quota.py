from datetime import datetime as real_datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from app.services import quota
from app.services import prompts


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}
        self.calls = []
        self.error = None

    def get(self, key):
        self.calls.append(("get", key))
        if self.error:
            raise self.error
        return self.values.get(key)

    def eval(self, script, numkeys, key, amount, ttl):
        self.calls.append(("eval", key, amount, ttl))
        if self.error:
            raise self.error
        assert numkeys == 1
        self.values[key] = int(self.values.get(key, 0)) + amount
        self.ttls.setdefault(key, ttl)
        return self.values[key]


class FakeBreaker:
    def __init__(self):
        self.allowed = True
        self.failures = 0

    def allow(self):
        return self.allowed

    def success(self):
        pass

    def failure(self, exc):
        self.failures += 1


@pytest.fixture
def env(monkeypatch):
    settings = SimpleNamespace(daily_token_limit=10000, daily_token_quota_enabled=True)
    redis, breaker = FakeRedis(), FakeBreaker()
    monkeypatch.setattr(quota, "get_settings", lambda: settings)
    monkeypatch.setattr(quota.cache, "_use_redis", lambda s: True)
    monkeypatch.setattr(quota.cache, "shared_breaker", lambda s: breaker)
    monkeypatch.setattr(quota.cache, "_get_client", lambda: redis)
    return settings, redis, breaker


def test_accumulate_below_limit(env):
    _, redis, _ = env
    quota.check_daily_quota("a")
    quota.add_daily_tokens("a", 123)
    quota.add_daily_tokens("a", 456)
    quota.check_daily_quota("a")
    assert redis.values[quota._key("a")] == 579
    assert redis.ttls[quota._key("a")] == 86400
    assert quota._key("b") not in redis.values


@pytest.mark.parametrize("used", [10000, 10001])
def test_block_at_limit(env, used):
    _, redis, _ = env
    redis.values[quota._key("a")] = used
    with pytest.raises(HTTPException) as error:
        quota.check_daily_quota("a")
    assert error.value.status_code == 429
    assert error.value.detail == {
        "code": "DAILY_QUOTA_EXCEEDED",
        "message": "Bạn đã hết lượt sử dụng AI trong ngày hôm nay. Vui lòng quay lại vào ngày mai!",
    }


def test_new_vietnam_day(env, monkeypatch):
    _, redis, _ = env
    class Clock:
        value = real_datetime(2026, 11, 17, 16, 59, tzinfo=timezone.utc)
        @classmethod
        def now(cls, tz):
            return cls.value.astimezone(tz)
    monkeypatch.setattr(quota, "datetime", Clock)
    quota.add_daily_tokens("a", 10000)
    assert quota._key("a").endswith(":2026-11-17")
    Clock.value = real_datetime(2026, 11, 17, 17, 0, tzinfo=timezone.utc)
    assert quota._key("a").endswith(":2026-11-18")
    quota.check_daily_quota("a")
    quota.add_daily_tokens("a", 7)
    assert redis.values[quota._key("a")] == 7


def test_disabled(env):
    settings, redis, _ = env
    settings.daily_token_quota_enabled = False
    quota.check_daily_quota("a")
    quota.add_daily_tokens("a", 50)
    assert not redis.calls


@pytest.mark.parametrize("operation", ["read", "write"])
def test_redis_error_fails_open(env, operation, caplog):
    _, redis, breaker = env
    redis.error = ConnectionError("offline")
    if operation == "read":
        quota.check_daily_quota("a")
    else:
        quota.add_daily_tokens("a", 50)
    assert breaker.failures == 1
    assert "Quota: Redis lỗi" in caplog.text


def test_open_breaker(env):
    _, redis, breaker = env
    breaker.allowed = False
    quota.check_daily_quota("a")
    quota.add_daily_tokens("a", 10)
    assert not redis.calls


@pytest.mark.parametrize("tokens", [None, 0, -1, True, "5"])
def test_invalid_usage(env, tokens):
    _, redis, _ = env
    quota.add_daily_tokens("a", tokens)
    assert not redis.calls


def test_default_config(monkeypatch):
    from app.core.config import Settings
    monkeypatch.delenv("DAILY_TOKEN_QUOTA_ENABLED", raising=False)
    monkeypatch.delenv("DAILY_TOKEN_LIMIT", raising=False)
    monkeypatch.delenv("TEST_UNLIMITED_USERS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.daily_token_quota_enabled is True
    assert settings.daily_token_limit == 20000
    assert settings.test_unlimited_users == ""


def test_user2026_unlimited_quota(env):
    """Chỉ tài khoản được operator khai báo rõ trong TEST_UNLIMITED_USERS mới được miễn trừ hạn mức."""
    settings, redis, _ = env
    settings.daily_token_limit = 20000
    settings.test_unlimited_users = "user2026,cc31ab7f-8864-4d16-8cd2-b06759797061"
    # Ngay cả khi đã dùng 999999 tokens
    redis.values[quota._key("user2026")] = 999999
    redis.values[quota._key("cc31ab7f-8864-4d16-8cd2-b06759797061")] = 999999
    # Không raise exception
    quota.check_daily_quota("user2026")
    quota.check_daily_quota("cc31ab7f-8864-4d16-8cd2-b06759797061")
    quota.check_daily_quota("some-other-uuid", username="user2026")
    # add_daily_tokens cũng không tích lũy thêm làm tốn tài nguyên
    quota.add_daily_tokens("user2026", 500)
    quota.add_daily_tokens("some-other-uuid", 500, username="user2026")


def test_no_hardcoded_unlimited_bypass_by_default(env):
    """Mặc định không có tài khoản nào được hardcode miễn trừ hạn mức."""
    settings, redis, _ = env
    settings.daily_token_limit = 100
    settings.test_unlimited_users = ""
    redis.values[quota._key("user2026")] = 100
    redis.values[quota._key("cc31ab7f-8864-4d16-8cd2-b06759797061")] = 100
    for uid in ("user2026", "cc31ab7f-8864-4d16-8cd2-b06759797061"):
        with pytest.raises(HTTPException) as err:
            quota.check_daily_quota(uid, username="user2026")
        assert err.value.status_code == 429
        assert err.value.detail["code"] == "DAILY_QUOTA_EXCEEDED"


def test_production_unlimited_matches_only_user_id_not_username(env):
    """Trên production không tin username để miễn trừ quota, chỉ đối chiếu user_id (JWT sub)."""
    settings, redis, _ = env
    settings.environment = "production"
    settings.daily_token_limit = 100
    settings.test_unlimited_users = "user2026,allowed-uuid"
    redis.values[quota._key("attacker-uuid")] = 100
    redis.values[quota._key("allowed-uuid")] = 100

    with pytest.raises(HTTPException) as err:
        quota.check_daily_quota("attacker-uuid", username="user2026")
    assert err.value.status_code == 429
    quota.check_daily_quota("allowed-uuid")


def test_inflight_lock_atomic_redis_and_local_fallback(env, monkeypatch):
    """Khóa nguyên tử theo user chặn request chồng lấn ở cả Redis (SET NX + Lua) lẫn fallback cục bộ."""
    class AtomicFakeRedis:
        def __init__(self):
            self.store = {}
            self.eval_calls = 0

        def set(self, key, value, nx=False, ex=None):
            if nx and key in self.store:
                return False
            self.store[key] = value
            return True

        def get(self, key):
            return self.store.get(key)

        def eval(self, script, numkeys, *args):
            self.eval_calls += 1
            key, expected = args[0], args[1]
            if self.store.get(key) == expected:
                del self.store[key]
                return 1
            return 0

    atomic_redis = AtomicFakeRedis()
    monkeypatch.setattr(quota.cache, "_get_client", lambda: atomic_redis)

    token1 = quota.acquire_inflight_slot("user-1")
    assert token1 and token1.startswith("redis:")
    with pytest.raises(HTTPException) as err:
        quota.acquire_inflight_slot("user-1")
    assert err.value.status_code == 429
    assert err.value.detail["code"] == "AI_REQUEST_IN_PROGRESS"

    # Nhả sai token không được xóa khóa của request đang chạy
    quota.release_inflight_slot("user-1", "redis:wrong-token")
    assert quota._inflight_key("user-1") in atomic_redis.store

    # Nhả đúng token giải phóng khóa nguyên tử
    quota.release_inflight_slot("user-1", token1)
    assert atomic_redis.eval_calls == 2
    assert quota._inflight_key("user-1") not in atomic_redis.store

    # Fallback cục bộ khi Redis chưa bật
    monkeypatch.setattr(quota.cache, "_use_redis", lambda s: False)
    local_tok = quota.acquire_inflight_slot("user-2")
    assert local_tok and local_tok.startswith("local:")
    with pytest.raises(HTTPException) as err2:
        quota.acquire_inflight_slot("user-2")
    assert err2.value.status_code == 429
    assert err2.value.detail["code"] == "AI_REQUEST_IN_PROGRESS"
    quota.release_inflight_slot("user-2", "local:wrong")
    with pytest.raises(HTTPException):
        quota.acquire_inflight_slot("user-2")
    quota.release_inflight_slot("user-2", local_tok)
    local_tok2 = quota.acquire_inflight_slot("user-2")
    quota.release_inflight_slot("user-2", local_tok2)


def test_redis_outage_falls_back_to_local_daily_quota(env):
    """Khi Redis lỗi/sập, bộ đếm cục bộ vẫn cộng dồn và chặn 429 khi chạm ngưỡng thay vì mở khóa vô hạn."""
    settings, redis, _ = env
    settings.daily_token_limit = 100
    redis.error = ConnectionError("offline")
    quota.add_daily_tokens("u-local", 60, target_date="2026-01-02")
    quota.check_daily_quota("u-local", target_date="2026-01-02")
    quota.add_daily_tokens("u-local", 40, target_date="2026-01-02")
    with pytest.raises(HTTPException) as err:
        quota.check_daily_quota("u-local", target_date="2026-01-02")
    assert err.value.status_code == 429
    assert err.value.detail["code"] == "DAILY_QUOTA_EXCEEDED"


def test_concurrent_send_message_blocked_and_quota_enforced(env, monkeypatch):
    """Hai request send_message gửi song song cùng lúc: request thứ 2 bị chặn 429 ngay, không vượt hạn mức."""
    import asyncio
    import uuid
    from app.api.v1 import conversations

    settings, redis, _ = env
    settings.daily_token_limit = 100
    monkeypatch.setattr(conversations, "_load_for_chat", lambda *args: (SimpleNamespace(user_id="race-u"), []))
    monkeypatch.setattr(quota, "get_today_vn", lambda: "2026-01-02")
    monkeypatch.setattr(conversations, "_save_pair", lambda db, conv, q, a, m, t: (
        SimpleNamespace(id=uuid.uuid4(), role="user", content=q, metadata_=None, created_at=t),
        SimpleNamespace(id=uuid.uuid4(), role="assistant", content=a, metadata_=m, created_at=t),
    ))

    entered = asyncio.Event()
    proceed = asyncio.Event()

    async def slow_process(*args, **kwargs):
        entered.set()
        await proceed.wait()
        return {"content": "Trả lời.", "metadata": {"tokens_used": 120, "model": "test", "rag_chunk_ids": []}}

    monkeypatch.setattr(conversations.agent, "process_message", slow_process)

    async def scenario():
        req = SimpleNamespace(content="câu hỏi", document_ids=[])
        cid = uuid.uuid4()
        t1 = asyncio.create_task(conversations.send_message(cid, req, user=None, token=None, db=None, x_request_id=None))
        await entered.wait()
        with pytest.raises(HTTPException) as err_concurrent:
            await conversations.send_message(cid, req, user=None, token=None, db=None, x_request_id=None)
        assert err_concurrent.value.status_code == 429
        assert err_concurrent.value.detail["code"] == "AI_REQUEST_IN_PROGRESS"

        proceed.set()
        res = await t1
        assert res.status_code == 200

        # Sau khi request 1 xong (đã cộng 120 >= 100), request tiếp theo bị chặn DAILY_QUOTA_EXCEEDED
        with pytest.raises(HTTPException) as err_quota:
            await conversations.send_message(cid, req, user=None, token=None, db=None, x_request_id=None)
        assert err_quota.value.status_code == 429
        assert err_quota.value.detail["code"] == "DAILY_QUOTA_EXCEEDED"

    asyncio.run(scenario())


def test_public_chat_enforces_quota_per_ip(env, monkeypatch):
    """Chatbox công khai /api/v1/agent/chat cũng bị giới hạn token ngày theo IP."""
    from fastapi.testclient import TestClient
    import app.main as main_mod

    settings, redis, _ = env
    settings.public_chat_daily_token_limit = 50
    monkeypatch.setattr(main_mod, "get_settings", lambda: settings)

    async def fake_ai(*args, **kwargs):
        return {"content": "Trả lời công khai", "metadata": {"tokens_used": 60, "rag_chunk_ids": []}}

    async def fake_proc(*args, **kwargs):
        return None

    monkeypatch.setattr(main_mod.agent, "process_message", fake_ai)
    monkeypatch.setattr(main_mod, "find_procedure", fake_proc)

    client = TestClient(main_mod.app)
    r1 = client.post("/api/v1/agent/chat", json={"message": "Hỏi lần 1"}, headers={"X-Real-IP": "203.0.113.10"})
    assert r1.status_code == 200
    r2 = client.post("/api/v1/agent/chat", json={"message": "Hỏi lần 2"}, headers={"X-Real-IP": "203.0.113.10"})
    assert r2.status_code == 429
    assert r2.json()["error"]["code"] == "DAILY_QUOTA_EXCEEDED"
    # IP khác vẫn có hạn mức độc lập
    r3 = client.post("/api/v1/agent/chat", json={"message": "IP khác"}, headers={"X-Real-IP": "203.0.113.11"})
    assert r3.status_code == 200


def test_revoked_and_blocked_jwt_rejected_by_agent_service(monkeypatch):
    """Token đã logout (jti) hoặc của user bị admin khóa đều bị agent-service từ chối ngay."""
    import time
    import jwt
    from common_auth import revocation
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from app.core import auth

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = private_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode("utf-8")
    pub_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("utf-8")
    monkeypatch.setenv("JWT_PUBLIC_KEY", pub_pem)

    now = int(time.time())
    tok1 = jwt.encode(
        {"sub": "u-1", "role": "citizen", "type": "access", "iss": "legal-assistant-user-service",
         "iat": now, "exp": now + 600, "jti": "jti-logout-1"},
        priv_pem, algorithm="RS256",
    )
    assert auth.decode_access_token(tok1).sub == "u-1"

    revocation.revoke_jti("jti-logout-1", now + 600)
    with pytest.raises(auth.AuthError) as err1:
        auth.decode_access_token(tok1)
    assert err1.value.code == "UNAUTHORIZED"

    tok2 = jwt.encode(
        {"sub": "u-blocked", "role": "citizen", "type": "access", "iss": "legal-assistant-user-service",
         "iat": now, "exp": now + 600, "jti": "jti-active-2"},
        priv_pem, algorithm="RS256",
    )
    assert auth.decode_access_token(tok2).sub == "u-blocked"
    revocation.block_user("u-blocked", at=now + 1)
    with pytest.raises(auth.AuthError) as err2:
        auth.decode_access_token(tok2)
    assert err2.value.code == "UNAUTHORIZED"


def test_concurrent_send_message_stream_blocked_until_stream_finishes(env, monkeypatch):
    """Hai request send_message_stream gửi đồng thời: request 2 bị chặn 429 cho tới khi stream 1 cộng token và nhả khóa."""
    import asyncio
    import time
    import uuid
    from app.api.v1 import conversations
    from app.services import responder

    settings, redis, _ = env
    settings.daily_token_limit = 100
    monkeypatch.setattr(conversations, "_load_for_chat", lambda *args: (SimpleNamespace(user_id="stream-race-u"), []))
    monkeypatch.setattr(quota, "get_today_vn", lambda: "2026-01-02")
    monkeypatch.setattr(conversations, "_save_pair", lambda db, conv, q, a, m, t: (
        SimpleNamespace(id=uuid.uuid4(), role="user", content=q, metadata_=None, created_at=t),
        SimpleNamespace(id=uuid.uuid4(), role="assistant", content=a, metadata_=m, created_at=t),
    ))

    async def prepare(*args, **kwargs):
        now = time.perf_counter()
        return responder.LLMCall("sys", [], [], "q", None, now, now)

    async def fake_stream(*args, info, **kwargs):
        yield "phần 1 "
        yield "phần 2"
        info.update(tokens=150, model="test")

    monkeypatch.setattr(conversations.agent, "prepare", prepare)
    monkeypatch.setattr(conversations.llm_client, "generate_stream", fake_stream)

    async def scenario():
        req = SimpleNamespace(content="hỏi stream", document_ids=[])
        cid = uuid.uuid4()
        resp = await conversations.send_message_stream(cid, req, user=None, token=None, db=None, x_request_id=None)
        gen = resp.body_iterator
        # Đọc delta đầu tiên: stream đang mở -> khóa vẫn đang giữ
        first_chunk = await anext(gen)
        assert "delta" in first_chunk

        # Request 2 chen vào giữa lúc stream 1 chưa xong -> phải bị chặn 429 AI_REQUEST_IN_PROGRESS
        with pytest.raises(HTTPException) as err_busy:
            await conversations.send_message_stream(cid, req, user=None, token=None, db=None, x_request_id=None)
        assert err_busy.value.status_code == 429
        assert err_busy.value.detail["code"] == "AI_REQUEST_IN_PROGRESS"

        # Đọc hết stream 1 -> cộng 150 tokens (>= 100) rồi mới nhả khóa
        async for _ in gen:
            pass

        # Request 3 sau khi stream 1 kết thúc -> khóa đã nhả nhưng chạm quota 150 >= 100 -> 429 DAILY_QUOTA_EXCEEDED
        with pytest.raises(HTTPException) as err_quota:
            await conversations.send_message_stream(cid, req, user=None, token=None, db=None, x_request_id=None)
        assert err_quota.value.status_code == 429
        assert err_quota.value.detail["code"] == "DAILY_QUOTA_EXCEEDED"

    asyncio.run(scenario())


@pytest.mark.parametrize("usage", [None, 0, -1, True, "5"])
def test_fallback_estimate_and_date(env, usage):
    _, redis, _ = env
    quota.add_daily_tokens("a", usage, target_date="2026-01-02", prompt="12345678", completion="abcd")
    assert redis.values[quota._key("a", "2026-01-02")] == 3
    assert quota.estimate_tokens("x") == 1
    assert quota.estimate_tokens() == 0
    quota.check_daily_quota("a", target_date="2026-01-02")


def test_fallback_client_without_eval(env, monkeypatch):
    class BasicRedis:
        def __init__(self):
            self.values = {}
        def get(self, key):
            return self.values.get(key)
        def set(self, key, value, ex):
            self.values[key] = value
            assert ex == quota.TTL
    client = BasicRedis()
    monkeypatch.setattr(quota.cache, "_get_client", lambda: client)
    quota.add_daily_tokens("a", 5, target_date="2026-01-02")
    quota.add_daily_tokens("a", 7, target_date="2026-01-02")
    assert client.values[quota._key("a", "2026-01-02")] == 12


@pytest.mark.parametrize("endpoint", ["send_message", "send_message_stream"])
def test_endpoints_reject_over_quota(env, monkeypatch, endpoint):
    import asyncio
    import uuid
    from app.api.v1 import conversations
    _, redis, _ = env
    monkeypatch.setattr(conversations, "_load_for_chat", lambda *args: (SimpleNamespace(user_id="a"), []))
    monkeypatch.setattr(quota, "get_today_vn", lambda: "2026-01-02")
    redis.values[quota._key("a", "2026-01-02")] = 10000
    with pytest.raises(HTTPException) as error:
        asyncio.run(getattr(conversations, endpoint)(uuid.uuid4(), SimpleNamespace(content="hello"), user=None, token=None, db=None, x_request_id=None))
    assert error.value.status_code == 429
    assert error.value.detail == {"code": "DAILY_QUOTA_EXCEEDED", "message": quota.MESSAGE}


@pytest.mark.parametrize("cancel", [True, False])
def test_stream_partial_is_counted_on_close_or_error(env, monkeypatch, cancel):
    import asyncio
    from datetime import datetime
    from app.api.v1 import conversations
    _, redis, _ = env
    async def stream(*args, **kwargs):
        yield "partial text"
        raise RuntimeError("stream failed")
    monkeypatch.setattr(conversations.llm_client, "generate_stream", stream)
    call = SimpleNamespace(system_prompt="", history=[], user_content="")
    async def run():
        events = conversations._stream_events(None, SimpleNamespace(user_id="a"),
            SimpleNamespace(content="question", document_ids=[]), "request", datetime.now(), call, None, "2026-01-02")
        assert "delta" in await anext(events)
        if cancel:
            await events.aclose()
        else:
            assert "error" in await anext(events)
            with pytest.raises(StopAsyncIteration):
                await anext(events)
    asyncio.run(run())
    assert redis.values[quota._key("a", "2026-01-02")] == quota.estimate_tokens("question", "partial text")


@pytest.mark.parametrize("metadata,answer", [
    ({"type": "out_of_scope"}, "Không có trích đoạn phù hợp."),
    ({}, prompts.OUT_OF_SCOPE_ANSWER),
    (None, "  " + prompts.OUT_OF_SCOPE_ANSWER + "\n"),
])
@pytest.mark.parametrize("tokens", [6575, None, 0])
def test_out_of_scope_never_increments_quota(env, metadata, answer, tokens):
    _, redis, _ = env
    key = quota._key("a")
    redis.values[key] = 100
    quota.add_daily_tokens("a", tokens, prompt="Vụ An Phát - Minh Long 30 tỷ",
                           completion=answer, metadata=metadata)
    assert redis.values[key] == 100
    assert redis.calls == []  # exemption precedes even fallback estimation


def test_user_prompt_and_arbitrary_flag_do_not_grant_exemption(env):
    _, redis, _ = env
    quota.add_daily_tokens("a", 25, prompt=prompts.OUT_OF_SCOPE_ANSWER,
                           completion="Có thể yêu cầu bồi thường theo trích đoạn.",
                           metadata={"quota_exempt": True})
    assert redis.values[quota._key("a")] == 25


@pytest.fixture
def quota_client(db):
    from fastapi.testclient import TestClient
    from app.api.deps import get_current_user
    from app.core.auth import TokenPayload
    from app.core.database import get_db
    from app.main import app

    user = TokenPayload(sub="11111111-1111-4111-8111-111111111111", role="citizen",
                        type="access", iss="legal-assistant-user-service", iat=0, exp=9999999999)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with TestClient(app) as client:
            yield client, user.sub
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("mode", ["sync", "prepared_stream", "llm_stream", "document_stream"])
@pytest.mark.parametrize("case", ["typed", "fallback", "normal"])
def test_http_quota_policy_and_usage_persistence(env, quota_client, monkeypatch, mode, case):
    import json
    import time
    from app.api.v1 import conversations
    from app.services import responder

    client, user_id = quota_client
    _, redis, _ = env
    metadata = {"tokens_used": 6575, "model": "deepseek-test", "rag_chunk_ids": []}
    if case == "typed":
        metadata["type"] = "out_of_scope"
    answer = prompts.OUT_OF_SCOPE_ANSWER if case == "fallback" else "Kết quả xử lý."
    result = {"content": answer, "metadata": metadata}
    key = quota._key(user_id)
    redis.values[key] = 100
    base = "/api/v1/agent/conversations"
    conversation_id = client.post(base).json()["data"]["id"]

    async def process(*args, **kwargs):
        return result

    async def prepare(*args, **kwargs):
        if mode != "llm_stream":
            return result
        now = time.perf_counter()
        return responder.LLMCall("prompt", [], [], "question", None, now, now,
                                 metadata_extra={"type": "out_of_scope"} if case == "typed" else {})

    async def stream(*args, info, **kwargs):
        yield answer[:20]
        yield answer[20:]
        info.update(tokens=6575, model="deepseek-test")

    monkeypatch.setattr(conversations.agent, "process_message", process)
    monkeypatch.setattr(conversations.agent, "prepare", prepare)
    monkeypatch.setattr(conversations.llm_client, "generate_stream", stream)
    body = {"content": "An Phát và Minh Long tranh chấp hợp đồng 30 tỷ đồng"}
    if mode == "document_stream":
        async def load_documents(*args):
            return [{"text": "Hợp đồng", "source_kind": "pdf_text"}]
        monkeypatch.setattr(conversations, "_load_documents", load_documents)
        body["document_ids"] = ["33333333-3333-4333-8333-333333333333"]
    path = f"{base}/{conversation_id}/messages" + ("" if mode == "sync" else "/stream")
    response = client.post(path, json=body)
    assert response.status_code == 200
    if mode == "sync":
        data = response.json()["data"]
    else:
        events = response.text.split("\n\n")
        done = next(e for e in events if e.startswith("event: done"))
        data = json.loads(done.split("data: ", 1)[1])
    saved = data["assistant_message"]["metadata"]
    exempt = case != "normal"
    assert saved.get("quota_exempt", False) is exempt
    assert saved["tokens_used"] == 6575
    assert redis.values[key] == (100 if exempt else 6675)
    writes = [c for c in redis.calls if c[0] == "eval"]
    assert len(writes) == (0 if exempt else 1)
    persisted = client.get(f"{base}/{conversation_id}").json()["data"]["messages"][-1]
    assert persisted["metadata"] == saved


@pytest.mark.parametrize("streaming", [False, True])
def test_real_agent_relevance_rejection_does_not_charge(env, quota_client, monkeypatch, chunk, streaming):
    from app.core.config import get_settings
    from app.services import llm_client, rag_client

    client, user_id = quota_client
    _, redis, _ = env
    monkeypatch.setenv("RELEVANCE_CHECK", "true")
    monkeypatch.setenv("LLM_BYPASS", "false")
    monkeypatch.setenv("QUERY_REWRITE_ENABLED", "false")
    get_settings.cache_clear()
    calls = []

    async def search(*args, **kwargs):
        return [chunk]  # land law is unrelated to the commercial dispute

    async def generate(system, history, question, **options):
        calls.append(options)
        return '{"relevant": []}', 6575, "deepseek-test"

    monkeypatch.setattr(rag_client, "search", search)
    monkeypatch.setattr(llm_client, "generate", generate)
    key = quota._key(user_id)
    redis.values[key] = 123
    base = "/api/v1/agent/conversations"
    cid = client.post(base).json()["data"]["id"]
    path = f"{base}/{cid}/messages" + ("/stream" if streaming else "")
    response = client.post(path, json={"content": "An Phát và Minh Long tranh chấp tiền hàng 30 tỷ, phạt vi phạm tính thế nào?"})
    assert response.status_code == 200
    saved = client.get(f"{base}/{cid}").json()["data"]["messages"][-1]
    assert saved["content"] == prompts.OUT_OF_SCOPE_ANSWER
    assert saved["metadata"]["tokens_used"] == 6575
    assert saved["metadata"]["quota_exempt"] is True
    assert redis.values[key] == 123
    assert len(calls) == 1 and calls[0]["reasoning_max_tokens"] == 0
