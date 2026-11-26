"""Cache lịch sử trò chuyện (app/services/history_cache.py) nối vào conversations.py: đọc qua cache, ghi xuyên, xoá, cô lập người dùng,
Redis lỗi. SQLite trong RAM thay Supabase (có đếm số lần đọc tin nhắn), Redis GIẢ viết ngay trong file, agent giả; không mạng, không sleep."""
import json
import uuid
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.v1 import conversations
from app.core.auth import TokenPayload
from app.core.config import get_settings
from app.core.database import get_db
from app.main import app
from app.repositories import conversation as repo
import httpx

from app.services import agent, cache, history_cache, llm_client, rag_client

USER_A = "11111111-1111-4111-8111-111111111111"
USER_B = "22222222-2222-4222-8222-222222222222"
BASE = "/api/v1/agent/conversations"
RESULT = {"content": "Trả lời mẫu.", "metadata": {"rag_chunk_ids": [], "model": "m", "tokens_used": 1}}
REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_GET_MESSAGES = repo.get_messages  # bản chưa bị đếm: dùng để lấy "sự thật" từ Supabase giả lập mà không tính vào số lần đọc


class FakeRedis:
    """get/set(ex=)/delete/scan_iter. `fail` = lỗi ném ở mọi lệnh; `fail_ops` = chỉ ném ở các lệnh có tên trong tập này."""

    def __init__(self):
        self.data = {}
        self.calls = 0
        self.fail = None
        self.fail_ops = None
        self.ops = []

    def _enter(self, op):
        self.calls += 1
        self.ops.append(op)
        if self.fail is not None and (self.fail_ops is None or op in self.fail_ops):
            raise self.fail

    def get(self, key):
        self._enter("get")
        entry = self.data.get(key)
        return None if entry is None else entry[1]

    def set(self, key, value, ex=None):
        self._enter("set")
        self.data[key] = (ex, value)

    def delete(self, *keys):
        self._enter("delete")
        for k in keys:
            self.data.pop(k, None)

    def scan_iter(self, match=None, count=None):
        self._enter("scan")
        prefix = (match or "*").rstrip("*")
        return iter([k for k in list(self.data) if k.startswith(prefix)])


@pytest.fixture
def fake(monkeypatch):
    f = FakeRedis()
    monkeypatch.setattr(cache, "_get_client", lambda: f)
    cache._warned_backends.clear()
    yield f
    cache._warned_backends.clear()


@pytest.fixture
def current_user():
    return {"sub": USER_A}


@pytest.fixture
def client(db, current_user):
    def fake_user():
        return TokenPayload(sub=current_user["sub"], role="citizen", type="access",
                            iss="legal-assistant-user-service", iat=0, exp=9999999999)

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = fake_user
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def seen_history(monkeypatch):
    """Agent giả: ghi lại history mà agent nhận được mỗi lượt."""
    seen = []

    async def fake_process_message(user_content, history, request_id=None, documents=None):
        seen.append(list(history))
        return RESULT

    monkeypatch.setattr(agent, "process_message", fake_process_message)
    return seen


@pytest.fixture
def db_reads(monkeypatch):
    """Đếm số lần đọc tin nhắn lịch sử (repo.get_messages có limit) từ 'Supabase'."""
    count = {"n": 0}
    real = repo.get_messages

    def counting(db, conversation_id, limit=None):
        if limit is not None:
            count["n"] += 1
        return real(db, conversation_id, limit)

    monkeypatch.setattr(repo, "get_messages", counting)
    return count


def configure(monkeypatch, *, enabled=True, backend="redis", url="redis://redis-test:6379/0", **env):
    monkeypatch.setenv("HISTORY_CACHE_ENABLED", "true" if enabled else "false")
    monkeypatch.setenv("CACHE_BACKEND", backend)
    monkeypatch.setenv("REDIS_URL", url)
    for name, value in env.items():
        monkeypatch.setenv(name, str(value))
    get_settings.cache_clear()
    history_cache.reset()


def create(client):
    return client.post(BASE).json()["data"]["id"]


def send(client, conversation_id, content="Hỏi"):
    return client.post(f"{BASE}/{conversation_id}/messages", json={"content": content})


