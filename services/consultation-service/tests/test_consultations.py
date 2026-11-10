import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.consultation import (
    IN_PROGRESS,
    WAITING,
    Consultation,
    utcnow,
)

BASE = "/api/v1/consultations"


def code(response):
    return response.json()["error"]["code"]


def waiting_of(db, citizen):
    return db.scalars(select(Consultation).where(Consultation.citizen_user_id == citizen,
                                                 Consultation.status == WAITING)).all()


def age(db, consultation_id, days):
    c = db.get(Consultation, uuid.UUID(consultation_id))
    c.created_at = utcnow() - timedelta(days=days)
    db.commit()


# ---------------------------------------------------------------- Xác thực, vai trò, điều khoản
def test_thieu_token(client):
    for method, path in [("get", ""), ("post", ""), ("get", "/lobby"), ("get", "/terms"), ("post", "/terms/accept"),
                         ("get", f"/{uuid.uuid4()}"), ("post", f"/{uuid.uuid4()}/accept"),
                         ("get", f"/{uuid.uuid4()}/messages"), ("post", f"/{uuid.uuid4()}/messages"),
                         ("post", f"/{uuid.uuid4()}/close")]:
        r = getattr(client, method)(BASE + path)
        assert r.status_code == 401 and code(r) == "UNAUTHORIZED", (method, path)


def test_admin_khong_dung_duoc_tinh_nang_nguoi_dan(client, auth, upstream):
    admin = uuid.uuid4()
    body = {"question": "hỏi", "source": "lobby"}
    assert client.post(BASE, headers=auth(admin, "admin"), json=body).status_code == 403
    assert client.get(BASE, headers=auth(admin, "admin")).status_code == 403
    assert client.get(BASE + "/lobby", headers=auth(admin, "admin")).status_code == 403


def test_chua_dong_y_dieu_khoan(client, auth, api, upstream):
    r = api.request(uuid.uuid4(), agreed=False)
    assert r.status_code == 403 and code(r) == "TERMS_NOT_ACCEPTED"

    citizen = uuid.uuid4()
    created = api.request(citizen).json()["data"]["id"]
    lawyer = upstream.lawyer()
    r = api.accept(lawyer, created, agreed=False)
    assert r.status_code == 403 and code(r) == "TERMS_NOT_ACCEPTED"


def test_dieu_khoan(client, auth, upstream):
    user = uuid.uuid4()
    h = auth(user)
    assert client.get(BASE + "/terms", headers=h).json()["data"] == {"version": "2026-11-01", "accepted": False}
    wrong = client.post(BASE + "/terms/accept", headers=h, json={"terms_version": "2020-01-01"})
    assert wrong.status_code == 422 and wrong.json()["error"]["details"][0]["field"] == "terms_version"
    for _ in range(2):  # gọi lại vẫn 200
        ok = client.post(BASE + "/terms/accept", headers=h, json={"terms_version": "2026-11-01"})
        assert ok.status_code == 200 and ok.json()["data"]["accepted"] is True
    assert client.get(BASE + "/terms", headers=h).json()["data"]["accepted"] is True


def test_cau_hoi_khong_hop_le(client, auth, api):
    user = uuid.uuid4()
    api.agree(user)
    for body in [{"question": "   ", "source": "lobby"}, {"question": "x" * 2001, "source": "lobby"},
                 {"source": "lobby"}, {"question": "hỏi", "source": "direct"}]:
        r = client.post(BASE, headers=auth(user), json=body)
        assert r.status_code == 422 and code(r) == "VALIDATION_ERROR", body


def test_direct_chua_cho_phep(client, auth, api):
    user = uuid.uuid4()
    api.agree(user)
    r = client.post(BASE, headers=auth(user), json={"question": "hỏi", "source": "direct", "lawyer_id": str(uuid.uuid4())})
    assert r.status_code == 422 and code(r) == "DIRECT_NOT_ALLOWED"


# ---------------------------------------------------------------- Giới hạn 10 yêu cầu chờ
def test_chan_yeu_cau_thu_11(api, db):
    citizen = uuid.uuid4()
    for _ in range(10):
        assert api.request(citizen).status_code == 201
    r = api.request(citizen)
    assert r.status_code == 409 and code(r) == "WAITING_LIMIT_REACHED"
    assert len(waiting_of(db, citizen)) == 10


