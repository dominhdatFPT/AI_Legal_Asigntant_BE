"""WebSocket và đánh dấu đã đọc. Dùng một TestClient trong khối `with` để REST và WebSocket chung một event loop."""

import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from starlette.websockets import WebSocketDisconnect

from app.api import ws as ws_module
from app.services import realtime

BASE = "/api/v1/consultations"
WS = "/ws/v1/consultations"


@pytest.fixture(autouse=True)
def clean_rooms():
    realtime.manager.rooms.clear()
    yield
    realtime.manager.rooms.clear()


@pytest.fixture
def live(db, upstream):
    from fastapi.testclient import TestClient

    from app.core.database import get_db
    from app.main import app

    def override():
        yield db

    app.dependency_overrides[get_db] = override
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def rest(live, make_api):
    return make_api(live)


def token_of(auth, user, role="citizen"):
    return auth(user, role)["Authorization"].removeprefix("Bearer ")


def room_of(channel, user):
    if channel == "lobby":
        return realtime.LOBBY
    return realtime.me(user) if channel == "me" else realtime.chat(uuid.UUID(channel))


@contextmanager
def connect(live, auth, user, channel, role="citizen"):
    """Mở socket, gửi tin xác thực rồi chờ server đưa socket vào phòng."""
    room = room_of(channel, user)
    before = len(realtime.manager.rooms.get(room, ()))
    with live.websocket_connect(f"{WS}/{channel}") as socket:
        socket.send_json({"type": "auth", "data": {"token": token_of(auth, user, role)}})
        for _ in range(300):
            if len(realtime.manager.rooms.get(room, ())) > before:
                break
            time.sleep(0.01)
        else:
            raise AssertionError("socket chưa vào phòng")
        yield socket


def close_code(socket):
    assert socket.receive_json()["type"] == "error"
    with pytest.raises(WebSocketDisconnect) as closed:
        socket.receive_json()
    return closed.value.code


def rejected(live, first_message, channel="me"):
    with live.websocket_connect(f"{WS}/{channel}") as socket:
        if first_message is not None:
            socket.send_json(first_message)
        return close_code(socket)


def rejected_after_auth(live, auth, user, channel, role="citizen"):
    with live.websocket_connect(f"{WS}/{channel}") as socket:
        socket.send_json({"type": "auth", "data": {"token": token_of(auth, user, role)}})
        return close_code(socket)


# ---------------------------------------------------------------- Xác thực
def test_khong_gui_token_qua_5_giay_bi_dong(live, monkeypatch):
    monkeypatch.setattr(ws_module, "AUTH_TIMEOUT", 0.2)
    assert rejected(live, None) == 4401


def test_tin_dau_khong_phai_auth_bi_dong(live):
    assert rejected(live, {"type": "ping", "data": {}}) == 4401


def test_token_sai_bi_dong(live):
    assert rejected(live, {"type": "auth", "data": {"token": "khong-phai-jwt"}}) == 4401
    assert rejected(live, {"type": "auth", "data": {}}) == 4401


def test_token_da_thu_hoi_bi_dong(live, key_pair):
    from common_auth import ALGORITHM, ISSUER, revocation

    now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": str(uuid.uuid4()), "role": "citizen", "type": "access", "iss": ISSUER, "iat": now,
                        "exp": now + timedelta(minutes=15), "jti": "da-dang-xuat"}, key_pair[0], algorithm=ALGORITHM)
    revocation.revoke_jti("da-dang-xuat", (now + timedelta(minutes=15)).timestamp())
    try:
        assert rejected(live, {"type": "auth", "data": {"token": token}}) == 4401
    finally:
        revocation.clear()


# ---------------------------------------------------------------- Quyền vào kênh
def test_citizen_vao_lobby_bi_4403(live, auth):
    assert rejected_after_auth(live, auth, uuid.uuid4(), "lobby") == 4403


def test_luat_su_chua_duoc_hanh_nghe_vao_lobby_bi_4403(live, auth, upstream):
    assert rejected_after_auth(live, auth, upstream.lawyer(practicing=False), "lobby", "lawyer") == 4403


def test_nguoi_ngoai_va_admin_vao_cuoc_tu_van_bi_4403(live, auth, rest, upstream):
    cid = rest.open_chat(uuid.uuid4(), upstream.lawyer())
    assert rejected_after_auth(live, auth, uuid.uuid4(), cid) == 4403
    assert rejected_after_auth(live, auth, uuid.uuid4(), cid, "admin") == 4403


def test_cuoc_tu_van_khong_ton_tai_bi_4404(live, auth):
    assert rejected_after_auth(live, auth, uuid.uuid4(), str(uuid.uuid4())) == 4404
    assert rejected_after_auth(live, auth, uuid.uuid4(), "khong-phai-uuid") == 4404


