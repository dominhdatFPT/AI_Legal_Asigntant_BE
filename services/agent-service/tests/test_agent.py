"""Test agent.process_message với rag-service và Gemini đều giả lập."""
import asyncio

import pytest

from app.services import agent, llm_client, rag_client


@pytest.fixture
def fake_services(monkeypatch, chunk):
    calls = {}

    async def fake_search(query, top_k=5, request_id=None, transport=None):
        calls["search"] = {"query": query, "top_k": top_k, "request_id": request_id}
        return calls.get("chunks", [chunk])

    async def fake_generate(system_prompt, history, user_content):
        calls["generate"] = {
            "system_prompt": system_prompt, "history": history, "user_content": user_content,
        }
        return "Theo Khoản 1 Điều 188 Luật Đất đai 2024...", 512, "gemini-test"

    monkeypatch.setattr(rag_client, "search", fake_search)
    monkeypatch.setattr(llm_client, "generate", fake_generate)
    return calls


def test_format_chunk_has_full_citation(chunk):
    text = agent.format_chunk(1, chunk)
    assert text.startswith("[1] Khoản 1 Điều 188, Luật Đất đai 2024 (31/2024/QH15)\n")
    assert "Điều 188. Điều kiện" in text


def test_format_chunk_without_section_info():
    text = agent.format_chunk(2, {"content": "Nội dung", "document": {"title": "Nghị định X"}})
    assert text == "[2] Nghị định X\nNội dung"


def test_process_message_uses_rag_context_and_history(fake_services, chunk):
    history = [
        {"role": "user", "content": "Chào bạn"},
        {"role": "assistant", "content": "Chào bạn, mình giúp gì được?"},
    ]

    result = asyncio.run(agent.process_message(
        "Sang tên sổ đỏ cần gì?", history, request_id="req-9",
    ))

    assert fake_services["search"] == {"query": "Sang tên sổ đỏ cần gì?", "top_k": 4, "request_id": "req-9"}
    prompt = fake_services["generate"]["system_prompt"]
    assert "Khoản 1 Điều 188, Luật Đất đai 2024 (31/2024/QH15)" in prompt
    assert "Chỉ dùng thông tin trong các trích đoạn" in prompt
    assert "Câu đầu tiên trả lời thẳng câu hỏi" in prompt
    assert fake_services["generate"]["history"] == history
    assert fake_services["generate"]["user_content"] == "Sang tên sổ đỏ cần gì?"

    assert result == {
        "content": "Theo Khoản 1 Điều 188 Luật Đất đai 2024...",
        "metadata": {
            "rag_chunk_ids": [chunk["id"]],
            "model": "gemini-test",
            "tokens_used": 512,
        },
    }


def test_process_message_without_chunks_tells_model(fake_services):
    fake_services["chunks"] = []

    result = asyncio.run(agent.process_message("Câu hỏi lạ", []))

    assert agent.NO_CONTEXT in fake_services["generate"]["system_prompt"]
    assert result["metadata"]["rag_chunk_ids"] == []


def test_process_message_propagates_rag_error(monkeypatch, fake_services):
    async def broken_search(*args, **kwargs):
        raise rag_client.RAGError("down")

    monkeypatch.setattr(rag_client, "search", broken_search)

    with pytest.raises(rag_client.RAGError):
        asyncio.run(agent.process_message("Hỏi", []))
    assert "generate" not in fake_services  # không gọi Gemini khi RAG lỗi


def test_process_message_propagates_llm_error(monkeypatch, fake_services):
    async def broken_generate(*args, **kwargs):
        raise llm_client.LLMError("503")

    monkeypatch.setattr(llm_client, "generate", broken_generate)

    with pytest.raises(llm_client.LLMError):
        asyncio.run(agent.process_message("Hỏi", []))
