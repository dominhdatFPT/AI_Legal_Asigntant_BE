"""Kiểm thử hồi quy cho gói gia cố bảo mật, trần chi phí phụ trợ và tối ưu hiệu suất pipeline:
1. Chống giả mạo CF-Connecting-IP / chuẩn hoá IPv4-IPv6 trên /api/v1/agent/chat.
2. Gom lệnh Redis MGET + pipeline (get_many / set_many) cho cache và kiểm soát trần $0.01 ở form-check.
3. Giải phóng kết nối DB sớm trước khi chờ LLM ở internal summary + áp CaseBudget cho summary / minutes.
4. Nhịp tim SSE `event: status` giữ sống kết nối cho câu hỏi phức tạp (complex) mà không đổi output hay thêm frame ở luồng nhanh.
5. Đồng bộ biến môi trường bảo mật/hiệu suất giữa config.py, docker-compose.yml và render.yaml.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace as NS
import uuid

from fastapi.testclient import TestClient
import pytest
import yaml

import app.main as main_mod
from app.api.deps import get_current_user
from app.api.v1 import conversations, internal
from app.core.auth import TokenPayload
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.main import app
from app.models import Message
from app.repositories import conversation as repo
from app.services import cache, form_check, llm_client, minutes, quota, rag_client, responder, summary

BE_ROOT = Path(__file__).resolve().parents[3]
USER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
INTERNAL_HEADERS = {"X-Internal-Token": "test-internal-token"}


def _set_env(monkeypatch, **items):
    for key, val in items.items():
        monkeypatch.setenv(key, str(val))
    get_settings.cache_clear()


# ── 1. Chống giả mạo IP trên chatbox công khai ───────────────────────────────────────────────────────────────────────

def test_public_chat_ignores_spoofed_cloudflare_header_by_default_and_normalizes_ipv6(monkeypatch):
    _set_env(
        monkeypatch,
        DAILY_TOKEN_QUOTA_ENABLED="true",
        PUBLIC_CHAT_DAILY_TOKEN_LIMIT="50",
        TRUST_CLOUDFLARE_HEADERS="false",
    )

    async def fake_ai(*args, **kwargs):
        return {"content": "Trả lời ngắn.", "metadata": {"tokens_used": 60, "rag_chunk_ids": []}}

    async def fake_proc(*args, **kwargs):
        return None

    monkeypatch.setattr(main_mod.agent, "process_message", fake_ai)
    monkeypatch.setattr(main_mod, "find_procedure", fake_proc)

    client = TestClient(app)
    # Kẻ tấn công xoay CF-Connecting-IP liên tục nhưng cùng một X-Real-IP của Gateway -> vẫn bị khoá quota ngày
    r1 = client.post(
        "/api/v1/agent/chat",
        json={"message": "Câu hỏi 1"},
        headers={"CF-Connecting-IP": "198.51.100.1", "X-Real-IP": "2001:0db8:0000:0000:0000:0000:0000:0001"},
    )
    assert r1.status_code == 200
    r2 = client.post(
        "/api/v1/agent/chat",
        json={"message": "Câu hỏi 2"},
        headers={"CF-Connecting-IP": "198.51.100.99", "X-Real-IP": "2001:db8::1"},
    )
    assert r2.status_code == 429
    assert r2.json()["error"]["code"] == "DAILY_QUOTA_EXCEEDED"


def test_public_chat_honors_cloudflare_header_only_when_explicitly_trusted_and_rejects_malformed_ips(monkeypatch):
    _set_env(monkeypatch, TRUST_CLOUDFLARE_HEADERS="true")

    def make_req(headers: dict[str, str], client_host: str = "10.0.0.5"):
        raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()]
        scope = {"type": "http", "headers": raw_headers, "client": (client_host, 12345)}
        from starlette.requests import Request
        return Request(scope)

    # Hợp lệ khi bật cờ tin cậy Cloudflare
    assert main_mod._public_chat_subject(make_req({"CF-Connecting-IP": "203.0.113.7"})) == "public-ip:203.0.113.7"
    # Chuỗi bẩn / tiêm kí tự trong CF-Connecting-IP và X-Real-IP bị loại bỏ, rơi về hop hợp lệ bên phải của X-Forwarded-For
    dirty_req = make_req({
        "CF-Connecting-IP": "not-an-ip:inject",
        "X-Real-IP": "999.999.999.999",
        "X-Forwarded-For": "spoofed-by-client, 198.51.100.42",
    })
    assert main_mod._public_chat_subject(dirty_req) == "public-ip:198.51.100.42"
    # Mọi header đều hỏng và client host không phải IP -> "public-ip:unknown"
    assert main_mod._public_chat_subject(make_req({"X-Real-IP": "bad"}, client_host="testclient")) == "public-ip:unknown"


# ── 2. Gom lệnh Redis MGET + pipeline (get_many / set_many) & trần $0.01 cho form-check ──────────────────────────────

class CountingRedis:
    def __init__(self, fail=False):
        self.store: dict[str, str] = {}
        self.fail = fail
        self.get_calls = 0
        self.mget_calls = 0
        self.set_calls = 0
        self.pipeline_execs = 0

    def get(self, k):
        self.get_calls += 1
        if self.fail:
            raise OSError("redis down")
        return self.store.get(k)

    def mget(self, keys):
        self.mget_calls += 1
        if self.fail:
            raise OSError("redis down")
        return [self.store.get(k) for k in keys]

    def set(self, k, v, ex=None):
        self.set_calls += 1
        if self.fail:
            raise OSError("redis down")
        self.store[k] = v

    def pipeline(self, transaction=False):
        parent = self

        class Pipe:
            def __init__(self):
                self.ops = []

            def set(self, k, v, ex=None):
                self.ops.append((k, v))
                return self

            def execute(self):
                parent.pipeline_execs += 1
                if parent.fail:
                    raise OSError("redis down")
                for k, v in self.ops:
                    parent.store[k] = v
                return [True] * len(self.ops)

        return Pipe()


def test_resilient_cache_batches_mget_and_pipeline_and_falls_back_cleanly():
    fake_redis = CountingRedis()
    rc = cache.ResilientCache(
        cache.RedisCache("formcheck", ttl_seconds=60, client=fake_redis),
        cache.TTLCache(max_items=50, ttl_seconds=60),
    )
    items = {(f"field_{i}", "v"): {"status": "ok", "idx": i} for i in range(10)}
    assert rc.set_many(items) is True
    assert fake_redis.pipeline_execs == 1 and fake_redis.set_calls == 0

    loaded = rc.get_many(list(items) + [("missing", "v")])
    assert loaded == items
    assert fake_redis.mget_calls == 1 and fake_redis.get_calls == 0

    # Khi Redis sập: tự ngắt mạch và rơi về TTLCache không làm hỏng request
    fake_redis.fail = True
    assert rc.set_many({("fallback_k",): {"status": "warning"}}) is False
    assert rc.get_many([("fallback_k",)]) == {("fallback_k",): {"status": "warning"}}


def test_rag_batch_search_uses_single_mget_and_single_pipeline_with_redis_cache(chunk, monkeypatch):
    fake_redis = CountingRedis()
    _set_env(monkeypatch, RAG_CACHE_ENABLED="true", CACHE_BACKEND="redis", REDIS_URL="redis://fake:6379/0")
    monkeypatch.setattr(cache, "_get_client", lambda: fake_redis)
    rag_client.clear_cache()

    def handler(request):
        body = json.loads(request.read())
        return rag_client.httpx.Response(200, json={
            "data": {
                "items": [
                    {"item_id": item["item_id"], "status": "ok", "chunks": [{**chunk, "id": f"c-{item['item_id']}"}]}
                    for item in body["items"]
                ],
            },
            "error": None,
        })

    queries = [("I1", "điều kiện tách thửa đất"), ("I2", "lệ phí trước bạ nhà đất"), ("I3", "thời hạn sang tên sổ đỏ")]
    transport = rag_client.httpx.MockTransport(handler)
    first = asyncio.run(rag_client.search_many(queries, top_k=5, transport=transport))
    assert len(first) == 3 and not first.degraded
    assert fake_redis.mget_calls == 1 and fake_redis.pipeline_execs == 1

    # Lượt 2 trúng cache toàn bộ bằng đúng 1 lệnh MGET, không gọi HTTP batch lần nữa
    second = asyncio.run(rag_client.search_many(
        queries,
        top_k=5,
        transport=rag_client.httpx.MockTransport(lambda req: pytest.fail("không được gọi rag-service khi đã trúng cache")),
    ))
    assert [g[0]["id"] for g in second] == ["c-1", "c-2", "c-3"]
    assert fake_redis.mget_calls == 2


def test_form_check_enforces_cost_budget_before_charging_daily_limit(monkeypatch):
    _set_env(
        monkeypatch,
        LLM_PROVIDER="openrouter",
        OPENROUTER_MODEL="deepseek/deepseek-v4.1-flash",
        OPENROUTER_AUX_MODEL="deepseek/deepseek-v4.1-flash",
        OPENROUTER_API_KEY="test-key",
        CASE_COST_BUDGET_MICRO_USD="500",  # 500 micro-USD ($0.0005) < riêng phần giữ chỗ output 1200 token ($0.00144)
        FORM_CHECK_DAILY_LIMIT="5",
    )
    form_check.reset()
    charged = []
    monkeypatch.setattr(quota, "_execute", lambda op: charged.append(1) or 1)
    app.dependency_overrides[get_current_user] = lambda: TokenPayload(
        sub=str(USER_ID), role="citizen", type="access", iss="legal-assistant-user-service", iat=0, exp=9999999999,
    )
    try:
        r = TestClient(app).post(
            "/api/v1/agent/form-check",
            json={"procedure_code": "DD-01", "form_name": "Đơn", "fields": [{"key": "reason", "label": "Lý do", "type": "text", "value": "xin cấp sổ"}]},
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "QUESTION_EXCEEDS_BUDGET"
        assert charged == []  # Chưa gọi LLM thì không được trừ lượt kiểm tra trong ngày
    finally:
        app.dependency_overrides.clear()
        form_check.reset()


# ── 3. Giải phóng kết nối DB sớm & áp CaseBudget ở các endpoint nội bộ ───────────────────────────────────────────────

def test_internal_summary_closes_db_session_before_awaiting_llm_and_enforces_budget(db, monkeypatch):
    conv = repo.create_conversation(db, USER_ID)
    t0 = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)
    db.add(Message(conversation_id=conv.id, role="user", content="Tôi muốn hỏi về đặt cọc mua đất.", created_at=t0))
    db.commit()

    events: list[str] = []
    orig_close = db.close

    def tracked_close():
        events.append("db_closed")
        orig_close()

    monkeypatch.setattr(db, "close", tracked_close)

    async def fake_summarize(transcript: str):
        # Ngay thời điểm vào hàm chờ LLM, kết nối DB PHẢI đã được đóng trả về pool!
        assert "db_closed" in events
        events.append("llm_called")
        return "Tóm tắt ngắn.", "deepseek/deepseek-v4.1-flash"

    monkeypatch.setattr(internal.summary, "summarize", fake_summarize)
    app.dependency_overrides[get_db] = lambda: db
    try:
        r = TestClient(app).post(
            f"/internal/v1/agent/conversations/{conv.id}/summary",
            json={"user_id": str(USER_ID), "until": (t0 + timedelta(minutes=5)).isoformat()},
            headers=INTERNAL_HEADERS,
        )
        assert r.status_code == 200
        assert events.index("db_closed") < events.index("llm_called")
    finally:
        app.dependency_overrides.clear()


def test_internal_summary_and_minutes_reject_unverified_route_when_budget_enabled(db, monkeypatch):
    # Tuyến giả lập chưa có bảng giá xác minh khi bật trần tiền -> fail-closed 503 AI_SERVICE_ERROR
    _set_env(
        monkeypatch,
        LLM_PROVIDER="openrouter",
        OPENROUTER_MODEL="unverified/expensive-model",
        OPENROUTER_API_KEY="test-key",
        CASE_COST_BUDGET_MICRO_USD="10000",
        MINUTES_COST_BUDGET_MICRO_USD="30000",
    )
    conv = repo.create_conversation(db, USER_ID)
    t0 = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)
    db.add(Message(conversation_id=conv.id, role="user", content="Nội dung cần tóm tắt.", created_at=t0))
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        r_sum = client.post(
            f"/internal/v1/agent/conversations/{conv.id}/summary",
            json={"user_id": str(USER_ID), "until": (t0 + timedelta(minutes=5)).isoformat()},
            headers=INTERNAL_HEADERS,
        )
        assert r_sum.status_code == 503 and r_sum.json()["error"]["code"] == "AI_SERVICE_ERROR"

        r_min = client.post(
            "/internal/v1/agent/transcripts/summary",
            json={
                "consultation_id": str(uuid.uuid4()),
                "window_start": None,
                "window_end": "2026-11-08T09:06:00+07:00",
                "previous_summary": None,
                "messages": [{"id": "m1", "role": "CITIZEN", "time": "2026-11-08T09:00:00+07:00", "content": "Xin tư vấn"}],
            },
            headers=INTERNAL_HEADERS,
        )
        assert r_min.status_code == 503 and r_min.json()["error"]["code"] == "AI_SERVICE_ERROR"
    finally:
        app.dependency_overrides.clear()


# ── 4. Nhịp tim SSE `event: status` cho luồng complex dài ────────────────────────────────────────────────────────────

def test_complex_stream_emits_status_heartbeat_without_altering_final_answer(monkeypatch):
    _set_env(monkeypatch, SSE_HEARTBEAT_SECONDS="0.02")
    chunks = [{"id": "c1", "content": "Điều 328 BLDS.", "matched_issue_ids": ["I1"], "confirmed_issue_ids": ["I1"]}]
    answer_text = "## Kết luận ngắn và căn cứ chính\n- Hợp đồng đặt cọc có hiệu lực [S1]."

    async def slow_stream(*args, info, **kwargs):
        for piece in (answer_text[:20], answer_text[20:45], answer_text[45:]):
            await asyncio.sleep(0.025)
            yield piece
        info.update(tokens=80, model="deepseek/deepseek-v4.1-flash", truncated=False, finish_reason="stop")

    async def fake_threadpool(fn, *args, **kwargs):
        return object(), object()

    monkeypatch.setattr(llm_client, "generate_stream", slow_stream)
    monkeypatch.setattr(conversations, "run_in_threadpool", fake_threadpool)
    monkeypatch.setattr(conversations.quota, "add_daily_tokens", lambda *a, **k: None)
    monkeypatch.setattr(conversations.MessageOut, "model_validate", lambda value: {})
    monkeypatch.setattr(conversations, "SendMessageResponse", lambda **kwargs: kwargs)

    call = responder.LLMCall(
        "system", chunks, [], "q", None, 0.0, 0.0,
        metadata_extra={"profile": "complex", "issues": [{"issue_id": "I1", "question": "Hiệu lực?"}]},
    )

    async def collect():
        return [
            ev async for ev in conversations._stream_events(
                None, NS(user_id="u1"), NS(content="q", document_ids=[]),
                "req-hb", datetime.now(timezone.utc), call, None,
            )
        ]

    events = asyncio.run(collect())
    status_events = [e for e in events if e.startswith("event: status\n")]
    delta_events = [e for e in events if e.startswith("event: delta\n")]
    assert len(status_events) >= 1
    status_payload = json.loads(status_events[0].split("data: ", 1)[1])
    assert status_payload["stage"] == "generating" and "pháp lý" in status_payload["message"]
    assert len(delta_events) == 1
    assert json.loads(delta_events[0].split("data: ", 1)[1])["text"] == answer_text
    assert events[-1].startswith("event: done\n")


# ── 5. Đồng bộ cấu hình Render / Compose / Settings ─────────────────────────────────────────────────────────────────

def test_render_blueprint_declares_redis_url_and_hardened_defaults(monkeypatch):
    for key in ("TRUST_CLOUDFLARE_HEADERS", "SSE_HEARTBEAT_SECONDS", "MINUTES_COST_BUDGET_MICRO_USD"):
        monkeypatch.delenv(key, raising=False)
    render_data = yaml.safe_load((BE_ROOT / "render.yaml").read_text(encoding="utf-8"))
    by_name = {s["name"]: s["envVars"] for s in render_data["services"]}

    groups = {g["name"]: {e["key"] for e in g["envVars"]} for g in render_data["envVarGroups"]}
    assert "CORPUS_VERSION_KEY" in groups["ailegal-cache"]
    for svc in ("ailegal-agent-service", "ailegal-rag-service"):
        assert {"fromGroup": "ailegal-cache"} in by_name[svc]
        assert "REDIS_URL" not in {e["key"] for e in by_name[svc] if "key" in e}, f"{svc} che khuất ailegal-cache"

    gw_vars = {e["key"]: e.get("value") for e in by_name["ailegal-gateway"] if "key" in e}
    agent_vars = {e["key"]: e.get("value") for e in by_name["ailegal-agent-service"] if "key" in e}
    defaults = Settings(_env_file=None)
    assert gw_vars["TRUST_CLOUDFLARE_HEADERS"] == "false"
    assert agent_vars["TRUST_CLOUDFLARE_HEADERS"] == "false" and defaults.trust_cloudflare_headers is False
    assert agent_vars["SSE_HEARTBEAT_SECONDS"] == "10" and defaults.sse_heartbeat_seconds == 10.0
    assert agent_vars["MINUTES_COST_BUDGET_MICRO_USD"] == "30000" and defaults.minutes_cost_budget_micro_usd == 30000