def limit_now():
    """Số tin lịch sử đang cấu hình (HISTORY_MAX_MESSAGES, mặc định 12): nguồn chung của nơi đọc Supabase và nơi cắt Redis."""
    return get_settings().history_max_messages


def truth(db, conversation_id, limit=None):
    """Lịch sử lấy THẲNG từ Supabase giả lập, đúng hình dạng _load_for_chat."""
    limit = limit or limit_now()
    return [{"role": m.role, "content": m.content} for m in REAL_GET_MESSAGES(db, uuid.UUID(str(conversation_id)), limit=limit)]


def cached(conversation_id, user=USER_A):
    return history_cache.get(user, conversation_id)


# ── đọc: trượt rồi trúng ─────────────────────────────────────────────────────

def test_doc_dau_truot_doc_db_roi_luu_doc_ke_tiep_trung_khong_doc_db(monkeypatch, fake, client, db, seen_history, db_reads):
    configure(monkeypatch)
    cid = create(client)
    send(client, cid, "Hỏi 1")  # hội thoại rỗng: đọc DB (1), không lưu rỗng; ghi xuyên không dựng cache
    assert db_reads["n"] == 1 and cached(cid) is None
    send(client, cid, "Hỏi 2")  # cache chưa có: đọc DB (2), lưu; ghi xuyên nối cặp mới
    assert db_reads["n"] == 2
    send(client, cid, "Hỏi 3")  # trúng cache: không đọc DB
    expected_4 = truth(db, cid)  # Supabase giả lập có gì ngay trước lượt 4
    send(client, cid, "Hỏi 4")
    assert db_reads["n"] == 2
    assert seen_history[0] == []
    assert seen_history[2] == [  # lượt 3 nhận 4 tin, giống hệt Supabase trước lượt 3
        {"role": "user", "content": "Hỏi 1"}, {"role": "assistant", "content": RESULT["content"]},
        {"role": "user", "content": "Hỏi 2"}, {"role": "assistant", "content": RESULT["content"]},
    ]
    assert seen_history[3] == expected_4  # lượt 4 nhận đúng những gì Supabase có (HISTORY_MAX_MESSAGES tin gần nhất)
    assert cached(cid) == truth(db, cid)  # cache cuối cùng = Supabase


def test_hoi_thoai_rong_khong_bi_luu(monkeypatch, fake, client, seen_history):
    configure(monkeypatch)
    cid = create(client)
    history_cache.put(USER_A, cid, [])  # gọi trực tiếp với danh sách rỗng
    assert fake.data == {}
    send(client, cid, "Hỏi đầu")  # lượt đầu: _load_for_chat đọc ra rỗng và không được lưu rỗng
    assert seen_history[0] == [] and fake.data == {}


def test_khoa_gom_ma_nguoi_dung_va_ma_hoi_thoai_vung_history(monkeypatch, fake):
    configure(monkeypatch)
    history_cache.put(USER_A, "c1", [{"role": "user", "content": "x"}])
    (key,) = fake.data
    assert key == cache.make_key("history", (USER_A, "c1"))
    assert key.startswith("legal:history:")
    assert USER_A not in key  # khoá băm, không lộ mã người dùng
    assert fake.data[key][0] == 3600  # TTL mặc định (cấu hình HISTORY_CACHE_TTL_SECONDS)


# ── ghi xuyên ────────────────────────────────────────────────────────────────

def seed_pairs(db, cid, n):
    from datetime import datetime, timedelta, timezone

    conversation = repo.get_conversation(db, uuid.UUID(cid))
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(n):
        repo.add_message_pair(db, conversation, f"Hỏi cũ {i}", f"Đáp cũ {i}", None, start + timedelta(seconds=i * 10))


