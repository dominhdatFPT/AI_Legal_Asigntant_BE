"""Tranh chấp thật trên PostgreSQL. Chỉ chạy khi đặt TEST_POSTGRES_URL (database dùng để thử, VD
postgresql://user:pass@localhost:5432/test); mỗi lần chạy tạo schema riêng rồi xóa."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="Không có TEST_POSTGRES_URL: bỏ qua test tranh chấp trên PostgreSQL")

BASE = "/api/v1/consultations"


@pytest.fixture
def pg_client(upstream):
    from fastapi.testclient import TestClient

    from app.core.database import Base, get_db
    from app.main import app
    from app import models  # noqa: F401

    schema = f"t_{uuid.uuid4().hex[:12]}"
    url = URL.replace("postgresql://", "postgresql+psycopg://", 1)
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f"create schema {schema}"))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"}, pool_size=20)
    Base.metadata.create_all(engine)

    def override():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()
    engine.dispose()
    with admin.begin() as conn:
        conn.execute(text(f"drop schema {schema} cascade"))
    admin.dispose()


def _parallel(calls):
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return [f.result() for f in [pool.submit(c) for c in calls]]


def test_hai_luat_su_nhan_cung_luc_chi_mot_nguoi_duoc(pg_client, make_api, upstream):
    api = make_api(pg_client)
    cid = api.request(uuid.uuid4()).json()["data"]["id"]
    lawyers = [upstream.lawyer() for _ in range(2)]
    for lawyer in lawyers:
        api.agree(lawyer, "lawyer")
    results = _parallel([lambda l=l: api.accept(l, cid, agreed=False) for l in lawyers])
    assert sorted(r.status_code for r in results) == [200, 409]
    assert next(r for r in results if r.status_code == 409).json()["error"]["code"] == "CONSULTATION_ALREADY_ACCEPTED"


def test_mot_luat_su_nhan_nhieu_yeu_cau_cung_luc_toi_da_3(pg_client, make_api, upstream):
    api = make_api(pg_client)
    lawyer = upstream.lawyer()
    api.agree(lawyer, "lawyer")
    ids = [api.request(uuid.uuid4()).json()["data"]["id"] for _ in range(8)]
    results = _parallel([lambda i=i: api.accept(lawyer, i, agreed=False) for i in ids])
    codes = sorted(r.status_code for r in results)
    assert codes == [200] * 3 + [409] * 5
    assert {r.json()["error"]["code"] for r in results if r.status_code == 409} == {"LAWYER_CAPACITY_REACHED"}


def test_user_tao_nhieu_yeu_cau_cung_luc_toi_da_10(pg_client, make_api, upstream):
    api = make_api(pg_client)
    citizen = uuid.uuid4()
    api.agree(citizen)
    results = _parallel([lambda: api.request(citizen, agreed=False) for _ in range(14)])
    assert sorted(r.status_code for r in results) == [201] * 10 + [409] * 4


def test_hai_ben_gui_tin_cung_luc_khong_deadlock(pg_client, make_api, upstream):
    api = make_api(pg_client)
    citizen, lawyer = uuid.uuid4(), upstream.lawyer()
    cid = api.open_chat(citizen, lawyer)
    calls = [(lambda: api.send(citizen, cid)), (lambda: api.send(lawyer, cid, role="lawyer"))] * 6
    assert {r.status_code for r in _parallel(calls)} == {201}