# ---------------------------------------------------------------- Sự kiện
def test_gui_tin_nguoi_con_lai_nhan_tren_kenh_chat_va_me(live, auth, rest, upstream):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    cid = rest.open_chat(citizen, lawyer)
    with connect(live, auth, lawyer, cid, "lawyer") as chat, connect(live, auth, lawyer, "me", "lawyer") as me, \
            connect(live, auth, citizen, "me") as sender_me:
        sent = rest.send(citizen, cid, "Chào luật sư")
        assert sent.status_code == 201
        for socket in (chat, me):
            event = socket.receive_json()
            assert event["type"] == "message.created"
            assert event["data"]["id"] == sent.json()["data"]["id"]
            assert event["data"]["content"] == "Chào luật sư"
        # kênh me của người gửi không nhận tin của chính mình: tin đầu tiên nó nhận là tin của luật sư
        rest.send(lawyer, cid, "Tôi nghe đây", role="lawyer")
        assert sender_me.receive_json()["data"]["content"] == "Tôi nghe đây"


def test_hai_tab_cua_mot_user_deu_nhan(live, auth, rest, upstream):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    cid = rest.open_chat(citizen, lawyer)
    with connect(live, auth, lawyer, "me", "lawyer") as tab1, connect(live, auth, lawyer, "me", "lawyer") as tab2:
        rest.send(citizen, cid)
        assert tab1.receive_json()["type"] == tab2.receive_json()["type"] == "message.created"


def test_mot_socket_chet_khong_chan_socket_con_lai(live, auth, rest, upstream):
    class Dead:
        async def send_json(self, message):
            raise RuntimeError("socket đã chết")

    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    cid = rest.open_chat(citizen, lawyer)
    dead = Dead()
    realtime.manager.join(realtime.me(lawyer), dead)
    with connect(live, auth, lawyer, "me", "lawyer") as alive:
        rest.send(citizen, cid)
        assert alive.receive_json()["type"] == "message.created"
        assert dead not in realtime.manager.rooms[realtime.me(lawyer)]
        rest.send(citizen, cid, "tin thứ hai")
        assert alive.receive_json()["data"]["content"] == "tin thứ hai"


def test_nhan_yeu_cau_lobby_nhan_accepted_va_removed(live, auth, rest, upstream):
    citizen, lawyer, other = uuid.uuid4(), upstream.lawyer(), upstream.lawyer()
    first = rest.request(citizen).json()["data"]["id"]
    second = rest.request(citizen).json()["data"]["id"]
    with connect(live, auth, other, "lobby", "lawyer") as lobby, connect(live, auth, citizen, "me") as citizen_me:
        assert rest.accept(lawyer, first).status_code == 200
        assert lobby.receive_json() == {"type": "consultation.accepted",
                                        "data": {"consultation_id": first, "lawyer_id": str(lawyer)}}
        assert lobby.receive_json() == {"type": "consultation.removed", "data": {"consultation_ids": [second]}}
        assert citizen_me.receive_json()["type"] == "consultation.accepted"


def test_tao_yeu_cau_lobby_nhan_created(live, auth, rest, upstream):
    with connect(live, auth, upstream.lawyer(), "lobby", "lawyer") as lobby:
        created = rest.request(uuid.uuid4()).json()["data"]
        event = lobby.receive_json()
        assert event["type"] == "consultation.created"
        assert event["data"]["id"] == created["id"]


def test_dong_cuoc_gui_closed_cho_hai_ben(live, auth, rest, upstream):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    cid = rest.open_chat(citizen, lawyer)
    with connect(live, auth, citizen, cid) as chat, connect(live, auth, lawyer, "me", "lawyer") as me:
        live.post(f"{BASE}/{cid}/close", headers=auth(citizen))
        for socket in (chat, me):
            event = socket.receive_json()
            assert event["type"] == "consultation.closed"
            assert event["data"]["close_reason"] == "completed"


def test_gui_tin_that_bai_khong_co_su_kien(live, auth, rest, upstream):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    cid = rest.open_chat(citizen, lawyer)
    with connect(live, auth, lawyer, cid, "lawyer") as chat:
        assert rest.send(uuid.uuid4(), cid).status_code == 404            # người ngoài
        assert rest.send(citizen, cid, content="  ").status_code == 422   # nội dung rỗng
        assert rest.send(citizen, cid, "tin hợp lệ").status_code == 201
        assert chat.receive_json()["data"]["content"] == "tin hợp lệ"     # tin đầu tiên nhận được là tin hợp lệ


def test_tin_client_gui_sau_xac_thuc_bi_bo_qua(live, auth, rest, upstream):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    other_cid = rest.open_chat(uuid.uuid4(), upstream.lawyer())
    mine = rest.open_chat(citizen, lawyer)
    with connect(live, auth, citizen, "me") as me:
        me.send_json({"type": "join", "data": {"channel": other_cid}})
        me.send_json({"type": "auth", "data": {"token": token_of(auth, uuid.uuid4())}})
        rest.send(lawyer, mine, "của tôi", role="lawyer")
        assert me.receive_json()["data"]["content"] == "của tôi"
        assert realtime.chat(uuid.UUID(other_cid)) not in realtime.manager.rooms