def test_yeu_cau_qua_han_khong_bi_tinh(api, db):
    citizen = uuid.uuid4()
    ids = [api.request(citizen).json()["data"]["id"] for _ in range(10)]
    age(db, ids[0], 11)
    assert api.request(citizen).status_code == 201
    assert api.request(citizen).status_code == 409


def test_luat_su_cung_tao_duoc_yeu_cau(api, upstream):
    lawyer = upstream.lawyer()
    assert api.request(lawyer, role="lawyer").status_code == 201


# ---------------------------------------------------------------- Hàng chờ
def test_hang_cho_chi_luat_su_duoc_hanh_nghe(client, auth, api, upstream):
    api.request(uuid.uuid4())
    citizen = uuid.uuid4()
    r = client.get(BASE + "/lobby", headers=auth(citizen))
    assert r.status_code == 403 and code(r) == "FORBIDDEN"
    r = client.get(BASE + "/lobby", headers=auth(upstream.lawyer(practicing=False), "lawyer"))
    assert r.status_code == 403 and code(r) == "LAWYER_NOT_APPROVED"
    r = client.get(BASE + "/lobby", headers=auth(upstream.lawyer(locked=True), "lawyer"))
    assert r.status_code == 403 and code(r) == "ACCOUNT_LOCKED"


def test_hang_cho_khong_co_yeu_cau_cua_minh_va_qua_han(client, auth, api, upstream, db):
    lawyer = upstream.lawyer()
    mine = api.request(lawyer, role="lawyer").json()["data"]["id"]
    first = api.request(uuid.uuid4()).json()["data"]["id"]
    second = api.request(uuid.uuid4()).json()["data"]["id"]
    old = api.request(uuid.uuid4()).json()["data"]["id"]
    age(db, old, 11)
    age(db, first, 2)

    r = client.get(BASE + "/lobby", headers=auth(lawyer, "lawyer"))
    assert r.status_code == 200
    data = r.json()["data"]
    assert [i["id"] for i in data["items"]] == [first, second]  # cũ nhất lên trước
    assert mine not in [i["id"] for i in data["items"]] and old not in [i["id"] for i in data["items"]]
    assert data["pagination"] == {"page": 1, "page_size": 20, "total": 2, "total_pages": 1}

    page2 = client.get(BASE + "/lobby?page=2&page_size=1", headers=auth(lawyer, "lawyer")).json()["data"]
    assert [i["id"] for i in page2["items"]] == [second] and page2["pagination"]["total_pages"] == 2


def test_chi_tiet_yeu_cau_dang_cho(client, auth, api, upstream, db):
    citizen = uuid.uuid4()
    cid = api.request(citizen).json()["data"]["id"]
    assert client.get(f"{BASE}/{cid}", headers=auth(citizen)).status_code == 200
    assert client.get(f"{BASE}/{cid}", headers=auth(upstream.lawyer(), "lawyer")).status_code == 200
    # Luật sư chưa duyệt, citizen khác, admin: 404
    for who, role in [(upstream.lawyer(practicing=False), "lawyer"), (uuid.uuid4(), "citizen"),
                      (uuid.uuid4(), "admin")]:
        r = client.get(f"{BASE}/{cid}", headers=auth(who, role))
        assert r.status_code == 404 and code(r) == "CONSULTATION_NOT_FOUND"
    age(db, cid, 11)
    assert client.get(f"{BASE}/{cid}", headers=auth(citizen)).status_code == 404


def test_cuoc_da_xoa_noi_dung_la_404(client, auth, api, upstream, db):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    cid = api.open_chat(citizen, lawyer)
    api.send(citizen, cid)
    assert client.post(f"{BASE}/{cid}/close", headers=auth(citizen)).status_code == 200
    c = db.get(Consultation, uuid.UUID(cid))
    c.question, c.ai_summary, c.purged_at = None, None, utcnow()
    db.commit()
    assert client.get(f"{BASE}/{cid}", headers=auth(citizen)).status_code == 404
    assert client.get(f"{BASE}/{cid}/messages", headers=auth(citizen)).status_code == 404
    assert client.get(BASE, headers=auth(citizen)).json()["data"]["items"] == []


