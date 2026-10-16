"""Admin: tạo tài khoản luật sư (bù trừ xóa cứng), danh sách / đếm, chi tiết đơn, duyệt, từ chối."""

import uuid

import pytest
from sqlalchemy.exc import OperationalError

from app.models import LawyerProfile, LawyerProfileSpecialization
from app.models.lawyer import utcnow

LAWYERS = "/api/v1/lawyers"
APPS = f"{LAWYERS}/me/applications"
ADMIN_APPS = f"{LAWYERS}/applications"


def _code(res):
    return res.json()["error"]["code"]


@pytest.fixture
def admin(upstream, auth):
    return auth(upstream.user(role="admin"), "admin")


def _pending(client, upstream, auth, *, uid=None, number="12345/TP", ocr_number=None, full_name="NGUYEN VAN A"):
    uid = uid or upstream.user()
    card = upstream.card(uid, number=ocr_number or number, full_name=full_name)
    data = client.post(APPS, headers=auth(uid), json={
        "type": "new", "bar_card_number": number, "bio": "Tư vấn", "specialization_codes": ["land", "business"],
        "card_document_id": str(card)}).json()["data"]
    res = client.post(f"{APPS}/{data['id']}/submit", headers=auth(uid))
    assert res.status_code == 200, res.text
    return uid, res.json()["data"]


# ---------------------------------------------------------------- Tạo tài khoản luật sư
def test_admin_tao_luat_su(client, upstream, admin):
    res = client.post(f"{LAWYERS}/accounts", headers=admin,
                      json={"email": "LuatSu.C@gmail.com", "password": "matkhau123", "full_name": "Lê Văn C"})
    assert res.status_code == 201, res.text
    data = res.json()["data"]
    assert data["user"]["role"] == "lawyer" and data["user"]["email"] == "luatsu.c@gmail.com"
    assert data["profile"]["status"] == "incomplete" and data["profile"]["source"] == "admin_created"
    assert data["profile"]["card_document_id"] is None
    assert ("POST", "/internal/v1/users") in upstream.calls


def test_tao_luat_su_chi_admin(client, upstream, auth):
    body = {"email": "a@b.vn", "password": "matkhau123", "full_name": "A"}
    assert client.post(f"{LAWYERS}/accounts", json=body).status_code == 401
    res = client.post(f"{LAWYERS}/accounts", headers=auth(upstream.user()), json=body)
    assert res.status_code == 403 and _code(res) == "FORBIDDEN"


@pytest.mark.parametrize("body", [{}, {"email": "x", "password": "matkhau123", "full_name": "A"},
                                  {"email": "a@b.vn", "password": "123", "full_name": "A"},
                                  {"email": "a@b.vn", "password": "matkhau123", "full_name": "  "},
                                  {"email": "a@b.vn", "password": "matkhau123", "full_name": "A", "role": "admin"}])
def test_tao_luat_su_du_lieu_sai(client, admin, body):
    assert client.post(f"{LAWYERS}/accounts", headers=admin, json=body).status_code == 422


def test_tao_luat_su_trung_email_tra_nguyen_ma(client, upstream, admin):
    upstream.taken_emails.add("a@b.vn")
    res = client.post(f"{LAWYERS}/accounts", headers=admin,
                      json={"email": "a@b.vn", "password": "matkhau123", "full_name": "A"})
    assert res.status_code == 409 and _code(res) == "EMAIL_IN_USE"


@pytest.mark.parametrize("kind,status", [("down", 503), ("timeout", 504), ("bad", 502)])
def test_tao_luat_su_user_service_loi(client, upstream, admin, kind, status):
    upstream.fail["/internal/v1/users"] = kind
    res = client.post(f"{LAWYERS}/accounts", headers=admin,
                      json={"email": "a@b.vn", "password": "matkhau123", "full_name": "A"})
    assert res.status_code == status
    # Thao tác ghi không tự retry.
    assert upstream.calls == [("POST", "/internal/v1/users")]


def test_luu_ho_so_loi_thi_xoa_cung_tai_khoan(client, db, upstream, admin, monkeypatch):
    def broken_commit():
        raise OperationalError("insert", {}, Exception("db down"))
    monkeypatch.setattr(db, "commit", broken_commit)
    res = client.post(f"{LAWYERS}/accounts", headers=admin,
                      json={"email": "a@b.vn", "password": "matkhau123", "full_name": "A"})
    assert res.status_code == 503 and _code(res) == "LAWYER_DB_UNAVAILABLE"
    deleted = [path for method, path in upstream.calls if method == "DELETE"]
    assert len(deleted) == 1 and deleted[0].startswith("/internal/v1/users/")