def test_ghi_xuyen_khi_lich_su_da_du_tin_toi_da_cat_con_tin_cuoi(monkeypatch, fake, client, db, seen_history, db_reads):
    configure(monkeypatch)
    cid = create(client)
    n = limit_now()
    seed_pairs(db, cid, n // 2 + 1)  # nhiều hơn giới hạn 2 tin trong DB
    send(client, cid, "Hỏi mới")  # đọc DB (giới hạn tin gần nhất), lưu, rồi ghi xuyên
    assert len(seen_history[0]) == n
    cur = cached(cid)
    assert len(cur) == n and cur == truth(db, cid)
    assert cur[-2:] == [{"role": "user", "content": "Hỏi mới"}, {"role": "assistant", "content": RESULT["content"]}]
    reads = db_reads["n"]
    send(client, cid, "Hỏi nữa")
    assert db_reads["n"] == reads  # trúng cache
    assert cached(cid) == truth(db, cid) and len(cached(cid)) == n


def test_15_tin_thi_chi_12_tin_cuoi_vao_lich_su_gui_llm_va_vao_redis(monkeypatch, fake, client, db, seen_history):
    configure(monkeypatch)
    assert get_settings().history_max_messages == 12  # mặc định
    cid = create(client)
    seed_pairs(db, cid, 7)  # 14 tin
    from datetime import datetime, timezone

    db.add(repo.Message(
        conversation_id=uuid.UUID(cid), role="user", content="Tin lẻ thứ 15", created_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    ))
    db.commit()
    assert len(repo.get_messages(db, uuid.UUID(cid))) == 15
    all_msgs = [{"role": m.role, "content": m.content} for m in repo.get_messages(db, uuid.UUID(cid))]
    send(client, cid, "Hỏi mới")
    assert seen_history[0] == all_msgs[-12:] and len(seen_history[0]) == 12  # LLM chỉ nhận 12 tin cuối
    cur = cached(cid)  # ghi xuyên: nối cặp mới rồi cắt còn 12 tin cuối
    assert len(cur) == 12
    assert cur[-2:] == [{"role": "user", "content": "Hỏi mới"}, {"role": "assistant", "content": RESULT["content"]}]
    assert cur == truth(db, cid)
    stored = json.loads(next(iter(fake.data.values()))[1])  # đúng nội dung nằm trong Redis giả, không chỉ trong RAM
    assert stored == cur


def test_dat_history_max_messages_bang_4_thi_ca_doc_supabase_lan_cat_redis_deu_con_4(monkeypatch, fake, client, db, seen_history):
    """Chứng minh hai nơi dùng chung MỘT nguồn cấu hình: chỉ đổi biến, không đổi chỗ nào khác."""
    configure(monkeypatch, HISTORY_MAX_MESSAGES=4)
    assert get_settings().history_max_messages == 4
    cid = create(client)
    seed_pairs(db, cid, 5)  # 10 tin
    send(client, cid, "Hỏi mới")
    assert len(seen_history[0]) == 4  # nơi đọc Supabase (_load_for_chat) cắt còn 4
    assert len(cached(cid)) == 4  # nơi cắt trong Redis (history_cache.append) cũng 4
    send(client, cid, "Hỏi nữa")  # trúng cache, nối 2 tin rồi cắt còn 4
    assert len(seen_history[1]) == 4 and len(cached(cid)) == 4
    assert cached(cid) == truth(db, cid)
    assert cached(cid)[-2:] == [{"role": "user", "content": "Hỏi nữa"}, {"role": "assistant", "content": RESULT["content"]}]


def test_history_max_messages_phai_la_so_nguyen_duong(monkeypatch):
    monkeypatch.setenv("HISTORY_MAX_MESSAGES", "0")
    get_settings.cache_clear()
    with pytest.raises(Exception):
        get_settings()
    monkeypatch.delenv("HISTORY_MAX_MESSAGES")
    get_settings.cache_clear()


def test_ghi_xuyen_khi_co_it_hon_gioi_han_thi_noi_them(monkeypatch, fake, client, db, seen_history):
    configure(monkeypatch)
    cid = create(client)
    seed_pairs(db, cid, 1)
    send(client, cid, "Hỏi A")  # cache đọc 2 tin rồi nối 2 tin = 4
    assert len(cached(cid)) == 4
    send(client, cid, "Hỏi B")  # 6 (vẫn dưới giới hạn mặc định)
    assert len(cached(cid)) == 6 and cached(cid) == truth(db, cid)


def test_cache_chua_co_ma_ghi_thi_khong_dung_cache_gia(monkeypatch, fake, client, db, seen_history, db_reads):
    configure(monkeypatch)
    cid = create(client)
    seed_pairs(db, cid, 2)  # tin đã có trong DB nhưng chưa nằm trong cache
    assert cached(cid) is None
    # ghi một cặp qua đúng đường của endpoint, nhưng cache đang trống: không được tự dựng
    history_cache.append(USER_A, uuid.UUID(cid), [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}], limit_now())
    assert fake.data == {} and cached(cid) is None
    send(client, cid, "Hỏi")  # lần đọc kế đọc Supabase
    assert db_reads["n"] == 1 and len(seen_history[0]) == 4