# ---------------------------------------------------------------- Nhận yêu cầu
def test_luat_su_khong_nhan_duoc_yeu_cau_cua_minh(api, upstream):
    lawyer = upstream.lawyer()
    cid = api.request(lawyer, role="lawyer").json()["data"]["id"]
    r = api.accept(lawyer, cid)
    assert r.status_code == 409 and code(r) == "CANNOT_ACCEPT_OWN_CONSULTATION"


def test_nhan_yeu_cau_dung_vai_tro(client, auth, api, upstream):
    cid = api.request(uuid.uuid4()).json()["data"]["id"]
    citizen = uuid.uuid4()
    api.agree(citizen)
    r = client.post(f"{BASE}/{cid}/accept", headers=auth(citizen))
    assert r.status_code == 403 and code(r) == "FORBIDDEN"
    assert api.accept(upstream.lawyer(practicing=False), cid).status_code == 403
    r = api.accept(upstream.lawyer(locked=True), cid)
    assert r.status_code == 403 and code(r) == "ACCOUNT_LOCKED"
    assert api.accept(uuid.uuid4(), str(uuid.uuid4())).status_code == 403  # citizen
    assert api.accept(upstream.lawyer(), str(uuid.uuid4())).status_code == 404


def test_nhan_yeu_cau_qua_han_la_404(api, upstream, db):
    cid = api.request(uuid.uuid4()).json()["data"]["id"]
    age(db, cid, 11)
    r = api.accept(upstream.lawyer(), cid)
    assert r.status_code == 404 and code(r) == "CONSULTATION_NOT_FOUND"


def test_nhan_thanh_cong_xoa_cac_yeu_cau_cho_khac(api, upstream, db):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    ids = [api.request(citizen).json()["data"]["id"] for _ in range(3)]
    other = api.request(uuid.uuid4()).json()["data"]["id"]

    r = api.accept(lawyer, ids[1])
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "in_progress" and data["lawyer_id"] == str(lawyer) and data["accepted_at"]
    assert waiting_of(db, citizen) == []
    assert db.get(Consultation, uuid.UUID(ids[0])) is None
    assert db.get(Consultation, uuid.UUID(other)).status == WAITING  # yêu cầu của người khác còn nguyên


def test_luat_su_khac_nhan_sau_la_409(api, upstream):
    cid = api.request(uuid.uuid4()).json()["data"]["id"]
    assert api.accept(upstream.lawyer(), cid).status_code == 200
    r = api.accept(upstream.lawyer(), cid)
    assert r.status_code == 409 and code(r) == "CONSULTATION_ALREADY_ACCEPTED"


def test_luat_su_co_3_cuoc_khong_nhan_duoc_cuoc_thu_4(api, upstream, db):
    lawyer = upstream.lawyer()
    for _ in range(3):
        api.open_chat(uuid.uuid4(), lawyer)
    cid = api.request(uuid.uuid4()).json()["data"]["id"]
    r = api.accept(lawyer, cid)
    assert r.status_code == 409 and code(r) == "LAWYER_CAPACITY_REACHED"
    assert db.get(Consultation, uuid.UUID(cid)).status == WAITING


def test_dong_cuoc_giai_phong_cho_cho_luat_su(client, auth, api, upstream):
    lawyer = upstream.lawyer()
    first = api.open_chat(uuid.uuid4(), lawyer)
    for _ in range(2):
        api.open_chat(uuid.uuid4(), lawyer)
    cid = api.request(uuid.uuid4()).json()["data"]["id"]
    assert api.accept(lawyer, cid).status_code == 409
    assert client.post(f"{BASE}/{first}/close", headers=auth(lawyer, "lawyer")).status_code == 200
    assert api.accept(lawyer, cid).status_code == 200


def test_hai_ben_dang_co_cuoc_mo_thi_khong_nhan_them(api, upstream):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    api.open_chat(citizen, lawyer)
    again = api.request(citizen).json()["data"]["id"]  # đang chat riêng vẫn tạo được yêu cầu mới
    r = api.accept(lawyer, again)
    assert r.status_code == 409 and code(r) == "CONSULTATION_ALREADY_OPEN"