# ---------------------------------------------------------------- Danh sách, đếm
def test_danh_sach_va_dem_khong_tinh_nhap(client, upstream, auth, admin):
    _pending(client, upstream, auth)
    uid = upstream.user()
    client.post(APPS, headers=auth(uid), json={"type": "new"})  # nháp
    listing = client.get(ADMIN_APPS, headers=admin).json()["data"]
    assert [a["status"] for a in listing["items"]] == ["pending"] and listing["pagination"]["total"] == 1
    assert client.get(ADMIN_APPS, headers=admin, params={"type": "card_change"}).json()["data"]["items"] == []
    summary = client.get(f"{ADMIN_APPS}/summary", headers=admin).json()["data"]
    assert summary == {"total": 1, "by_status": {"pending": 1, "approved": 0, "rejected": 0, "withdrawn": 0},
                       "by_type": {"new": 1, "card_change": 0}}


def test_danh_sach_admin_phan_quyen_va_422(client, upstream, auth, admin):
    citizen = auth(upstream.user())
    for path in (ADMIN_APPS, f"{ADMIN_APPS}/summary", f"{ADMIN_APPS}/{uuid.uuid4()}"):
        assert client.get(path, headers=citizen).status_code == 403
        assert client.get(path).status_code == 401
    assert client.get(ADMIN_APPS, headers=admin, params={"status": "draft"}).status_code == 422
    assert client.get(ADMIN_APPS, headers=admin, params={"page_size": 101}).status_code == 422
    assert client.get(f"{ADMIN_APPS}/summary", headers=admin, params={"type": "x"}).status_code == 422


# ---------------------------------------------------------------- Chi tiết đơn
def test_chi_tiet_don_co_anh_cccd_va_co_khong_khop(client, upstream, auth, admin):
    _, app = _pending(client, upstream, auth, number="12345/TP", ocr_number="12346/TP", full_name="NGUYỄN VĂN A")
    res = client.get(f"{ADMIN_APPS}/{app['id']}", headers=admin)
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["card_image"]["expires_in"] == 300 and data["card_image"]["url"].startswith("https://")
    assert data["citizen_profile"]["full_name"] == "Nguyễn Văn A" and data["current_profile"] is None
    assert data["checks"] == {"is_name_mismatch": False, "is_card_number_mismatch": True}


def test_chi_tiet_ocr_loi_thi_co_la_null(client, upstream, auth, admin):
    uid = upstream.user()
    card = upstream.card(uid, status="failed")
    data = client.post(APPS, headers=auth(uid), json={"type": "new", "bar_card_number": "1/TP",
                                                      "specialization_codes": ["land"],
                                                      "card_document_id": str(card)}).json()["data"]
    client.post(f"{APPS}/{data['id']}/submit", headers=auth(uid))
    checks = client.get(f"{ADMIN_APPS}/{data['id']}", headers=admin).json()["data"]["checks"]
    assert checks == {"is_name_mismatch": None, "is_card_number_mismatch": None}


def test_admin_khong_thay_nhap(client, upstream, auth, admin):
    uid = upstream.user()
    data = client.post(APPS, headers=auth(uid), json={"type": "new"}).json()["data"]
    assert client.get(f"{ADMIN_APPS}/{data['id']}", headers=admin).status_code == 404
    assert client.post(f"{ADMIN_APPS}/{data['id']}/approve", headers=admin).status_code == 404


@pytest.mark.parametrize("prefix,kind,status", [("/internal/v1/documents", "down", 503),
                                                ("/internal/v1/documents", "bad", 502),
                                                ("/internal/v1/users", "timeout", 504)])
def test_chi_tiet_service_phu_loi(client, upstream, auth, admin, prefix, kind, status):
    _, app = _pending(client, upstream, auth)
    upstream.fail[prefix] = kind
    assert client.get(f"{ADMIN_APPS}/{app['id']}", headers=admin).status_code == status


# ---------------------------------------------------------------- Duyệt
def test_duyet_don_new_doi_vai_tro_va_tao_ho_so(client, upstream, auth, admin):
    uid, app = _pending(client, upstream, auth)
    res = client.post(f"{ADMIN_APPS}/{app['id']}/approve", headers=admin)
    assert res.status_code == 200, res.text
    assert res.json()["data"]["status"] == "approved" and res.json()["data"]["reviewed_by"]
    assert upstream.users[uid]["role"] == "lawyer"
    me = client.get(f"{LAWYERS}/me", headers=auth(uid, "lawyer")).json()["data"]
    assert me["status"] == "active" and me["source"] == "self_applied" and me["bar_card_number"] == "12345/TP"
    assert {s["code"] for s in me["specializations"]} == {"land", "business"}
    again = client.post(f"{ADMIN_APPS}/{app['id']}/approve", headers=admin)
    assert again.status_code == 409 and _code(again) == "APPLICATION_NOT_PENDING"


