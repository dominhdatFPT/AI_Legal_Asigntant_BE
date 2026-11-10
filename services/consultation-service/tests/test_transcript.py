import uuid
from datetime import timedelta

from sqlalchemy import select

from app.models.consultation import Consultation, ConsultationMessage, ConsultationSummary, utcnow
from app.repositories import consultation_repository as repo
from app.services import transcript_service as ts
from tests.conftest import minimal_transcript


def open_chat_with(api, upstream, db, texts, idle=True):
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    cid = api.open_chat(citizen, lawyer)
    for i, text in enumerate(texts):
        who, role = (citizen, "citizen") if i % 2 == 0 else (lawyer, "lawyer")
        assert api.send(who, cid, text, role).status_code == 201
    if idle:
        c = db.get(Consultation, uuid.UUID(cid))
        c.last_activity_at = utcnow() - timedelta(minutes=11)
        db.commit()
    return uuid.UUID(cid)


def rows(db):
    return list(db.scalars(select(ConsultationSummary)))


def test_im_lang_10_phut_thi_tom_tat_va_giu_tin_goc(api, upstream, db):
    cid = open_chat_with(api, upstream, db, ["Gọi tôi theo 0912345678 nhé", "Vâng"])

    assert ts.run_sweep(lambda: db) == 1

    [row] = rows(db)
    assert row.consultation_id == cid and row.message_count == 2 and row.schema_version == 1
    assert row.prompt_version == "v1" and row.summary["has_substance"] is True
    sent = upstream.transcript_bodies[0]["messages"]
    assert sent[0]["content"] == "Gọi tôi theo [SĐT] nhé" and sent[0]["role"] == "CITIZEN"
    assert upstream.transcript_bodies[0]["previous_summary"] is None
    assert db.scalar(select(ConsultationMessage.id).where(ConsultationMessage.consultation_id == cid).limit(1))
    assert ts.run_sweep(lambda: db) == 0  # đã tóm tắt hết: không gọi Agent nữa
    assert len(upstream.transcript_bodies) == 1


def test_chua_im_lang_thi_chua_tom_tat(api, upstream, db):
    open_chat_with(api, upstream, db, ["Chào luật sư"], idle=False)

    assert ts.run_sweep(lambda: db) == 0 and not upstream.transcript_bodies


def test_doan_sau_mang_ngu_canh_cua_doan_truoc(api, upstream, db):
    cid = open_chat_with(api, upstream, db, ["Hỏi về sổ đỏ"])
    ts.run_sweep(lambda: db)
    c = db.get(Consultation, cid)
    assert api.send(c.citizen_user_id, str(cid), "Thêm một ý nữa").status_code == 201
    c.last_activity_at = utcnow() - timedelta(minutes=11)
    db.commit()

    assert ts.run_sweep(lambda: db) == 1

    assert upstream.transcript_bodies[1]["previous_summary"] == {"overview": "Hỏi về sổ đỏ.",
                                                                 "legal_domains": ["dat_dai"]}
    assert len(rows(db)) == 2


def test_agent_tra_id_bia_thi_khong_luu_va_dung_sau_3_lan(api, upstream, db):
    ts._failures.clear()
    upstream.transcript_reply = lambda body: {**minimal_transcript(body), "citizen_issues": [{"content": "x", "source_ids": ["khong-co"]}]}
    cid = open_chat_with(api, upstream, db, ["Hỏi về sổ đỏ"])

    for _ in range(5):
        assert ts.run_sweep(lambda: db) == 0

    assert rows(db) == [] and db.get(Consultation, cid).summarized_until is None
    assert len(upstream.transcript_bodies) == ts.MAX_FAILURES  # lỗi mãi thì thôi, không đốt LLM mỗi phút
    ts._failures.clear()


def test_agent_sap_thi_giu_tin_de_thu_lai(api, upstream, db):
    ts._failures.clear()
    upstream.fail["/internal/v1/agent/transcripts"] = "down"
    cid = open_chat_with(api, upstream, db, ["Hỏi về sổ đỏ"])

    assert ts.run_sweep(lambda: db) == 0 and db.get(Consultation, cid).summarized_until is None

    upstream.fail.clear()
    ts._failures.clear()
    assert ts.run_sweep(lambda: db) == 1
    ts._failures.clear()


def test_cuoc_da_dong_tom_tat_ngay_khong_cho_im_lang(api, upstream, db):
    cid = open_chat_with(api, upstream, db, ["Hỏi về sổ đỏ"], idle=False)
    c = db.get(Consultation, cid)
    assert api.client.post(f"/api/v1/consultations/{cid}/close", headers=api.auth(c.citizen_user_id)).status_code == 200

    ts.summarize_all(cid, lambda: db)

    assert len(rows(db)) == 1


def test_chat_lien_tuc_du_tin_thi_tom_tat_khong_cho_im_lang(api, upstream, db):
    cid = open_chat_with(api, upstream, db, ["a", "b", "c"], idle=False)

    assert repo.summary_candidates(db, utcnow(), timedelta(minutes=10), batch=4, limit=10) == []
    assert repo.summary_candidates(db, utcnow(), timedelta(minutes=10), batch=3, limit=10) == [cid]


def test_hai_ban_service_cung_tom_tat_mot_doan_chi_ghi_mot_lan(api, upstream, db):
    cid = open_chat_with(api, upstream, db, ["Hỏi về sổ đỏ"])
    end = utcnow()

    assert repo.save_window_summary(db, cid, None, end, 1, {"x": 1}, 1, "v1") is True
    db.commit()
    assert repo.save_window_summary(db, cid, None, end, 1, {"x": 2}, 1, "v1") is False  # mốc đã bị đẩy
    db.rollback()
    assert len(rows(db)) == 1