def test_supabase_ghi_loi_thi_cache_khong_doi(monkeypatch, fake, client, db, seen_history):
    configure(monkeypatch)
    cid = create(client)
    send(client, cid, "Hỏi 1")
    send(client, cid, "Hỏi 2")
    before = cached(cid)
    assert before is not None

    def broken(*args, **kwargs):
        raise RuntimeError("DB down")

    monkeypatch.setattr(repo, "add_message_pair", broken)
    with pytest.raises(RuntimeError):
        send(client, cid, "Hỏi 3")
    assert cached(cid) == before


# ── xoá ──────────────────────────────────────────────────────────────────────

def test_xoa_hoi_thoai_xoa_khoa_cache_khoa_khac_con_nguyen(monkeypatch, fake, client, db, seen_history):
    configure(monkeypatch)
    c1, c2 = create(client), create(client)
    for cid in (c1, c2):
        send(client, cid, "a")
        send(client, cid, "b")
    assert cached(c1) and cached(c2)
    assert client.delete(f"{BASE}/{c1}").status_code == 200
    assert cached(c1) is None and cached(c2) is not None


def test_xoa_that_bai_thi_khong_xoa_khoa(monkeypatch, fake, client, db, seen_history):
    configure(monkeypatch)
    cid = create(client)
    send(client, cid, "a")
    send(client, cid, "b")

    def broken(db_, conversation):
        raise RuntimeError("DB down")

    monkeypatch.setattr(repo, "delete_conversation", broken)
    with pytest.raises(RuntimeError):
        client.delete(f"{BASE}/{cid}")
    assert cached(cid) is not None


# ── cô lập người dùng và quyền sở hữu ────────────────────────────────────────

def test_nguoi_khong_so_huu_van_bi_tu_choi_du_hoi_thoai_dang_nam_trong_cache(monkeypatch, fake, client, db, current_user, seen_history):
    configure(monkeypatch)
    cid = create(client)
    send(client, cid, "a")
    send(client, cid, "b")
    assert cached(cid) is not None
    current_user["sub"] = USER_B
    r = send(client, cid, "tôi muốn đọc lịch sử người khác")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CONVERSATION_FORBIDDEN"
    assert len(seen_history) == 2  # agent không được gọi cho lượt bị từ chối


def test_khong_doc_nham_giua_hai_nguoi_dung_cung_ma_hoi_thoai(monkeypatch, fake):
    configure(monkeypatch)
    history_cache.put(USER_A, "chung", [{"role": "user", "content": "bí mật của A"}])
    assert history_cache.get(USER_B, "chung") is None
    history_cache.put(USER_B, "chung", [{"role": "user", "content": "của B"}])
    assert history_cache.get(USER_A, "chung") == [{"role": "user", "content": "bí mật của A"}]
    assert history_cache.get(USER_B, "chung") == [{"role": "user", "content": "của B"}]


# ── Redis lỗi ────────────────────────────────────────────────────────────────

def test_redis_loi_khi_doc_chat_van_chay_va_doc_supabase(monkeypatch, fake, client, db, seen_history, db_reads):
    configure(monkeypatch)
    cid = create(client)
    seed_pairs(db, cid, 1)
    fake.fail = ConnectionError("hong")
    r = send(client, cid, "Hỏi")
    assert r.status_code == 200 and len(seen_history[0]) == 2
    assert db_reads["n"] == 1


