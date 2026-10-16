"""Đơn của người dùng: tạo nháp, sửa, gửi duyệt, rút; ảnh thẻ phải của chính mình; thời gian chờ sau khi bị từ chối."""

import uuid
from datetime import timedelta

import pytest

from app.models import LawyerApplication, LawyerProfile, LawyerProfileSpecialization
from app.models.lawyer import utcnow

APPS = "/api/v1/lawyers/me/applications"


def _code(res):
    return res.json()["error"]["code"]


def _new_body(card_id, number="12345/TP"):
    return {"type": "new", "bar_card_number": number, "law_firm": "VP ABC", "years_experience": 8,
            "bio": "Tư vấn đất đai", "specialization_codes": ["land"], "card_document_id": str(card_id)}


def _draft(client, upstream, auth, uid=None, number="12345/TP"):
    uid = uid or upstream.user()
    res = client.post(APPS, headers=auth(uid), json=_new_body(upstream.card(uid, number=number), number))
    assert res.status_code == 201, res.text
    return uid, res.json()["data"]


def _lawyer(db, upstream, number="111/TP"):
    uid = upstream.user(role="lawyer")
    profile = LawyerProfile(user_id=uid, source="self_applied", status="active", bar_card_number=number)
    profile.specializations = [LawyerProfileSpecialization(specialization_code="land")]
    db.add(profile)
    db.commit()
    return uid


# ---------------------------------------------------------------- Tạo nháp
def test_tao_nhap_lay_json_ocr_tu_document(client, upstream, auth):
    uid, data = _draft(client, upstream, auth)
    assert data["status"] == "draft" and data["type"] == "new" and data["user_id"] == str(uid)
    assert data["ocr_result"]["fields"]["bar_card_number"] == "12345/TP"
    assert data["specializations"] == [{"code": "land", "name": "Đất đai"}]
    assert data["submitted_at"] is None and data["can_reapply_at"] is None


def test_tao_don_can_dang_nhap(client):
    res = client.post(APPS, json={"type": "new"})
    assert res.status_code == 401


def test_luat_su_khong_tao_don_new_citizen_khong_tao_card_change(client, db, upstream, auth):
    lawyer = _lawyer(db, upstream)
    assert client.post(APPS, headers=auth(lawyer, "lawyer"), json={"type": "new"}).status_code == 403
    citizen = upstream.user()
    assert client.post(APPS, headers=auth(citizen), json={"type": "card_change"}).status_code == 403
    admin = upstream.user(role="admin")
    assert client.post(APPS, headers=auth(admin, "admin"), json={"type": "new"}).status_code == 403


@pytest.mark.parametrize("body", [{}, {"type": "other"}, {"type": "new", "specialization_codes": ["tax"]},
                                  {"type": "new", "years_experience": -1}, {"type": "new", "card_document_id": "x"},
                                  {"type": "new", "owner_user_id": str(uuid.uuid4())}])
def test_tao_don_du_lieu_sai_422(client, upstream, auth, body):
    res = client.post(APPS, headers=auth(upstream.user()), json=body)
    assert res.status_code == 422 and _code(res) == "VALIDATION_ERROR"


def test_don_doi_the_chi_mang_thong_tin_the(client, db, upstream, auth):
    uid = _lawyer(db, upstream)
    res = client.post(APPS, headers=auth(uid, "lawyer"), json={"type": "card_change", "bio": "x"})
    assert res.status_code == 422 and res.json()["error"]["details"][0]["field"] == "bio"


def test_chi_mot_don_dang_mo(client, upstream, auth):
    uid, _ = _draft(client, upstream, auth)
    res = client.post(APPS, headers=auth(uid), json={"type": "new"})
    assert res.status_code == 409 and _code(res) == "OPEN_APPLICATION_EXISTS"


def test_da_co_ho_so_thi_khong_xin_moi(client, db, upstream, auth):
    uid = upstream.user()
    db.add(LawyerProfile(user_id=uid, source="admin_created", status="incomplete"))
    db.commit()
    res = client.post(APPS, headers=auth(uid), json={"type": "new"})
    assert res.status_code == 409 and _code(res) == "LAWYER_PROFILE_EXISTS"


def test_doi_the_khi_chua_co_so_the(client, db, upstream, auth):
    uid = upstream.user(role="lawyer")
    db.add(LawyerProfile(user_id=uid, source="admin_created", status="incomplete"))
    db.commit()
    res = client.post(APPS, headers=auth(uid, "lawyer"), json={"type": "card_change"})
    assert res.status_code == 409 and _code(res) == "CARD_NOT_SET"


