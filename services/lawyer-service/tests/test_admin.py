"""Admin: danh sách hồ sơ xin làm luật sư, duyệt (đổi vai trò + tạo hồ sơ), từ chối rồi người dùng gửi lại."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.models import LawyerApplication, LawyerProfile, LawyerProfileSpecialization
from app.models.lawyer import utcnow

LAWYERS = "/api/v1/lawyers"
ME = f"{LAWYERS}/me"
ADMIN_APPS = f"{LAWYERS}/applications"


def _code(res):
    return res.json()["error"]["code"]


@pytest.fixture
def admin(upstream, auth):
    return auth(upstream.user(role="admin"), "admin")


def _body(upstream, uid, number):
    return {"bar_card_number": number, "bio": "Tư vấn", "specialization_codes": ["land", "business"],
            "credentials": upstream.credentials(uid)}


def _pending(client, upstream, auth, *, uid=None, number="12345/TP"):
    uid = uid or upstream.user()
    res = client.post(LAWYERS, headers=auth(uid), json=_body(upstream, uid, number))
    assert res.status_code == 201, res.text
    return uid, res.json()["data"]


def _status(client, admin, app_id):
    for status in ("pending", "approved", "rejected"):
        items = client.get(ADMIN_APPS, headers=admin, params={"approval_status": status}).json()["data"]["items"]
        if any(a["id"] == app_id for a in items):
            return status


# ---------------------------------------------------------------- Danh sách
def test_danh_sach_cho_duyet(client, upstream, auth, admin):
    _, app = _pending(client, upstream, auth)
    res = client.get(ADMIN_APPS, headers=admin)
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert [a["id"] for a in data["items"]] == [app["id"]]
    assert data["items"][0]["full_name"] == "Nguyễn Văn A" and len(data["items"][0]["credentials"]) == 2
    assert data["pagination"] == {"page": 1, "page_size": 20, "total": 1, "total_pages": 1}
    assert client.get(ADMIN_APPS, headers=admin, params={"approval_status": "approved"}).json()["data"]["items"] == []


def test_danh_sach_sap_xep_va_phan_trang(client, upstream, auth, admin):
    _, first = _pending(client, upstream, auth, number="1/TP")
    _, second = _pending(client, upstream, auth, number="2/TP")
    asc = client.get(ADMIN_APPS, headers=admin).json()["data"]["items"]
    assert [a["id"] for a in asc] == [first["id"], second["id"]]
    page = client.get(ADMIN_APPS, headers=admin, params={"order": "desc", "page_size": 1}).json()["data"]
    assert [a["id"] for a in page["items"]] == [second["id"]] and page["pagination"]["total_pages"] == 2


@pytest.mark.parametrize("params", [{"approval_status": "draft"}, {"order": "x"}, {"page": 0}, {"page_size": 101}])
def test_danh_sach_tham_so_sai_422(client, admin, params):
    assert client.get(ADMIN_APPS, headers=admin, params=params).status_code == 422


def test_route_admin_phan_quyen(client, upstream, auth):
    uid, app = _pending(client, upstream, auth)
    calls = [("get", ADMIN_APPS, {}), ("post", f"{LAWYERS}/{app['id']}/approve", {}),
             ("post", f"{LAWYERS}/{app['id']}/reject", {"json": {"reason": "x"}})]
    for method, path, kw in calls:
        res = getattr(client, method)(path, headers=auth(uid), **kw)
        assert res.status_code == 403 and _code(res) == "FORBIDDEN"
        assert getattr(client, method)(path, headers=auth(uid, "lawyer"), **kw).status_code == 403
        assert getattr(client, method)(path, **kw).status_code == 401


# ---------------------------------------------------------------- Duyệt
def test_gui_danh_sach_duyet_tao_ho_so_luat_su(client, db, upstream, auth, admin):
    uid, app = _pending(client, upstream, auth)
    card_id = next(c["document_id"] for c in app["credentials"] if c["type"] == "the_luat_su")
    res = client.post(f"{LAWYERS}/{app['id']}/approve", headers=admin)
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["approval_status"] == "approved" and data["reviewed_at"]
    assert upstream.users[uid]["role"] == "lawyer" and ("PUT", f"/internal/v1/users/{uid}/role") in upstream.calls
    db.expire_all()
    profile = db.query(LawyerProfile).filter_by(user_id=uid).one()
    assert (profile.status, profile.source, profile.bar_card_number, str(profile.card_document_id)) == \
        ("active", "self_applied", "12345/TP", card_id)
    assert sorted(profile.specialization_codes) == ["business", "land"]
    me = client.get(ME, headers=auth(uid, "lawyer")).json()["data"]
    assert me["approval_status"] == "approved" and me["id"] == app["id"]
    assert _status(client, admin, app["id"]) == "approved"
    again = client.post(f"{LAWYERS}/{app['id']}/approve", headers=admin)
    assert again.status_code == 409 and _code(again) == "APPLICATION_NOT_PENDING"
    # Không có OCR thì phải là SQL NULL: JSON 'null' vi phạm CHECK jsonb_typeof(...) = 'object' trên Postgres.
    assert db.execute(text("select card_ocr_result is null from lawyer_profiles")).scalar() == 1


def test_duyet_lai_ho_so_da_an(client, db, upstream, auth, admin):
    uid = upstream.user()
    hidden = LawyerProfile(user_id=uid, source="admin_created", status="hidden", hidden_at=utcnow(),
                           hidden_reason="role_changed", bar_card_number="OLD/TP")
    hidden.specializations = [LawyerProfileSpecialization(specialization_code="business")]
    db.add(hidden)
    db.commit()
    _, app = _pending(client, upstream, auth, uid=uid, number="NEW/TP")
    assert client.post(f"{LAWYERS}/{app['id']}/approve", headers=admin).status_code == 200
    db.expire_all()
    profile = db.query(LawyerProfile).filter_by(user_id=uid).one()
    assert (profile.status, profile.source, profile.bar_card_number, profile.hidden_at) == \
        ("active", "self_applied", "NEW/TP", None)
    assert sorted(profile.specialization_codes) == ["business", "land"]


@pytest.mark.parametrize("action,kw", [("approve", {}), ("reject", {"json": {"reason": "x"}})])
def test_don_khong_ton_tai_404(client, admin, action, kw):
    res = client.post(f"{LAWYERS}/{uuid.uuid4()}/{action}", headers=admin, **kw)
    assert res.status_code == 404 and _code(res) == "APPLICATION_NOT_FOUND"
    assert client.post(f"{LAWYERS}/not-uuid/{action}", headers=admin, **kw).status_code == 422


@pytest.mark.parametrize("error", [(409, "USER_DELETED"), (404, "USER_NOT_FOUND")])
def test_duyet_khi_tai_khoan_khong_con_hop_le(client, upstream, auth, admin, error):
    _, app = _pending(client, upstream, auth)
    upstream.role_error = error
    res = client.post(f"{LAWYERS}/{app['id']}/approve", headers=admin)
    assert res.status_code == 409 and _code(res) == "APPLICANT_UNAVAILABLE"
    assert _status(client, admin, app["id"]) == "pending"


@pytest.mark.parametrize("kind,status", [("down", 503), ("timeout", 504), ("bad", 502)])
def test_duyet_user_service_loi_thi_don_van_cho(client, db, upstream, auth, admin, kind, status):
    uid, app = _pending(client, upstream, auth)
    upstream.fail["/internal/v1/users"] = kind
    upstream.calls.clear()
    assert client.post(f"{LAWYERS}/{app['id']}/approve", headers=admin).status_code == status
    assert upstream.calls == [("PUT", f"/internal/v1/users/{uid}/role")]  # thao tác ghi không tự retry
    upstream.fail.clear()
    assert _status(client, admin, app["id"]) == "pending"
    assert db.query(LawyerProfile).filter_by(user_id=uid).first() is None


# ---------------------------------------------------------------- Từ chối rồi gửi lại
def test_tu_choi_bat_buoc_ly_do(client, upstream, auth, admin):
    _, app = _pending(client, upstream, auth)
    for body in ({}, {"reason": "   "}, {"reason": "x" * 501}, {"reason": "x", "extra": 1}):
        res = client.post(f"{LAWYERS}/{app['id']}/reject", headers=admin, json=body)
        assert res.status_code == 422 and _code(res) == "VALIDATION_ERROR"
    assert _status(client, admin, app["id"]) == "pending"


def test_tu_choi_roi_gui_lai_sau_thoi_gian_cho(client, db, upstream, auth, admin):
    uid, app = _pending(client, upstream, auth)
    res = client.post(f"{LAWYERS}/{app['id']}/reject", headers=admin, json={"reason": " Ảnh mờ "})
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["approval_status"] == "rejected" and data["rejected_reason"] == "Ảnh mờ" and data["reviewed_at"]
    assert upstream.users[uid]["role"] == "citizen"
    again = client.post(f"{LAWYERS}/{app['id']}/reject", headers=admin, json={"reason": "x"})
    assert again.status_code == 409 and _code(again) == "APPLICATION_NOT_PENDING"
    assert client.post(f"{LAWYERS}/{app['id']}/approve", headers=admin).status_code == 409

    me = client.get(ME, headers=auth(uid)).json()["data"]
    assert me["approval_status"] == "rejected" and me["rejected_reason"] == "Ảnh mờ"
    res = client.put(ME, headers=auth(uid), json=_body(upstream, uid, "12345/TP"))
    assert res.status_code == 409 and _code(res) == "REAPPLY_COOLDOWN"

    db.get(LawyerApplication, uuid.UUID(app["id"])).reviewed_at = utcnow() - timedelta(hours=25)
    db.commit()
    res = client.put(ME, headers=auth(uid), json=_body(upstream, uid, "12345/TP"))
    assert res.status_code == 200, res.text
    new_id = res.json()["data"]["id"]
    assert new_id != app["id"] and client.get(ME, headers=auth(uid)).json()["data"]["approval_status"] == "pending"
    assert _status(client, admin, new_id) == "pending" and _status(client, admin, app["id"]) == "rejected"
    assert client.post(f"{LAWYERS}/{new_id}/approve", headers=admin).status_code == 200


# ---------------------------------------------------------------- Xem giấy tờ
def test_admin_xem_giay_to_trong_ho_so(client, upstream, auth, admin):
    uid, app = _pending(client, upstream, auth)
    doc = app["credentials"][0]["document_id"]
    res = client.get(f"{LAWYERS}/{app['id']}/credentials/{doc}", headers=admin)
    assert res.status_code == 200 and res.content == b"%PDF-test"
    assert res.headers["content-type"] == "application/pdf"
    assert res.headers["content-disposition"].startswith("inline;")
    assert res.headers["x-content-type-options"] == "nosniff" and res.headers["cache-control"] == "no-store"
    # Chính chủ hay luật sư khác cũng không mở được qua API admin.
    assert client.get(f"{LAWYERS}/{app['id']}/credentials/{doc}", headers=auth(uid)).status_code == 403


def test_chi_mo_duoc_giay_to_dinh_kem_ho_so(client, upstream, auth, admin):
    uid, app = _pending(client, upstream, auth)
    other = upstream.document(uid)  # cùng chủ nhưng không gửi kèm hồ sơ
    res = client.get(f"{LAWYERS}/{app['id']}/credentials/{other}", headers=admin)
    assert res.status_code == 404 and _code(res) == "CREDENTIAL_NOT_FOUND"
    doc = app["credentials"][0]["document_id"]
    res = client.get(f"{LAWYERS}/{uuid.uuid4()}/credentials/{doc}", headers=admin)
    assert res.status_code == 404 and _code(res) == "APPLICATION_NOT_FOUND"


def test_giay_to_chua_quet_xong_409(client, upstream, auth, admin):
    _, app = _pending(client, upstream, auth)
    doc = app["credentials"][0]["document_id"]
    upstream.not_ready.add(uuid.UUID(doc))
    res = client.get(f"{LAWYERS}/{app['id']}/credentials/{doc}", headers=admin)
    assert res.status_code == 409 and _code(res) == "FILE_NOT_READY"
    upstream.documents.pop(uuid.UUID(doc))  # Document Service không còn file
    res = client.get(f"{LAWYERS}/{app['id']}/credentials/{doc}", headers=admin)
    assert res.status_code == 404 and _code(res) == "CREDENTIAL_NOT_FOUND"
