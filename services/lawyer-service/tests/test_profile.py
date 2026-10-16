"""Lĩnh vực, GET/PATCH /lawyers/me: lần đầu điền số thẻ không cần duyệt, tự chuyển active khi đủ."""

import uuid

import pytest

from app.models import LawyerProfile, LawyerProfileSpecialization

ME = "/api/v1/lawyers/me"


def _profile(db, user_id, *, status="incomplete", source="admin_created", number=None, specs=()):
    profile = LawyerProfile(user_id=user_id, source=source, status=status, bar_card_number=number)
    profile.specializations = [LawyerProfileSpecialization(specialization_code=c) for c in specs]
    db.add(profile)
    db.commit()
    return profile


def _code(res):
    return res.json()["error"]["code"]


def test_danh_sach_linh_vuc_khong_can_dang_nhap(client):
    res = client.get("/api/v1/lawyers/specializations")
    assert res.status_code == 200
    assert res.json()["data"]["items"] == [{"code": "land", "name": "Đất đai"},
                                           {"code": "business", "name": "Hộ kinh doanh"}]


def test_xem_ho_so_can_dang_nhap(client):
    res = client.get(ME)
    assert res.status_code == 401 and _code(res) == "UNAUTHORIZED"


def test_chua_co_ho_so_tra_404(client, auth):
    res = client.get(ME, headers=auth(uuid.uuid4()))
    assert res.status_code == 404 and _code(res) == "LAWYER_NOT_FOUND"


def test_ho_so_chua_hoan_thien_bao_con_thieu_gi(client, db, upstream, auth):
    uid = upstream.user(cccd=False, role="lawyer")
    _profile(db, uid)
    data = client.get(ME, headers=auth(uid, "lawyer")).json()["data"]
    assert data["status"] == "incomplete" and data["is_complete"] is False
    assert data["missing_requirements"] == ["citizen_profile", "bar_card_number", "specializations"]
    assert data["pending_application"] is None and data["is_online"] is False


def test_du_dieu_kien_thi_tu_chuyen_active(client, db, upstream, auth):
    uid = upstream.user(cccd=True, role="lawyer")
    _profile(db, uid, number="111/TP", specs=["land"])
    data = client.get(ME, headers=auth(uid, "lawyer")).json()["data"]
    assert data["status"] == "active" and data["is_complete"] is True and data["missing_requirements"] == []


@pytest.mark.parametrize("kind,status,code", [("down", 503, "USER_UNAVAILABLE"), ("timeout", 504, "USER_TIMEOUT"),
                                              ("bad", 502, "USER_BAD_RESPONSE")])
def test_xem_ho_so_user_service_loi(client, db, upstream, auth, kind, status, code):
    uid = upstream.user(role="lawyer")
    _profile(db, uid)
    upstream.fail["/internal/v1/users"] = kind
    res = client.get(ME, headers=auth(uid, "lawyer"))
    assert res.status_code == status and _code(res) == code


def test_lan_dau_dien_so_the_luu_ngay_va_active(client, db, upstream, auth):
    uid = upstream.user(cccd=True, role="lawyer")
    _profile(db, uid)
    res = client.patch(ME, headers=auth(uid, "lawyer"), json={
        "bar_card_number": " 12345/TP ", "specialization_codes": ["land", "business"], "law_firm": "VP ABC",
        "years_experience": 8, "bio": "Tư vấn đất đai"})
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["bar_card_number"] == "12345/TP" and data["status"] == "active"
    assert {s["code"] for s in data["specializations"]} == {"land", "business"}


def test_doi_so_the_da_co_phai_tao_don(client, db, upstream, auth):
    uid = upstream.user(role="lawyer")
    _profile(db, uid, status="active", number="111/TP", specs=["land"])
    res = client.patch(ME, headers=auth(uid, "lawyer"), json={"bar_card_number": "222/TP"})
    assert res.status_code == 409 and _code(res) == "CARD_CHANGE_REQUIRES_REVIEW"
    # Gửi lại đúng số đang có (khác hoa thường) thì bỏ qua, các trường khác vẫn lưu.
    res = client.patch(ME, headers=auth(uid, "lawyer"), json={"bar_card_number": "111/tp", "bio": "Mới"})
    assert res.status_code == 200 and res.json()["data"]["bio"] == "Mới"


def test_so_the_trung_nguoi_khac(client, db, upstream, auth):
    _profile(db, uuid.uuid4(), status="active", number="999/TP", specs=["land"])
    uid = upstream.user(role="lawyer")
    _profile(db, uid)
    res = client.patch(ME, headers=auth(uid, "lawyer"), json={"bar_card_number": "999/tp"})
    assert res.status_code == 409 and _code(res) == "BAR_CARD_NUMBER_TAKEN"


def test_sua_ho_so_chi_cho_luat_su(client, db, upstream, auth):
    uid = upstream.user()
    _profile(db, uid)
    res = client.patch(ME, headers=auth(uid, "citizen"), json={"bio": "x"})
    assert res.status_code == 403 and _code(res) == "FORBIDDEN"


def test_ho_so_bi_an_khong_sua_duoc(client, db, upstream, auth):
    from app.models.lawyer import utcnow
    uid = upstream.user(role="lawyer")
    p = _profile(db, uid)
    p.status, p.hidden_at, p.hidden_reason = "hidden", utcnow(), "role_changed"
    db.commit()
    res = client.patch(ME, headers=auth(uid, "lawyer"), json={"bio": "x"})
    assert res.status_code == 404


@pytest.mark.parametrize("body", [{}, {"specialization_codes": []}, {"specialization_codes": ["tax"]},
                                  {"specialization_codes": ["land", "land"]}, {"years_experience": 71},
                                  {"bar_card_number": "   "}, {"bar_card_number": None}, {"bar_card_number": "x" * 51},
                                  {"unknown": 1}])
def test_sua_ho_so_du_lieu_sai_422(client, db, upstream, auth, body):
    uid = upstream.user(role="lawyer")
    _profile(db, uid)
    res = client.patch(ME, headers=auth(uid, "lawyer"), json=body)
    assert res.status_code == 422 and _code(res) == "VALIDATION_ERROR"


def test_user_service_loi_khi_sua_thi_khong_luu(client, db, upstream, auth):
    uid = upstream.user(role="lawyer")
    _profile(db, uid)
    upstream.fail["/internal/v1/users"] = "down"
    res = client.patch(ME, headers=auth(uid, "lawyer"), json={"bar_card_number": "123/TP",
                                                              "specialization_codes": ["land"]})
    assert res.status_code == 503
    db.expire_all()
    assert db.query(LawyerProfile).filter_by(user_id=uid).one().bar_card_number is None