def test_anh_the_cua_nguoi_khac_bi_tu_choi(client, upstream, auth):
    uid = upstream.user()
    other_card = upstream.card(uuid.uuid4())
    res = client.post(APPS, headers=auth(uid), json={"type": "new", "card_document_id": str(other_card)})
    assert res.status_code == 422 and res.json()["error"]["details"][0]["field"] == "card_document_id"
    res = client.post(APPS, headers=auth(uid), json={"type": "new", "card_document_id": str(uuid.uuid4())})
    assert res.status_code == 422


@pytest.mark.parametrize("kind,status,code", [("down", 503, "DOCUMENT_UNAVAILABLE"),
                                              ("timeout", 504, "DOCUMENT_TIMEOUT"),
                                              ("bad", 502, "DOCUMENT_BAD_RESPONSE")])
def test_document_service_loi(client, upstream, auth, kind, status, code):
    uid = upstream.user()
    card = upstream.card(uid)
    upstream.fail["/internal/v1/documents"] = kind
    res = client.post(APPS, headers=auth(uid), json={"type": "new", "card_document_id": str(card)})
    assert res.status_code == status and _code(res) == code


# ---------------------------------------------------------------- Sửa, xem, danh sách
def test_sua_nhap_va_xem_lai(client, upstream, auth):
    uid, data = _draft(client, upstream, auth)
    res = client.patch(f"{APPS}/{data['id']}", headers=auth(uid), json={"bio": None, "specialization_codes":
                                                                        ["land", "business"]})
    assert res.status_code == 200 and res.json()["data"]["bio"] is None
    assert client.get(f"{APPS}/{data['id']}", headers=auth(uid)).json()["data"]["specializations"][1]["code"] == \
        "business"
    listing = client.get(APPS, headers=auth(uid)).json()["data"]
    assert listing["pagination"] == {"page": 1, "page_size": 20, "total": 1, "total_pages": 1}


def test_don_nguoi_khac_tra_404(client, upstream, auth):
    _, data = _draft(client, upstream, auth)
    stranger = auth(upstream.user())
    for res in (client.get(f"{APPS}/{data['id']}", headers=stranger),
                client.patch(f"{APPS}/{data['id']}", headers=stranger, json={"bio": "x"}),
                client.post(f"{APPS}/{data['id']}/submit", headers=stranger),
                client.post(f"{APPS}/{data['id']}/withdraw", headers=stranger)):
        assert res.status_code == 404 and _code(res) == "APPLICATION_NOT_FOUND"


def test_sua_don_rong_hoac_id_sai_422(client, upstream, auth):
    uid, data = _draft(client, upstream, auth)
    assert client.patch(f"{APPS}/{data['id']}", headers=auth(uid), json={}).status_code == 422
    assert client.get(f"{APPS}/not-a-uuid", headers=auth(uid)).status_code == 422


def test_sua_don_pending_van_phai_du(client, upstream, auth):
    uid, data = _draft(client, upstream, auth)
    assert client.post(f"{APPS}/{data['id']}/submit", headers=auth(uid)).status_code == 200
    res = client.patch(f"{APPS}/{data['id']}", headers=auth(uid), json={"specialization_codes": []})
    assert res.status_code == 422
    res = client.patch(f"{APPS}/{data['id']}", headers=auth(uid), json={"law_firm": "VP mới"})
    assert res.status_code == 200 and res.json()["data"]["status"] == "pending"


# ---------------------------------------------------------------- Gửi duyệt
def test_gui_duyet_luu_ho_ten_cccd(client, upstream, auth):
    uid, data = _draft(client, upstream, auth)
    res = client.post(f"{APPS}/{data['id']}/submit", headers=auth(uid))
    assert res.status_code == 200, res.text
    sent = res.json()["data"]
    assert sent["status"] == "pending" and sent["submitted_at"] and sent["applicant_full_name"] == "Nguyễn Văn A"
    again = client.post(f"{APPS}/{data['id']}/submit", headers=auth(uid))
    assert again.status_code == 409 and _code(again) == "APPLICATION_NOT_DRAFT"


def test_gui_duyet_thieu_truong(client, upstream, auth):
    uid = upstream.user()
    data = client.post(APPS, headers=auth(uid), json={"type": "new"}).json()["data"]
    res = client.post(f"{APPS}/{data['id']}/submit", headers=auth(uid))
    assert res.status_code == 422
    assert {d["field"] for d in res.json()["error"]["details"]} == {"card_document_id", "bar_card_number",
                                                                    "specialization_codes"}


