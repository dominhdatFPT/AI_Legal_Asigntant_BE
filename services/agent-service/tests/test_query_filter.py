import asyncio

import pytest

from app.services import agent, llm_client, query_filter, rag_client


@pytest.mark.parametrize("text", [
    "dsadsafsaf", "fdavfsva", "asdf qwer", "jkjkjk", "aaaaaaa", "!!!???", "hdhd sdfs",
])
def test_cau_vo_nghia(text):
    assert query_filter.is_meaningless(text)


@pytest.mark.parametrize("text", [
    "Sang tên sổ đỏ cần những giấy tờ gì?",
    "mua đất thì cần làm những thủ tục gì",
    "mua dat can giay to gi",  # gõ không dấu
    "Điều 188 Luật Đất đai 2024 nói gì",
    "Làm CCCD ở đâu",
    "nghiêng khuỷu tay quyền",
    "đăng ký hộ kinh doanh online",  # lẫn 1 từ tiếng Anh vẫn hiểu
])
def test_cau_co_nghia(text):
    assert not query_filter.is_meaningless(text)


def test_agent_tra_loi_mac_dinh_khong_goi_rag_va_llm(monkeypatch):
    async def must_not_call(*args, **kwargs):
        raise AssertionError("không được gọi RAG/LLM với câu vô nghĩa")

    monkeypatch.setattr(rag_client, "search", must_not_call)
    monkeypatch.setattr(llm_client, "generate", must_not_call)

    result = asyncio.run(agent.process_message("dsadsafsaf", []))

    assert result == {
        "content": query_filter.DEFAULT_ANSWER,
        "metadata": {"rag_chunk_ids": [], "model": None, "tokens_used": None},
    }