def test_duyet_lai_ho_so_da_an(client, db, upstream, auth, admin):
    uid = upstream.user()
    hidden = LawyerProfile(user_id=uid, source="admin_created", status="hidden", hidden_at=utcnow(),
                           hidden_reason="role_changed", bar_card_number="OLD/TP")
    hidden.specializations = [LawyerProfileSpecialization(specialization_code="business")]
    db.add(hidden)
    db.commit()
    _, app = _pending(client, upstream, auth, uid=uid, number="NEW/TP")
    assert client.post(f"{ADMIN_APPS}/{app['id']}/approve", headers=admin).status_code == 200
    db.expire_all()
    profile = db.query(LawyerProfile).filter_by(user_id=uid).one()
    assert (profile.status, profile.source, profile.bar_card_number, profile.hidden_at) == \
        ("active", "self_applied", "NEW/TP", None)
    assert sorted(profile.specialization_codes) == ["business", "land"]


def test_duyet_khi_tai_khoan_khong_con_hop_le(client, upstream, auth, admin):
    _, app = _pending(client, upstream, auth)
    upstream.role_error = (409, "USER_DELETED")
    res = client.post(f"{ADMIN_APPS}/{app['id']}/approve", headers=admin)
    assert res.status_code == 409 and _code(res) == "APPLICANT_UNAVAILABLE"


@pytest.mark.parametrize("kind,status", [("down", 503), ("timeout", 504), ("bad", 502)])
def test_duyet_user_service_loi_thi_don_van_cho(client, upstream, auth, admin, kind, status):
    uid, app = _pending(client, upstream, auth)
    upstream.fail["/internal/v1/users"] = kind
    assert client.post(f"{ADMIN_APPS}/{app['id']}/approve", headers=admin).status_code == status
    upstream.fail.clear()
    assert client.get(f"{APPS}/{app['id']}", headers=auth(uid)).json()["data"]["status"] == "pending"


def test_duyet_doi_the_ghi_de_thong_tin_the(client, db, upstream, auth, admin):
    uid = upstream.user(role="lawyer")
    profile = LawyerProfile(user_id=uid, source="admin_created", status="active", bar_card_number="111/TP")
    profile.specializations = [LawyerProfileSpecialization(specialization_code="land")]
    db.add(profile)
    db.commit()
    card = upstream.card(uid, number="222/TP")
    data = client.post(APPS, headers=auth(uid, "lawyer"), json={"type": "card_change", "bar_card_number": "222/TP",
                                                                 "card_document_id": str(card)}).json()["data"]
    client.post(f"{APPS}/{data['id']}/submit", headers=auth(uid, "lawyer"))
    detail = client.get(f"{ADMIN_APPS}/{data['id']}", headers=admin).json()["data"]
    assert detail["current_profile"]["bar_card_number"] == "111/TP"
    assert client.post(f"{ADMIN_APPS}/{data['id']}/approve", headers=admin).status_code == 200
    assert all(m != "PUT" for m, _ in upstream.calls)  # đổi thẻ không đổi vai trò
    me = client.get(f"{LAWYERS}/me", headers=auth(uid, "lawyer")).json()["data"]
    assert me["bar_card_number"] == "222/TP" and me["card_document_id"] == str(card)


def test_duyet_chi_admin(client, upstream, auth):
    uid, app = _pending(client, upstream, auth)
    assert client.post(f"{ADMIN_APPS}/{app['id']}/approve", headers=auth(uid)).status_code == 403
    assert client.post(f"{ADMIN_APPS}/{app['id']}/approve").status_code == 401
    assert client.post(f"{ADMIN_APPS}/not-uuid/approve", headers=auth(uid, "admin")).status_code == 422


# ---------------------------------------------------------------- Từ chối
def test_tu_choi_bat_buoc_ly_do(client, upstream, auth, admin):
    uid, app = _pending(client, upstream, auth)
    for body in ({}, {"reason": "   "}, {"reason": "x" * 501}):
        assert client.post(f"{ADMIN_APPS}/{app['id']}/reject", headers=admin, json=body).status_code == 422
    res = client.post(f"{ADMIN_APPS}/{app['id']}/reject", headers=admin, json={"reason": " Ảnh mờ "})
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["status"] == "rejected" and data["reject_reason"] == "Ảnh mờ" and data["can_reapply_at"]
    again = client.post(f"{ADMIN_APPS}/{app['id']}/reject", headers=admin, json={"reason": "x"})
    assert again.status_code == 409 and _code(again) == "APPLICATION_NOT_PENDING"
    assert client.post(f"{ADMIN_APPS}/{app['id']}/reject", headers=auth(uid), json={"reason": "x"}).status_code == 403