def test_json_hong_trong_cache_thi_doc_supabase_va_khong_nem_loi(monkeypatch, fake, client, db, seen_history, db_reads):
    configure(monkeypatch)
    cid = create(client)
    seed_pairs(db, cid, 1)
    fake.data[cache.make_key("history", (USER_A, cid))] = (3600, "{khong phai json")
    r = send(client, cid, "Hỏi")
    assert r.status_code == 200 and db_reads["n"] == 1 and len(seen_history[0]) == 2


def test_hinh_dang_sai_trong_cache_bi_bo(monkeypatch, fake, client, db, seen_history, db_reads):
    configure(monkeypatch)
    cid = create(client)
    seed_pairs(db, cid, 1)
    fake.data[cache.make_key("history", (USER_A, cid))] = (3600, json.dumps([{"role": "user"}]))  # thiếu content
    send(client, cid, "Hỏi")
    assert db_reads["n"] == 1 and len(seen_history[0]) == 2


def test_redis_loi_luc_ghi_roi_song_lai_thi_lich_su_khong_bi_cu(monkeypatch, fake, client, db, seen_history, db_reads):
    """Cache đang có lịch sử cũ; Redis chết đúng lúc ghi cặp mới; sau khi Redis sống lại, lần đọc kế phải thấy cặp mới (từ Supabase)."""
    configure(monkeypatch, REDIS_BREAKER_SECONDS=0)  # không giữ ngắt mạch sau khi Redis sống lại, để thấy cache được dựng lại
    cid = create(client)
    send(client, cid, "Hỏi 1")
    send(client, cid, "Hỏi 2")
    old = cached(cid)
    assert len(old) == 4
    fake.fail = ConnectionError("hong")
    assert send(client, cid, "Hỏi 3 khi Redis chết").status_code == 200
    fake.fail = None  # Redis sống lại, vẫn giữ lịch sử cũ (4 tin) trong fake.data
    key = cache.make_key("history", (USER_A, cid))
    assert json.loads(fake.data[key][1]) == old  # đúng là bản cũ còn nằm trong Redis
    reads = db_reads["n"]
    expected = truth(db, cid)  # Supabase giả lập đã có cặp của lượt 3
    assert any("Hỏi 3 khi Redis chết" == m["content"] for m in expected)
    assert send(client, cid, "Hỏi 4").status_code == 200
    assert seen_history[-1] == expected  # lượt 4 nhận lịch sử MỚI (có cặp lượt 3), không phải bản cũ trong Redis
    assert db_reads["n"] == reads + 1  # phải đọc Supabase, không dùng bản cũ trong Redis
    assert cached(cid) == truth(db, cid)  # đã dọn bản cũ, lưu bản mới


def test_ngat_mach_van_mo_sau_khi_redis_song_lai_thi_van_khong_doc_ban_cu_roi_don_khi_het_ngat(monkeypatch, fake, client, db, seen_history):
    """Redis sống lại nhưng ngắt mạch còn mở nên chưa xoá được bản cũ trong Redis: khoá bị đánh dấu, chat vẫn đúng (đọc Supabase);
    khi hết ngắt mạch thì bản cũ được xoá và cache được dựng lại."""
    now = {"t": 1000.0}
    monkeypatch.setattr(cache.time, "monotonic", lambda: now["t"])
    configure(monkeypatch)
    cid = create(client)
    send(client, cid, "Hỏi 1")
    send(client, cid, "Hỏi 2")
    old = cached(cid)
    key = cache.make_key("history", (USER_A, cid))
    fake.fail = ConnectionError("hong")
    send(client, cid, "Hỏi 3")
    send(client, cid, "Hỏi 4")  # đủ lỗi liên tiếp để ngắt mạch mở
    fake.fail = None  # Redis sống lại, ngắt mạch vẫn mở
    assert json.loads(fake.data[key][1]) == old  # bản cũ vẫn nằm trong Redis
    assert (USER_A, cid) in history_cache._dirty
    expected = truth(db, cid)
    send(client, cid, "Hỏi 5")
    assert seen_history[-1] == expected  # vẫn đúng: đọc Supabase, không dùng bản cũ
    assert json.loads(fake.data[key][1]) == old  # chưa xoá được vì còn ngắt mạch
    now["t"] += 60  # hết ngắt mạch
    expected = truth(db, cid)
    send(client, cid, "Hỏi 6")
    assert seen_history[-1] == expected
    assert (USER_A, cid) not in history_cache._dirty
    assert cached(cid) == truth(db, cid)  # bản cũ đã được thay bằng bản đúng


