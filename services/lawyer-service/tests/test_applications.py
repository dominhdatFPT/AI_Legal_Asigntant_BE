"""Hồ sơ xin làm luật sư của người dùng: POST /lawyers (gửi là chờ duyệt luôn), GET /me, PUT /me (gửi lại sau từ chối)."""

import uuid
from datetime import timedelta

import pytest

from app.models import LawyerApplication, LawyerProfile, LawyerProfileSpecialization
from app.models.lawyer import utcnow

LAWYERS = "/api/v1/lawyers"
ME = f"{LAWYERS}/me"


def _code(res):
    return res.json()["error"]["code"]


def _body(upstream, uid, number="12345/TP"):
    return {"bar_card_number": number, "law_firm": "VP ABC", "years_experience": 8, "bio": "Tư vấn đất đai",
            "specialization_codes": ["land"], "credentials": upstream.credentials(uid)}


def _submit(client, upstream, auth, uid=None, number="12345/TP"):
    uid = uid or upstream.user()
    res = client.post(LAWYERS, headers=auth(uid), json=_body(upstream, uid, number))
    assert res.status_code == 201, res.text
    return uid, res.json()["data"]


def _rejected(db, uid, hours_ago=2, number="1/TP"):
    """Đơn đã bị từ chối cách đây `hours_ago` giờ."""
    app = LawyerApplication(user_id=uid, type="new", status="rejected", reject_reason="Ảnh mờ",
                            reviewed_by=uuid.uuid4(), reviewed_at=utcnow() - timedelta(hours=hours_ago),
                            submitted_at=utcnow() - timedelta(hours=hours_ago + 1), bar_card_number=number,
                            specialization_codes=["land"], created_at=utcnow() - timedelta(hours=hours_ago + 1))
    db.add(app)
    db.commit()
    return app


# ---------------------------------------------------------------- Gửi hồ sơ
def test_gui_ho_so_vao_hang_cho_luon(client, upstream, auth):
    uid = upstream.user()
    body = _body(upstream, uid)
    res = client.post(LAWYERS, headers=auth(uid), json=body)
    assert res.status_code == 201, res.text
    data = res.json()["data"]
    assert data["approval_status"] == "pending" and data["user_id"] == str(uid)
    assert data["full_name"] == "Nguyễn Văn A" and data["rejected_reason"] is None and data["reviewed_at"] is None
    assert data["specializations"] == [{"code": "land", "name": "Đất đai"}]
    assert data["credentials"] == body["credentials"] and data["created_at"]
    me = client.get(ME, headers=auth(uid))
    assert me.status_code == 200 and me.json()["data"]["id"] == data["id"]


def test_gui_ho_so_can_dang_nhap(client):
    assert client.post(LAWYERS, json={}).status_code == 401
    assert client.get(ME).status_code == 401
    assert client.put(ME, json={}).status_code == 401


@pytest.mark.parametrize("role", ["lawyer", "admin"])
def test_chi_citizen_duoc_gui(client, upstream, auth, role):
    uid = upstream.user(role=role)
    for res in (client.post(LAWYERS, headers=auth(uid, role), json=_body(upstream, uid)),
                client.put(ME, headers=auth(uid, role), json=_body(upstream, uid))):
        assert res.status_code == 403 and _code(res) == "FORBIDDEN"