def test_unique_index_la_lop_chan_cuoi(api, upstream, db, monkeypatch):
    from app.repositories import consultation_repository as repo

    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    api.open_chat(citizen, lawyer)
    again = api.request(citizen).json()["data"]["id"]
    monkeypatch.setattr(repo, "open_pair_exists", lambda *a: False)  # bỏ qua kiểm tra trước, để index chặn
    r = api.accept(lawyer, again)
    assert r.status_code == 409 and code(r) == "CONSULTATION_ALREADY_OPEN"
    assert db.get(Consultation, uuid.UUID(again)).status == WAITING  # đã rollback


def test_model_chan_trung_cap_dang_mo(db):
    citizen, lawyer = uuid.uuid4(), uuid.uuid4()
    now = utcnow()

    def row():
        return Consultation(citizen_user_id=citizen, lawyer_user_id=lawyer, source="lobby", question="q",
                            status=IN_PROGRESS, accepted_at=now)

    db.add(row())
    db.commit()
    db.add(row())
    with pytest.raises(IntegrityError):
        db.commit()


# ---------------------------------------------------------------- Danh sách của tôi
def test_danh_sach_cua_toi(client, auth, api, upstream):
    lawyer = upstream.lawyer()
    citizen = uuid.uuid4()
    chat = api.open_chat(citizen, lawyer)
    waiting = api.request(citizen).json()["data"]["id"]
    api.send(citizen, chat)

    items = client.get(BASE, headers=auth(citizen)).json()["data"]["items"]
    assert {i["id"] for i in items} == {waiting, chat}
    only = client.get(BASE + "?status=in_progress", headers=auth(lawyer, "lawyer")).json()["data"]["items"]
    assert [i["id"] for i in only] == [chat]
    assert only[0]["last_message"]["content"] == "xin chào"
    assert only[0]["unread_count"] == 1  # tin của citizen, lawyer chưa đọc
    mine = client.get(BASE + "?status=in_progress", headers=auth(citizen)).json()["data"]["items"]
    assert mine[0]["unread_count"] == 0  # tin của chính mình không tính
    assert client.get(BASE + "?status=abc", headers=auth(citizen)).status_code == 422


# ---------------------------------------------------------------- Tin nhắn
def test_nguoi_thu_ba_va_admin_khong_doc_gui_duoc(client, auth, api, upstream):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    cid = api.open_chat(citizen, lawyer)
    api.send(citizen, cid, "bí mật")
    outsiders = [(uuid.uuid4(), "citizen"), (upstream.lawyer(), "lawyer"), (uuid.uuid4(), "admin")]
    for who, role in outsiders:
        r = client.get(f"{BASE}/{cid}/messages", headers=auth(who, role))
        assert r.status_code == 404 and code(r) == "CONSULTATION_NOT_FOUND"
        r = api.send(who, cid, "chen ngang", role=role)
        assert r.status_code == 404
        assert client.get(f"{BASE}/{cid}", headers=auth(who, role)).status_code == 404
        assert client.post(f"{BASE}/{cid}/close", headers=auth(who, role)).status_code == 404
    assert len(client.get(f"{BASE}/{cid}/messages", headers=auth(citizen)).json()["data"]["items"]) == 1


def test_gui_tin_hai_ben(client, auth, api, upstream, db):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    cid = api.open_chat(citizen, lawyer)
    before = db.get(Consultation, uuid.UUID(cid)).last_activity_at
    r = api.send(citizen, cid, "Chào luật sư")
    assert r.status_code == 201
    msg = r.json()["data"]
    assert msg["sender_id"] == str(citizen) and msg["is_read"] is False and msg["consultation_id"] == cid
    assert api.send(lawyer, cid, "Chào bạn", role="lawyer").status_code == 201
    db.expire_all()
    assert db.get(Consultation, uuid.UUID(cid)).last_activity_at > before
    for bad in ["", "   ", "x" * 5001]:
        assert api.send(citizen, cid, bad).status_code == 422


def test_cuoc_cho_hoac_da_dong_khong_gui_duoc_tin(client, auth, api, upstream):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    waiting = api.request(citizen).json()["data"]["id"]
    r = api.send(citizen, waiting)
    assert r.status_code == 409 and code(r) == "CONSULTATION_INVALID_STATUS"

    cid = api.open_chat(uuid.uuid4(), lawyer)
    other = uuid.UUID(client.get(f"{BASE}/{cid}", headers=auth(lawyer, "lawyer")).json()["data"]["citizen_id"])
    assert client.post(f"{BASE}/{cid}/close", headers=auth(other)).status_code == 200
    for who, role in [(other, "citizen"), (lawyer, "lawyer")]:
        r = api.send(who, cid, role=role)
        assert r.status_code == 409 and code(r) == "CONSULTATION_INVALID_STATUS"
    # Đóng rồi vẫn xem được lịch sử
    assert client.get(f"{BASE}/{cid}/messages", headers=auth(other)).status_code == 200