def test_ket_noi_lai_lay_du_tin_bi_lo(live, auth, rest, upstream):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    cid = rest.open_chat(citizen, lawyer)
    with connect(live, auth, lawyer, cid, "lawyer") as chat:
        first = rest.send(citizen, cid, "tin 1").json()["data"]
        assert chat.receive_json()["data"]["id"] == first["id"]
    for _ in range(300):  # server rời phòng khi nhận được ngắt kết nối
        if realtime.chat(uuid.UUID(cid)) not in realtime.manager.rooms:
            break
        time.sleep(0.01)
    assert realtime.chat(uuid.UUID(cid)) not in realtime.manager.rooms
    for text in ("tin 2", "tin 3"):  # gửi lúc luật sư mất kết nối
        rest.send(citizen, cid, text)
    with connect(live, auth, lawyer, cid, "lawyer"):
        missed = live.get(f"{BASE}/{cid}/messages", params={"after": first["id"]}, headers=auth(lawyer, "lawyer"))
    assert [m["content"] for m in missed.json()["data"]["items"]] == ["tin 2", "tin 3"]


# ---------------------------------------------------------------- Đã đọc
def test_read_chi_danh_dau_tin_cua_nguoi_kia(rest, live, auth, upstream):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    cid = rest.open_chat(citizen, lawyer)
    for _ in range(2):
        rest.send(citizen, cid)
    rest.send(lawyer, cid, "trả lời", role="lawyer")

    def unread(user, role="citizen"):
        return live.get(f"{BASE}/{cid}", headers=auth(user, role)).json()["data"]["unread_count"]

    assert (unread(lawyer, "lawyer"), unread(citizen)) == (2, 1)
    read = live.post(f"{BASE}/{cid}/read", headers=auth(lawyer, "lawyer"))
    assert read.status_code == 200
    assert read.json()["data"] == {"consultation_id": cid, "unread_count": 0}
    assert (unread(lawyer, "lawyer"), unread(citizen)) == (0, 1)  # tin của luật sư gửi citizen vẫn chưa đọc
    assert live.post(f"{BASE}/{cid}/read", headers=auth(lawyer, "lawyer")).status_code == 200  # gọi lại vẫn 200


def test_read_nguoi_ngoai_admin_va_cuoc_cho_deu_404(rest, live, auth, upstream):
    citizen = uuid.uuid4()
    cid = rest.open_chat(citizen, upstream.lawyer())
    assert live.post(f"{BASE}/{cid}/read", headers=auth(uuid.uuid4())).status_code == 404
    assert live.post(f"{BASE}/{cid}/read", headers=auth(uuid.uuid4(), "admin")).status_code == 404
    waiting = rest.request(citizen).json()["data"]["id"]
    assert live.post(f"{BASE}/{waiting}/read", headers=auth(citizen)).status_code == 404
    assert live.post(f"{BASE}/{cid}/read").status_code == 401


def test_read_dung_duoc_khi_cuoc_da_dong(rest, live, auth, upstream):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    cid = rest.open_chat(citizen, lawyer)
    rest.send(lawyer, cid, "tạm biệt", role="lawyer")
    live.post(f"{BASE}/{cid}/close", headers=auth(citizen))
    assert live.post(f"{BASE}/{cid}/read", headers=auth(citizen)).json()["data"]["unread_count"] == 0


def test_kiem_tra_quyen_kenh_chat_khong_giu_ket_noi_db(api, db, upstream):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    consultation_id = str(api.open_chat(citizen, lawyer))
    room = ws_module._chat_room(db, ws_module.Caller(citizen, "citizen"), consultation_id)
    assert room == realtime.chat(uuid.UUID(consultation_id))
    assert not db.in_transaction()


# ---------------------------------------------------------------- Cookie của trình duyệt
def cookie_headers(auth, user, origin="http://localhost:3000", role="citizen"):
    headers = {"Cookie": f"access_token={token_of(auth, user, role)}"}
    return {**headers, "Origin": origin} if origin else headers


def test_cookie_dung_origin_vao_phong_khong_can_tin_xac_thuc(live, auth):
    user = uuid.uuid4()
    with live.websocket_connect(f"{WS}/me", headers=cookie_headers(auth, user)):
        for _ in range(300):
            if realtime.manager.rooms.get(realtime.me(user)):
                break
            time.sleep(0.01)
        else:
            raise AssertionError("socket chưa vào phòng")


@pytest.mark.parametrize("origin", ["https://trang-la.example", None])
def test_cookie_sai_hoac_thieu_origin_bi_4403(live, auth, origin):
    with live.websocket_connect(f"{WS}/me", headers=cookie_headers(auth, uuid.uuid4(), origin)) as socket:
        assert close_code(socket) == 4403


def test_cookie_token_sai_bi_4401(live):
    headers = {"Cookie": "access_token=sai", "Origin": "http://localhost:3000"}
    with live.websocket_connect(f"{WS}/me", headers=headers) as socket:
        assert close_code(socket) == 4401