@pytest.mark.parametrize("change", [
    lambda b: b.update(credentials=b["credentials"][:1]),                         # thiếu chứng chỉ hành nghề
    lambda b: b.update(credentials=[b["credentials"][0], b["credentials"][0]]),   # trùng giấy tờ
    lambda b: b.update(credentials=[]),
    lambda b: b.update(credentials=b["credentials"] + [{"document_id": str(uuid.uuid4()), "type": "khac"}
                                                       for _ in range(9)]),  # quá 10 giấy tờ
    lambda b: b["credentials"][0].update(type="cccd"),
    lambda b: b.update(specialization_codes=[]),
    lambda b: b.update(specialization_codes=["tax"]),
    lambda b: b.update(specialization_codes=["land", "land"]),
    lambda b: b.update(bar_card_number="   "),
    lambda b: b.pop("bar_card_number"),
    lambda b: b.update(years_experience=71),
    lambda b: b.update(owner_user_id=str(uuid.uuid4())),
])
def test_du_lieu_sai_422(client, upstream, auth, change):
    uid = upstream.user()
    body = _body(upstream, uid)
    change(body)
    res = client.post(LAWYERS, headers=auth(uid), json=body)
    assert res.status_code == 422 and _code(res) == "VALIDATION_ERROR"


def test_thieu_giay_to_bat_buoc_bao_dung_o(client, upstream, auth):
    uid = upstream.user()
    body = _body(upstream, uid)
    body["credentials"][1]["type"] = "bang_cu_nhan"
    res = client.post(LAWYERS, headers=auth(uid), json=body)
    assert res.status_code == 422 and res.json()["error"]["details"][0]["field"] == "credentials"


@pytest.mark.parametrize("foreign", [True, False])
def test_giay_to_cua_nguoi_khac_hoac_khong_ton_tai(client, upstream, auth, foreign):
    uid = upstream.user()
    body = _body(upstream, uid)
    body["credentials"][0]["document_id"] = str(upstream.document(uuid.uuid4()) if foreign else uuid.uuid4())
    res = client.post(LAWYERS, headers=auth(uid), json=body)
    assert res.status_code == 422 and _code(res) == "VALIDATION_ERROR"
    assert res.json()["error"]["details"][0]["field"] == "credentials"
    assert client.get(ME, headers=auth(uid)).status_code == 404


def test_gui_lan_hai_409(client, upstream, auth):
    uid, _ = _submit(client, upstream, auth)
    res = client.post(LAWYERS, headers=auth(uid), json=_body(upstream, uid, "999/TP"))
    assert res.status_code == 409 and _code(res) == "LAWYER_PROFILE_EXISTS"


def test_da_co_ho_so_luat_su_thi_khong_xin_moi(client, db, upstream, auth):
    uid = upstream.user()
    db.add(LawyerProfile(user_id=uid, source="admin_created", status="incomplete"))
    db.commit()
    res = client.post(LAWYERS, headers=auth(uid), json=_body(upstream, uid))
    assert res.status_code == 409 and _code(res) == "LAWYER_PROFILE_EXISTS"


def test_so_the_trung_don_dang_cho(client, upstream, auth):
    _submit(client, upstream, auth, number="777/TP")
    uid = upstream.user()
    res = client.post(LAWYERS, headers=auth(uid), json=_body(upstream, uid, "777/tp"))
    assert res.status_code == 409 and _code(res) == "BAR_CARD_NUMBER_TAKEN"


def test_so_the_trung_ho_so_dang_hoat_dong(client, db, upstream, auth):
    profile = LawyerProfile(user_id=uuid.uuid4(), source="admin_created", status="active", bar_card_number="888/TP")
    profile.specializations = [LawyerProfileSpecialization(specialization_code="land")]
    db.add(profile)
    db.commit()
    uid = upstream.user()
    res = client.post(LAWYERS, headers=auth(uid), json=_body(upstream, uid, " 888/tp "))
    assert res.status_code == 409 and _code(res) == "BAR_CARD_NUMBER_TAKEN"


def test_chua_khai_cccd_hoac_bi_khoa(client, upstream, auth):
    uid = upstream.user(cccd=False)
    res = client.post(LAWYERS, headers=auth(uid), json=_body(upstream, uid))
    assert res.status_code == 409 and _code(res) == "CITIZEN_PROFILE_REQUIRED"
    upstream.users[uid].update(has_citizen_profile=True, is_locked=True)
    res = client.post(LAWYERS, headers=auth(uid), json=_body(upstream, uid))
    assert res.status_code == 403 and _code(res) == "ACCOUNT_LOCKED"