def test_redis_loi_luc_ghi_nhung_van_thanh_cong_cap_nhat_khoa_bi_xoa_khong_dirty(monkeypatch, fake, client, db, seen_history):
    """Chỉ lệnh set lỗi (get/delete bình thường): khoá được xoá ngay, không cần đánh dấu."""
    configure(monkeypatch)
    cid = create(client)
    send(client, cid, "Hỏi 1")
    send(client, cid, "Hỏi 2")
    fake.fail, fake.fail_ops = RuntimeError("lệnh set lỗi"), {"set"}  # lỗi kiểu khác kết nối: không mở ngắt mạch ngay
    send(client, cid, "Hỏi 3")
    fake.fail = None
    assert cached(cid) is None  # khoá bị xoá
    assert history_cache._dirty == set()


def test_log_khong_chua_noi_dung_tin_nhan_hay_url(monkeypatch, fake, client, db, seen_history, caplog):
    import logging

    secret_msg = "NOI-DUNG-RIENG-TU-123"
    url = "redis://" + "default:" + "KHONG-LO-RA-LOG" + "@" + "redis-test:6379/0"
    configure(monkeypatch, url=url)
    cid = create(client)
    send(client, cid, secret_msg)
    send(client, cid, secret_msg)
    fake.fail = ConnectionError("loi " + url + " " + secret_msg)
    with caplog.at_level(logging.DEBUG):
        send(client, cid, secret_msg)
        send(client, cid, secret_msg)
    log_text = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("app.services"))
    assert secret_msg not in log_text and "KHONG-LO-RA-LOG" not in log_text and "redis-test" not in log_text


# ── cờ tắt, backend memory, mặc định ─────────────────────────────────────────

def test_co_tat_hanh_vi_y_het_cu_khong_cham_cache(monkeypatch, fake, client, db, seen_history, db_reads):
    configure(monkeypatch, enabled=False)
    cid = create(client)
    for i in range(3):
        send(client, cid, f"Hỏi {i}")
    assert fake.calls == 0 and fake.data == {}
    assert db_reads["n"] == 3  # đọc DB mỗi lượt như trước B6
    assert seen_history[2] == truth(db, cid)[:-2]


def test_backend_memory_dung_cache_ram_khong_cham_redis(monkeypatch, fake, client, db, seen_history, db_reads):
    configure(monkeypatch, backend="memory")
    cid = create(client)
    send(client, cid, "Hỏi 1")
    send(client, cid, "Hỏi 2")
    send(client, cid, "Hỏi 3")
    assert db_reads["n"] == 2 and fake.calls == 0
    assert type(history_cache._get_cache()) is cache.TTLCache
    assert cached(cid) == truth(db, cid)


def test_gia_tri_mac_dinh_cua_cau_hinh(monkeypatch):
    for name in ["HISTORY_CACHE_ENABLED", "HISTORY_CACHE_TTL_SECONDS", "HISTORY_CACHE_MAX_ITEMS"]:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    s = get_settings()
    assert (s.history_cache_enabled, s.history_cache_ttl_seconds, s.history_cache_max_items) == (True, 3600, 500)


