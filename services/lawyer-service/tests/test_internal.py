"""API nội bộ: trạng thái luật sư (cho Consultation) và ẩn hồ sơ (User Service gọi)."""

import uuid

import pytest

from app.models import LawyerApplication, LawyerProfile, LawyerProfileSpecialization

INTERNAL = {"X-Internal-Token": "test-internal-token"}


def _profile(db, uid, status="incomplete", number=None, specs=()):
    profile = LawyerProfile(user_id=uid, source="admin_created", status=status, bar_card_number=number)
    profile.specializations = [LawyerProfileSpecialization(specialization_code=c) for c in specs]
    db.add(profile)
    db.commit()
    return profile


@pytest.mark.parametrize("method,path", [("get", "/status"), ("post", "/hide")])
@pytest.mark.parametrize("headers", [{}, {"X-Internal-Token": "sai"}])
def test_can_internal_token(client, method, path, headers):
    res = getattr(client, method)(f"/internal/v1/lawyers/{uuid.uuid4()}{path}", headers=headers,
                                  **({"json": {"reason": "deleted"}} if method == "post" else {}))
    assert res.status_code == 401


def test_chua_tung_co_ho_so(client):
    data = client.get(f"/internal/v1/lawyers/{uuid.uuid4()}/status", headers=INTERNAL).json()["data"]
    assert data["has_profile"] is False and data["can_practice"] is False and data["profile_status"] is None


def test_trang_thai_tu_chuyen_active_khi_du(client, db, upstream):
    uid = upstream.user(cccd=True, role="lawyer")
    _profile(db, uid, number="1/TP", specs=["land"])
    data = client.get(f"/internal/v1/lawyers/{uid}/status", headers=INTERNAL).json()["data"]
    assert data["profile_status"] == "active" and data["can_practice"] is True and data["is_complete"] is True


def test_trang_thai_chua_du(client, db, upstream):
    uid = upstream.user(cccd=False, role="lawyer")
    _profile(db, uid, number="1/TP", specs=["land"])
    data = client.get(f"/internal/v1/lawyers/{uid}/status", headers=INTERNAL).json()["data"]
    assert data["can_practice"] is False and data["missing_requirements"] == ["citizen_profile"]


@pytest.mark.parametrize("kind,status", [("down", 503), ("timeout", 504), ("bad", 502)])
def test_trang_thai_user_service_loi(client, db, upstream, kind, status):
    uid = upstream.user(role="lawyer")
    _profile(db, uid)
    upstream.fail["/internal/v1/users"] = kind
    assert client.get(f"/internal/v1/lawyers/{uid}/status", headers=INTERNAL).status_code == status


def test_trang_thai_id_sai_422(client):
    assert client.get("/internal/v1/lawyers/abc/status", headers=INTERNAL).status_code == 422


def test_an_ho_so_rut_don_dang_mo_va_goi_lai_an_toan(client, db, upstream):
    uid = upstream.user(role="lawyer")
    _profile(db, uid, status="active", number="1/TP", specs=["land"])
    draft = LawyerApplication(user_id=uid, type="card_change", status="draft")
    db.add(draft)
    db.commit()
    res = client.post(f"/internal/v1/lawyers/{uid}/hide", headers=INTERNAL, json={"reason": "role_changed"})
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["profile_found"] is True and data["is_hidden"] is True
    assert data["withdrawn_application_ids"] == [str(draft.id)]
    again = client.post(f"/internal/v1/lawyers/{uid}/hide", headers=INTERNAL, json={"reason": "deleted"})
    assert again.status_code == 200 and again.json()["data"]["withdrawn_application_ids"] == []
    db.expire_all()
    profile = db.query(LawyerProfile).filter_by(user_id=uid).one()
    assert profile.status == "hidden" and profile.hidden_reason == "role_changed" and profile.is_online is False
    status = client.get(f"/internal/v1/lawyers/{uid}/status", headers=INTERNAL).json()["data"]
    assert status["is_hidden"] is True and status["can_practice"] is False
    # Số thẻ của hồ sơ đã ẩn được người khác dùng lại.
    from app.repositories import lawyer_repository
    assert lawyer_repository.bar_card_taken(db, "1/tp", uuid.uuid4()) is False


def test_an_khi_khong_co_ho_so(client):
    data = client.post(f"/internal/v1/lawyers/{uuid.uuid4()}/hide", headers=INTERNAL,
                       json={"reason": "deleted"}).json()["data"]
    assert (data["profile_found"], data["is_hidden"], data["withdrawn_application_ids"]) == (False, False, [])


@pytest.mark.parametrize("body", [{}, {"reason": "locked"}, {"reason": "deleted", "x": 1}])
def test_an_ho_so_du_lieu_sai(client, body):
    res = client.post(f"/internal/v1/lawyers/{uuid.uuid4()}/hide", headers=INTERNAL, json=body)
    assert res.status_code == 422