def test_tai_khoan_khong_ton_tai_401(client, upstream, auth):
    uid = uuid.uuid4()  # có token nhưng User Service không còn tài khoản
    res = client.post(LAWYERS, headers=auth(uid), json=_body(upstream, uid))
    assert res.status_code == 401 and _code(res) == "UNAUTHORIZED"


@pytest.mark.parametrize("prefix,kind,status,code", [
    ("/internal/v1/users", "down", 503, "USER_UNAVAILABLE"),
    ("/internal/v1/users", "timeout", 504, "USER_TIMEOUT"),
    ("/internal/v1/users", "bad", 502, "USER_BAD_RESPONSE"),
    ("/api/v1/documents", "down", 503, "DOCUMENT_UNAVAILABLE"),
    ("/api/v1/documents", "bad", 502, "DOCUMENT_BAD_RESPONSE"),
])
def test_service_phu_loi_thi_khong_luu(client, upstream, auth, prefix, kind, status, code):
    uid = upstream.user()
    upstream.fail[prefix] = kind
    res = client.post(LAWYERS, headers=auth(uid), json=_body(upstream, uid))
    assert res.status_code == status and _code(res) == code
    upstream.fail.clear()
    assert client.get(ME, headers=auth(uid)).status_code == 404


# ---------------------------------------------------------------- Xem hồ sơ của tôi
def test_chua_gui_ho_so_tra_404(client, auth):
    res = client.get(ME, headers=auth(uuid.uuid4()))
    assert res.status_code == 404 and _code(res) == "LAWYER_NOT_FOUND"


def test_me_tra_don_moi_nhat(client, db, upstream, auth, monkeypatch):
    uid = upstream.user()
    _rejected(db, uid, hours_ago=48)
    monkeypatch.setenv("LAWYER_REAPPLY_COOLDOWN_HOURS", "0")
    res = client.put(ME, headers=auth(uid), json=_body(upstream, uid))
    assert res.status_code == 200, res.text
    me = client.get(ME, headers=auth(uid)).json()["data"]
    assert me["id"] == res.json()["data"]["id"] and me["approval_status"] == "pending"


# ---------------------------------------------------------------- Gửi lại sau khi bị từ chối
def test_gui_lai_khi_chua_co_don_404(client, upstream, auth):
    uid = upstream.user()
    res = client.put(ME, headers=auth(uid), json=_body(upstream, uid))
    assert res.status_code == 404 and _code(res) == "LAWYER_NOT_FOUND"


def test_don_dang_cho_khong_gui_lai_duoc(client, upstream, auth):
    uid, _ = _submit(client, upstream, auth)
    res = client.put(ME, headers=auth(uid), json=_body(upstream, uid))
    assert res.status_code == 409 and _code(res) == "APPLICATION_NOT_EDITABLE"


def test_gui_lai_phai_cho_het_thoi_gian(client, db, upstream, auth):
    uid = upstream.user()
    old = _rejected(db, uid, hours_ago=2)
    res = client.put(ME, headers=auth(uid), json=_body(upstream, uid))
    assert res.status_code == 409 and _code(res) == "REAPPLY_COOLDOWN"
    assert "vui lòng chờ đến" in res.json()["error"]["message"]
    # Hết 24 giờ thì gửi lại được: tạo đơn mới, đơn cũ giữ làm lịch sử.
    old.reviewed_at = utcnow() - timedelta(hours=25)
    db.commit()
    res = client.put(ME, headers=auth(uid), json=_body(upstream, uid, "1/TP"))
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["id"] != str(old.id) and data["approval_status"] == "pending" and data["rejected_reason"] is None
    db.expire_all()
    assert db.get(LawyerApplication, old.id).status == "rejected"
    again = client.put(ME, headers=auth(uid), json=_body(upstream, uid))
    assert again.status_code == 409 and _code(again) == "APPLICATION_NOT_EDITABLE"