def test_luat_su_bi_khoa_khong_gui_duoc_tin(api, upstream):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    cid = api.open_chat(citizen, lawyer)
    upstream.locked.add(lawyer)
    r = api.send(lawyer, cid, role="lawyer")
    assert r.status_code == 403 and code(r) == "ACCOUNT_LOCKED"
    assert api.send(citizen, cid).status_code == 201  # chỉ luật sư bị kiểm tra


def test_user_service_loi_khi_luat_su_gui_tin(api, upstream):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    cid = api.open_chat(citizen, lawyer)
    upstream.fail["/api/v1/users"] = "timeout"
    r = api.send(lawyer, cid, role="lawyer")
    assert r.status_code == 504 and code(r) == "USER_TIMEOUT"


def test_phan_trang_tin_nhan(client, auth, api, upstream):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    cid = api.open_chat(citizen, lawyer)
    ids = [api.send(citizen, cid, f"tin {n}").json()["data"]["id"] for n in range(1, 6)]

    def page(**params):
        r = client.get(f"{BASE}/{cid}/messages", headers=auth(citizen), params=params)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        return [i["id"] for i in data["items"]], data["has_more"]

    assert page(limit=2) == (ids[3:5], True)  # mới nhất, thứ tự cũ → mới
    assert page(limit=2, before=ids[3]) == (ids[1:3], True)
    assert page(limit=2, before=ids[1]) == (ids[0:1], False)
    assert page(limit=5) == (ids, False)
    assert page(limit=2, after=ids[0]) == (ids[1:3], True)
    assert page(limit=2, after=ids[2]) == (ids[3:5], False)
    assert page(after=ids[4]) == ([], False)
    assert page() == (ids, False)

    both = client.get(f"{BASE}/{cid}/messages", headers=auth(citizen), params={"after": ids[0], "before": ids[2]})
    assert both.status_code == 422
    unknown = client.get(f"{BASE}/{cid}/messages", headers=auth(citizen), params={"after": str(uuid.uuid4())})
    assert unknown.status_code == 422
    assert client.get(f"{BASE}/{cid}/messages", headers=auth(citizen), params={"limit": 101}).status_code == 422


def test_thu_tu_tin_nhan_khong_dao_khi_dong_ho_tho(client, auth, api, upstream, monkeypatch):
    from app.services import consultation_service

    frozen = utcnow()
    monkeypatch.setattr(consultation_service, "utcnow", lambda: frozen)
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    cid = api.open_chat(citizen, lawyer)
    ids = [api.send(citizen, cid, f"tin {n}").json()["data"]["id"] for n in range(5)]
    got = client.get(f"{BASE}/{cid}/messages", headers=auth(citizen)).json()["data"]["items"]
    assert [m["id"] for m in got] == ids


# ---------------------------------------------------------------- Đóng
def test_dong_cuoc(client, auth, api, upstream, db):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    cid = api.open_chat(citizen, lawyer)
    activity = db.get(Consultation, uuid.UUID(cid)).last_activity_at
    r = client.post(f"{BASE}/{cid}/close", headers=auth(lawyer, "lawyer"))
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "closed" and data["close_reason"] == "completed" and data["closed_by"] == str(lawyer)
    assert data["closed_at"]
    db.expire_all()
    assert db.get(Consultation, uuid.UUID(cid)).last_activity_at == activity  # đóng không đổi mốc 10 ngày
    again = client.post(f"{BASE}/{cid}/close", headers=auth(citizen))
    assert again.status_code == 409 and code(again) == "CONSULTATION_INVALID_STATUS"


def test_khong_dong_duoc_yeu_cau_dang_cho(client, auth, api):
    citizen = uuid.uuid4()
    cid = api.request(citizen).json()["data"]["id"]
    r = client.post(f"{BASE}/{cid}/close", headers=auth(citizen))
    assert r.status_code == 409 and code(r) == "CONSULTATION_INVALID_STATUS"
