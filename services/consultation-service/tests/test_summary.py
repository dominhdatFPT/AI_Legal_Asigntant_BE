import uuid

import pytest

from app.models.consultation import Consultation, utcnow
from app.services import consultation_service as service

BASE = "/api/v1/consultations"


def code(response):
    return response.json()["error"]["code"]


def request_with_ai(client, auth, api, citizen, session):
    api.agree(citizen)
    r = client.post(BASE, headers=auth(citizen), json={"question": "Hỏi về sổ đỏ", "source": "lobby",
                                                       "agent_session_id": str(session)})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def test_tao_nen_roi_luat_su_xem_ban_da_luu(client, auth, api, upstream, db):
    citizen, lawyer, session = uuid.uuid4(), upstream.lawyer(), uuid.uuid4()
    upstream.summaries[str(session)] = (citizen, "Mua đất giấy viết tay năm 2010.")
    cid = request_with_ai(client, auth, api, citizen, session)

    service.summarize_in_background(uuid.UUID(cid), lambda: db)  # TestClient không chạy nền với session của test
    calls = len(upstream.agent_bodies)
    assert calls == 1 and upstream.agent_bodies[0]["until"].endswith(("Z", "+00:00"))
    assert db.get(Consultation, uuid.UUID(cid)).ai_summary == "Mua đất giấy viết tay năm 2010."

    r = client.get(f"{BASE}/{cid}/summary", headers=auth(lawyer, "lawyer"))
    assert r.status_code == 200 and r.json()["data"] == {"consultation_id": cid,
                                                           "ai_summary": "Mua đất giấy viết tay năm 2010."}
    assert len(upstream.agent_bodies) == calls  # đã lưu thì không gọi Agent lần nữa


def test_chua_co_ban_luu_thi_tao_lai_khi_xem(client, auth, api, upstream, db):
    citizen, lawyer, session = uuid.uuid4(), upstream.lawyer(), uuid.uuid4()
    upstream.summaries[str(session)] = (citizen, "Tóm tắt A")
    cid = request_with_ai(client, auth, api, citizen, session)

    r = client.get(f"{BASE}/{cid}/summary", headers=auth(lawyer, "lawyer"))

    assert r.status_code == 200 and r.json()["data"]["ai_summary"] == "Tóm tắt A"
    assert db.get(Consultation, uuid.UUID(cid)).ai_summary == "Tóm tắt A"


def test_khong_ghi_de_ban_da_luu(db):
    from app.repositories import consultation_repository as repo

    c = Consultation(citizen_user_id=uuid.uuid4(), source="lobby", question="q", status="waiting",
                     last_activity_at=utcnow(), created_at=utcnow(), updated_at=utcnow())
    db.add(c)
    db.commit()
    assert repo.save_summary(db, c.id, "bản đầu") == 1
    assert repo.save_summary(db, c.id, "bản sau") == 0
    assert repo.get_summary(db, c.id) == "bản đầu"


def test_khong_co_agent_session_id(client, auth, api, upstream):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    cid = api.request(citizen).json()["data"]["id"]

    r = client.get(f"{BASE}/{cid}/summary", headers=auth(lawyer, "lawyer"))

    assert r.status_code == 404 and code(r) == "SUMMARY_NOT_AVAILABLE"
    assert upstream.agent_bodies == []


@pytest.mark.parametrize("setup", ["hoi_thoai_da_xoa", "chua_co_tin_truoc_until"])
def test_agent_khong_co_tom_tat(client, auth, api, upstream, setup):
    citizen, lawyer, session = uuid.uuid4(), upstream.lawyer(), uuid.uuid4()
    if setup == "chua_co_tin_truoc_until":
        upstream.summaries[str(session)] = (citizen, None)  # Agent trả 422 CONVERSATION_EMPTY
    cid = request_with_ai(client, auth, api, citizen, session)

    r = client.get(f"{BASE}/{cid}/summary", headers=auth(lawyer, "lawyer"))

    assert r.status_code == 404 and code(r) == "SUMMARY_NOT_AVAILABLE"


@pytest.mark.parametrize("kind,status,error", [("timeout", 504, "AGENT_TIMEOUT"), ("down", 503, "AGENT_UNAVAILABLE"),
                                               ("bad", 502, "AGENT_BAD_RESPONSE")])
def test_agent_loi(client, auth, api, upstream, kind, status, error):
    citizen, lawyer, session = uuid.uuid4(), upstream.lawyer(), uuid.uuid4()
    cid = request_with_ai(client, auth, api, citizen, session)
    upstream.fail["/internal/v1/agent"] = kind

    r = client.get(f"{BASE}/{cid}/summary", headers=auth(lawyer, "lawyer"))

    assert r.status_code == status and code(r) == error


def test_nguoi_khong_duoc_xem_la_404(client, auth, api, upstream):
    citizen, session = uuid.uuid4(), uuid.uuid4()
    upstream.summaries[str(session)] = (citizen, "x")
    cid = request_with_ai(client, auth, api, citizen, session)
    other_citizen, admin = uuid.uuid4(), uuid.uuid4()
    not_practicing = upstream.lawyer(practicing=False)

    for headers in (auth(citizen), auth(other_citizen), auth(admin, "admin"), auth(not_practicing, "lawyer")):
        r = client.get(f"{BASE}/{cid}/summary", headers=headers)
        assert r.status_code == 404 and code(r) == "CONSULTATION_NOT_FOUND"
    assert upstream.agent_bodies == []


def test_luat_su_da_nhan_van_xem_duoc(client, auth, api, upstream):
    citizen, lawyer, session = uuid.uuid4(), upstream.lawyer(), uuid.uuid4()
    upstream.summaries[str(session)] = (citizen, "Tóm tắt")
    cid = request_with_ai(client, auth, api, citizen, session)
    assert api.accept(lawyer, cid).status_code == 200
    upstream.practicing.discard(lawyer)  # đã nhận rồi thì không cần còn hành nghề để xem

    assert client.get(f"{BASE}/{cid}/summary", headers=auth(lawyer, "lawyer")).status_code == 200


def test_nen_loi_khong_nem_ra_ngoai(db, upstream):
    service.summarize_in_background(uuid.uuid4(), lambda: db)  # không tồn tại: im lặng