def test_docker_compose_va_env_example_co_cac_bien_cache_mac_dinh_dung():
    """Chống tái diễn lỗi B4 (compose và .env.example còn ghi false): cả hai cờ cache còn lại (tìm luật, lịch sử) phải mặc định true ở compose và
    hai .env.example, các biến lịch sử có đúng mặc định, và KHÔNG còn biến ANSWER_CACHE_* nào (cache câu trả lời đã bị gỡ). Cờ chỉ-để-test
    LLM_BYPASS thì không được bật mặc định ở đâu."""
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    env = compose["services"]["agent-service"]["environment"]
    for flag in ("RAG_CACHE_ENABLED", "HISTORY_CACHE_ENABLED"):
        assert env[flag] == "${" + flag + ":-true}", flag
    assert not [k for k in env if k.startswith("ANSWER_CACHE")]
    assert env["HISTORY_CACHE_TTL_SECONDS"] == "${HISTORY_CACHE_TTL_SECONDS:-3600}"
    assert env["HISTORY_CACHE_MAX_ITEMS"] == "${HISTORY_CACHE_MAX_ITEMS:-500}"
    assert env["HISTORY_MAX_MESSAGES"] == "${HISTORY_MAX_MESSAGES:-12}"
    assert env["LLM_BYPASS"] == "${LLM_BYPASS:-false}"
    for path in (REPO_ROOT / ".env.example", REPO_ROOT / "services" / "agent-service" / ".env.example"):
        lines = path.read_text(encoding="utf-8").splitlines()
        for flag in ("RAG_CACHE_ENABLED", "HISTORY_CACHE_ENABLED"):
            assert f"{flag}=true" in lines, (path.name, flag)
            assert f"{flag}=false" not in lines, (path.name, flag)
        assert not [l for l in lines if l.startswith("ANSWER_CACHE")], path.name
        assert "HISTORY_CACHE_TTL_SECONDS=3600" in lines
        assert "HISTORY_CACHE_MAX_ITEMS=500" in lines
        assert "HISTORY_MAX_MESSAGES=12" in lines
        assert "LLM_BYPASS=true" not in lines


# ── cache câu trả lời đã bị gỡ: tin đầu của mọi hội thoại đều đi qua rag và AI ─────────────────────────────

def test_hai_hoi_thoai_cung_cau_hoi_dau_goi_llm_hai_lan_nhung_cache_tim_luat_van_dung_lai(monkeypatch, fake, client, db, chunk):
    """Không còn cache câu trả lời dùng chung: hai hội thoại khác nhau gửi cùng một câu đầu tiên thì LLM được gọi 2 lần (không có
    cache_hit), nhưng lần 2 cache TÌM LUẬT (vùng rag) vẫn được dùng lại nên rag-service chỉ bị gọi 1 lần và Redis chỉ có khoá rag."""
    configure(monkeypatch)
    monkeypatch.setenv("RAG_CACHE_ENABLED", "true")
    get_settings.cache_clear()
    rag_client.clear_cache()
    counts = {"rag": 0, "llm": 0}

    def rag_handler(request: httpx.Request):
        counts["rag"] += 1
        return httpx.Response(200, json={"data": {"chunks": [chunk]}, "error": None, "request_id": "r"})

    original_post = rag_client._post

    async def post_with_mock(path, payload, request_id, transport):
        return await original_post(path, payload, request_id, httpx.MockTransport(rag_handler))

    async def fake_generate(system_prompt, history, user_content, **options):
        counts["llm"] += 1
        return "Theo Khoản 1 Điều 188 Luật Đất đai 2024...", 512, "gemini-test"

    monkeypatch.setattr(rag_client, "_post", post_with_mock)
    monkeypatch.setattr(llm_client, "generate", fake_generate)
    question = "Sang tên sổ đỏ cần gì?"
    try:
        answers = []
        for _ in range(2):
            cid = create(client)  # hội thoại mới mỗi lần: tin đầu, lịch sử rỗng
            r = send(client, cid, question)
            assert r.status_code == 200
            answers.append(r.json()["data"]["assistant_message"])
        assert counts["llm"] == 2  # không còn cache câu trả lời: lần 2 vẫn gọi AI
        assert counts["rag"] == 1  # nhưng cache tìm luật được dùng lại
        for a in answers:
            assert "cache_hit" not in a["metadata"] and a["metadata"]["tokens_used"] == 512
        kinds = sorted({k.split(":")[1] for k in fake.data if k.startswith("legal:")})
        assert kinds == ["rag"]  # không có legal:answer; chưa có legal:history vì mỗi hội thoại mới chỉ có tin đầu (lịch sử rỗng không được lưu)
    finally:
        rag_client.clear_cache()
