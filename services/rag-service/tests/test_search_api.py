import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.database import get_db
from app.main import app
from app.services.embedding import EmbeddingError

URL = "/internal/v1/rag/search"
TOKEN = {"X-Internal-Token": "test-internal-token"}

CHUNK = {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "content": "Điều 4. Điều kiện chuyển nhượng quyền sử dụng đất ...",
    "score": 0.87,
    "section_info": {"chuong": "II", "dieu": "4", "khoan": "1", "diem": None},
    "document": {
        "id": "f1e2d3c4-b5a6-7890-abcd-ef1234567890",
        "title": "Luật Mẫu",
        "doc_number": "01/2099/QH-MAU",
        "doc_type": "luat",
    },
}


@pytest.fixture
def client(monkeypatch):
    calls = []

    def fake_search(db, embedder, query, top_k, doc_types):
        calls.append({"query": query, "top_k": top_k, "doc_types": doc_types})
        return [CHUNK]

    monkeypatch.setattr("app.api.v1.internal.search_chunks", fake_search)
    app.dependency_overrides[get_db] = lambda: iter([None])
    c = TestClient(app, raise_server_exceptions=False)
    c.calls = calls
    yield c
    app.dependency_overrides.clear()


def test_tim_kiem_thanh_cong_dung_hop_dong(client):
    r = client.post(URL, json={"query": "  chuyển nhượng đất  ", "top_k": 3, "doc_types": ["luat"]},
                    headers={**TOKEN, "X-Request-ID": "req-abc"})
    assert r.status_code == 200
    body = r.json()
    assert body == {"data": {"chunks": [CHUNK]}, "error": None, "request_id": "req-abc"}
    assert r.headers["X-Request-ID"] == "req-abc"
    assert client.calls == [{"query": "chuyển nhượng đất", "top_k": 3, "doc_types": ["luat"]}]


def test_mac_dinh_top_k_5(client):
    client.post(URL, json={"query": "x"}, headers=TOKEN)
    assert client.calls[0]["top_k"] == 5 and client.calls[0]["doc_types"] is None


def test_agent_service_doc_duoc_ket_qua(client):
    """Đúng các trường mà agent-service/app/services/rag_client.py + agent.py dùng."""
    chunk = client.post(URL, json={"query": "x"}, headers=TOKEN).json()["data"]["chunks"][0]
    assert {"id", "content", "score", "section_info", "document"} <= chunk.keys()
    assert {"title", "doc_number"} <= chunk["document"].keys()


@pytest.mark.parametrize("headers", [{}, {"X-Internal-Token": "sai"}])
def test_sai_hoac_thieu_token_401(client, headers):
    r = client.post(URL, json={"query": "x"}, headers=headers)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"
    assert client.calls == []


def test_server_chua_cau_hinh_token_500(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("INTERNAL_TOKEN", "")
    get_settings.cache_clear()
    r = client.post(URL, json={"query": "x"}, headers=TOKEN)
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"query": ""}, "query"),
        ({"query": "   "}, "query"),
        ({"query": "x" * 1001}, "query"),
        ({}, "query"),
        ({"query": "x", "top_k": 0}, "top_k"),
        ({"query": "x", "top_k": 21}, "top_k"),
        ({"query": "x", "doc_types": ["hien_phap"]}, "doc_types.0"),
    ],
)
def test_du_lieu_sai_422(client, payload, field):
    r = client.post(URL, json=payload, headers=TOKEN)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert field in [d["field"] for d in err["details"]]
    assert client.calls == []


def test_loi_embedding_503(client, monkeypatch):
    def broken(*a, **kw):
        raise EmbeddingError("429 hết quota")

    monkeypatch.setattr("app.api.v1.internal.search_chunks", broken)
    r = client.post(URL, json={"query": "x"}, headers=TOKEN)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "EMBEDDING_SERVICE_UNAVAILABLE"
    assert "429" not in r.json()["error"]["message"]  # không lộ chi tiết nội bộ


def test_loi_database_503(client, monkeypatch):
    def broken(*a, **kw):
        raise OperationalError("select", {}, Exception("connection refused"))

    monkeypatch.setattr("app.api.v1.internal.search_chunks", broken)
    r = client.post(URL, json={"query": "x"}, headers=TOKEN)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "RAG_DATABASE_UNAVAILABLE"
    assert "connection" not in r.json()["error"]["message"]


def test_health_van_chay(client):
    assert client.get("/api/v1/rag/health").json() == {"status": "ok", "service": "rag-service"}