def test_gui_duyet_chua_khai_cccd_hoac_bi_khoa(client, upstream, auth):
    uid = upstream.user(cccd=False)
    _, data = _draft(client, upstream, auth, uid)
    res = client.post(f"{APPS}/{data['id']}/submit", headers=auth(uid))
    assert res.status_code == 409 and _code(res) == "CITIZEN_PROFILE_REQUIRED"
    upstream.users[uid].update(has_citizen_profile=True, is_locked=True)
    res = client.post(f"{APPS}/{data['id']}/submit", headers=auth(uid))
    assert res.status_code == 403 and _code(res) == "ACCOUNT_LOCKED"


def test_so_the_trung_don_dang_cho(client, upstream, auth):
    uid1, d1 = _draft(client, upstream, auth, number="777/TP")
    assert client.post(f"{APPS}/{d1['id']}/submit", headers=auth(uid1)).status_code == 200
    uid2, d2 = _draft(client, upstream, auth, number="777/tp")
    res = client.post(f"{APPS}/{d2['id']}/submit", headers=auth(uid2))
    assert res.status_code == 409 and _code(res) == "BAR_CARD_NUMBER_TAKEN"


@pytest.mark.parametrize("kind,status", [("down", 503), ("timeout", 504), ("bad", 502)])
def test_gui_duyet_user_service_loi(client, upstream, auth, kind, status):
    uid, data = _draft(client, upstream, auth)
    upstream.fail["/internal/v1/users"] = kind
    res = client.post(f"{APPS}/{data['id']}/submit", headers=auth(uid))
    assert res.status_code == status and _code(res).startswith("USER_")
    upstream.fail.clear()
    assert client.get(f"{APPS}/{data['id']}", headers=auth(uid)).json()["data"]["status"] == "draft"


def test_cho_24h_sau_khi_bi_tu_choi(client, db, upstream, auth, monkeypatch):
    uid = upstream.user()
    db.add(LawyerApplication(user_id=uid, type="new", status="rejected", reject_reason="Ảnh mờ",
                             reviewed_by=uuid.uuid4(), reviewed_at=utcnow() - timedelta(hours=2),
                             submitted_at=utcnow() - timedelta(hours=3), card_document_id=uuid.uuid4(),
                             bar_card_number="1/TP"))
    db.commit()
    _, data = _draft(client, upstream, auth, uid)  # tạo nháp vẫn được
    res = client.post(f"{APPS}/{data['id']}/submit", headers=auth(uid))
    assert res.status_code == 409 and _code(res) == "REAPPLY_COOLDOWN" and "vui lòng chờ đến" in res.json()["error"]["message"]
    rejected = [a for a in client.get(APPS, headers=auth(uid)).json()["data"]["items"] if a["status"] == "rejected"]
    assert rejected[0]["can_reapply_at"]
    monkeypatch.setenv("LAWYER_REAPPLY_COOLDOWN_HOURS", "1")
    assert client.post(f"{APPS}/{data['id']}/submit", headers=auth(uid)).status_code == 200


# ---------------------------------------------------------------- Rút đơn
def test_rut_don_pending_va_nhap(client, upstream, auth):
    uid, data = _draft(client, upstream, auth)
    client.post(f"{APPS}/{data['id']}/submit", headers=auth(uid))
    res = client.post(f"{APPS}/{data['id']}/withdraw", headers=auth(uid))
    assert res.status_code == 200 and res.json()["data"]["status"] == "withdrawn"
    again = client.post(f"{APPS}/{data['id']}/withdraw", headers=auth(uid))
    assert again.status_code == 409 and _code(again) == "APPLICATION_NOT_EDITABLE"
    assert client.patch(f"{APPS}/{data['id']}", headers=auth(uid), json={"bio": "x"}).status_code == 409
    # Rút xong thì tạo đơn mới được, không tính thời gian chờ.
    _draft(client, upstream, auth, uid, number="555/TP")


def test_doi_the_cua_luat_su(client, db, upstream, auth):
    uid = _lawyer(db, upstream)
    card = upstream.card(uid, number="222/TP")
    res = client.post(APPS, headers=auth(uid, "lawyer"), json={"type": "card_change", "bar_card_number": "222/TP",
                                                                "card_document_id": str(card)})
    assert res.status_code == 201 and res.json()["data"]["specializations"] is None
    sent = client.post(f"{APPS}/{res.json()['data']['id']}/submit", headers=auth(uid, "lawyer"))
    assert sent.status_code == 200
    me = client.get("/api/v1/lawyers/me", headers=auth(uid, "lawyer")).json()["data"]
    assert me["bar_card_number"] == "111/TP" and me["pending_application"]["type"] == "card_change"
