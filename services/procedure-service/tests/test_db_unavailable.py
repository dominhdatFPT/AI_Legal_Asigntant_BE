import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.main import app

client = TestClient(app)


@pytest.fixture
def broken_db(monkeypatch):
    import app.database as database

    # Postgres không tồn tại: mọi truy vấn đều lỗi kết nối.
    monkeypatch.setattr(database, "_engine", create_engine("sqlite:///Z:/khong/ton/tai/db.sqlite"))


@pytest.mark.parametrize("method,path,body", [
    ("get", "/api/v1/procedures", None),
    ("get", "/api/v1/procedures/domains", None),
    ("get", "/api/v1/procedures/domains/land/procedures", None),
    ("get", "/api/v1/procedures/domains/land/names", None),
    ("get", "/api/v1/procedures/HKD-001", None),
    ("post", "/api/v1/procedures/search", {"query": "hộ kinh doanh"}),
])
def test_db_down_returns_503_not_mock(broken_db, method, path, body):
    res = getattr(client, method)(path, **({"json": body} if body else {}))
    assert res.status_code == 503
    payload = res.json()
    assert payload["data"] is None
    assert payload["error"]["code"] == "PROCEDURE_DB_UNAVAILABLE"
    assert payload["request_id"]


def test_empty_db_returns_empty_list_not_mock(db_engine):
    from sqlalchemy import text
    with db_engine.begin() as conn:
        conn.execute(text("DELETE FROM public.procedures"))
    res = client.get("/api/v1/procedures/domains/land/procedures")
    assert res.status_code == 200
    assert res.json()["data"]["items"] == []


def test_db_not_configured_returns_503(monkeypatch):
    import app.database as database

    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setenv("PROCEDURE_DATABASE_URL", "")
    res = client.get("/api/v1/procedures/domains")
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "PROCEDURE_DB_UNAVAILABLE"
