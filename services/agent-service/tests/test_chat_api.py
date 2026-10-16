"""Chatbox công khai POST /api/v1/agent/chat: không cần đăng nhập, RAG + Gemini, kèm thủ tục nếu có.
Giả lập agent.process_message và find_procedure, không gọi mạng, không tốn quota Gemini."""
import pytest
from fastapi.testclient import TestClient

PROCEDURE = {"id": "p1", "code": "HKD-001", "name": "Đăng ký hộ kinh doanh"}


@pytest.fixture
def main_module():
    import app.main as main
    return main


@pytest.fixture
def client(main_module):
    return TestClient(main_module.app, raise_server_exceptions=False)


def fake_ai(answer="Theo Khoản 1 Điều 45 Luật Đất đai 2024...", error=None):
    async def process_message(content, history, request_id=None):
        assert history == []  # chatbox công khai không có lịch sử
        if error:
            raise error
        return {"content": answer, "metadata": {"rag_chunk_ids": ["c1", "c2"], "model": "gemini-test", "tokens_used": 10}}
    return process_message


def fake_procedure(result):
    async def find_procedure(message, request_id):
        return result
    return find_procedure


def test_chat_khong_can_dang_nhap(client, main_module, monkeypatch):
    monkeypatch.setattr(main_module.agent, "process_message", fake_ai())
    monkeypatch.setattr(main_module, "find_procedure", fake_procedure(None))
    r = client.post("/api/v1/agent/chat", json={"message": "Điều kiện chuyển nhượng đất?"}, headers={"X-Request-ID": "req-1"})
    assert r.status_code == 200
    body = r.json()
    assert body["data"] == {"answer": "Theo Khoản 1 Điều 45 Luật Đất đai 2024...", "procedure": None, "sources": ["c1", "c2"]}
    assert body["error"] is None
    assert body["request_id"] == "req-1"


def test_chat_kem_thu_tuc(client, main_module, monkeypatch):
    monkeypatch.setattr(main_module.agent, "process_message", fake_ai("Bạn cần chuẩn bị..."))
    monkeypatch.setattr(main_module, "find_procedure", fake_procedure(PROCEDURE))
    r = client.post("/api/v1/agent/chat", json={"message": "đăng ký hộ kinh doanh"})
    assert r.status_code == 200
    assert r.json()["data"]["answer"] == "Bạn cần chuẩn bị..."
    assert r.json()["data"]["procedure"] == PROCEDURE


@pytest.mark.parametrize("error_name,code", [("RAGError", "RAG_SERVICE_ERROR"), ("LLMError", "AI_SERVICE_ERROR")])
def test_ai_loi_khong_co_thu_tuc_tra_503(client, main_module, monkeypatch, error_name, code):
    error_cls = getattr(main_module.rag_client if error_name == "RAGError" else main_module.llm_client, error_name)
    monkeypatch.setattr(main_module.agent, "process_message", fake_ai(error=error_cls("down")))
    monkeypatch.setattr(main_module, "find_procedure", fake_procedure(None))
    r = client.post("/api/v1/agent/chat", json={"message": "xin chào"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == code
    assert r.json()["data"] is None


def test_ai_loi_nhung_co_thu_tuc_van_tra_thu_tuc(client, main_module, monkeypatch):
    monkeypatch.setattr(main_module.agent, "process_message", fake_ai(error=main_module.llm_client.LLMError("down")))
    monkeypatch.setattr(main_module, "find_procedure", fake_procedure(PROCEDURE))
    r = client.post("/api/v1/agent/chat", json={"message": "hộ kinh doanh"})
    assert r.status_code == 200
    assert r.json()["data"] == {"answer": "Đã tìm thấy thủ tục phù hợp.", "procedure": PROCEDURE, "sources": []}


def test_procedure_service_sap_thi_tra_none(main_module, monkeypatch):
    import asyncio
    monkeypatch.setattr(main_module, "procedure_service_url", "http://127.0.0.1:9")
    assert asyncio.run(main_module.find_procedure("hộ kinh doanh", "req-x")) is None


@pytest.mark.parametrize("value", ["", "   ", "x" * 2001])
def test_message_khong_hop_le(client, value):
    r = client.post("/api/v1/agent/chat", json={"message": value})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
