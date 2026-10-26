"""Chế độ rà soát của agent.process_message (có tài liệu đính kèm): RAG tìm theo nội dung hợp đồng, prompt riêng."""
import asyncio

import pytest

from app.services import agent, contract_review, query_filter, rag_client
from tests.test_agent import doc, fake_services  # noqa: F401  (fixture dùng chung với test_agent)

CONTRACT_TEXT = "HỢP ĐỒNG CHUYỂN NHƯỢNG QUYỀN SỬ DỤNG ĐẤT\nBên A chuyển nhượng cho bên B thửa đất số 12."
ASPECTS = contract_review.REVIEW_QUERIES["chuyen_nhuong_dat"]


def review(question="kiểm tra giúp tôi", text=CONTRACT_TEXT):
    return asyncio.run(agent.process_message(question, [], request_id="req-7", documents=[doc(text)]))


def test_review_uses_review_prompt_not_the_short_answer_prompt(fake_services):
    review()

    prompt = fake_services["generate"]["system_prompt"]
    assert "KHÔNG hỏi lại người dùng" in prompt and "Tóm tắt" in prompt and "Khuyến nghị sửa cụ thể" in prompt
    assert "(chưa có căn cứ trong kho văn bản)" in prompt and "luật sư hoặc công chứng viên" in prompt
    assert "hỏi lại đúng 1 câu" not in prompt and "dưới 200 chữ" not in prompt  # quy tắc của SYSTEM_PROMPT cũ
    # khối chống prompt injection và cảnh báo OCR giữ nguyên
    assert agent.INJECTION_RULE in prompt and agent.OCR_RULE in prompt
    assert "<<<TÀI LIỆU 1 (Word DOCX)>>>" in prompt


def test_review_searches_by_contract_aspects_with_top_k_4(fake_services):
    review()

    searches = fake_services["searches"]
    assert [s["query"] for s in searches] == ASPECTS
    assert all(s["top_k"] == 4 and s["request_id"] == "req-7" for s in searches)


def test_review_searches_run_in_parallel(monkeypatch, fake_services):
    running, peak = 0, 0

    async def slow(query, top_k=5, request_id=None, transport=None):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return []

    monkeypatch.setattr(rag_client, "search", slow)
    review()

    assert peak == len(ASPECTS)


def test_review_puts_specific_question_first(fake_services):
    review(question="Điều khoản đặt cọc có rủi ro không?")

    queries = [s["query"] for s in fake_services["searches"]]
    assert queries == ["Điều khoản đặt cọc có rủi ro không?"] + ASPECTS
    assert len(queries) <= contract_review.MAX_QUERIES


def test_review_passes_review_limits_and_no_retry(fake_services):
    review()

    assert fake_services["generate"]["options"] == {
        "max_output_tokens": 2000, "timeout_seconds": 40.0, "max_retries": 0,
    }


def test_review_metadata_has_mode_and_contract_type(fake_services, chunk):
    result = review()

    assert result["metadata"] == {
        "rag_chunk_ids": [chunk["id"]], "model": "gemini-test", "tokens_used": 512,
        "mode": "contract_review", "contract_type": "chuyen_nhuong_dat",
    }


def test_review_unknown_contract_type_searches_by_document_opening(fake_services):
    result = review(text="BIÊN BẢN HỌP GIA ĐÌNH về việc chia tài sản")

    assert result["metadata"]["contract_type"] == "khac"
    assert [s["query"] for s in fake_services["searches"]] == ["BIÊN BẢN HỌP GIA ĐÌNH về việc chia tài sản"]


def test_review_without_any_query_falls_back_to_the_question(fake_services):
    review(question="kiểm tra giúp tôi", text="   ")

    assert [s["query"] for s in fake_services["searches"]] == ["kiểm tra giúp tôi"]


def test_review_type_comes_from_first_recognised_document(fake_services):
    docs = [doc("Giấy tờ nhà"), doc(CONTRACT_TEXT)]
    result = asyncio.run(agent.process_message("kiểm tra giúp tôi", [], documents=docs))

    assert result["metadata"]["contract_type"] == "chuyen_nhuong_dat"


def test_review_merges_chunks_without_duplicates_and_caps_at_10(monkeypatch, fake_services, chunk):
    def make(n):
        return {**chunk, "id": f"id-{n}", "content": f"Điều {n}"}

    async def many(query, top_k=5, request_id=None, transport=None):
        index = ASPECTS.index(query)
        # câu nào cũng trả đoạn 0 (trùng nhau) và 3 đoạn riêng của nó
        return [make(0)] + [make(10 * (index + 1) + k) for k in range(3)]

    monkeypatch.setattr(rag_client, "search", many)
    result = review()

    ids = result["metadata"]["rag_chunk_ids"]
    assert len(ids) == 10 and len(set(ids)) == 10
    assert ids[0] == "id-0" and ids[1:5] == ["id-10", "id-20", "id-30", "id-40"]  # xen kẽ
    assert fake_services["generate"]["system_prompt"].count("\n[10]") == 1  # đoạn thứ 10 vào prompt, không có đoạn 11


def test_review_skips_a_failed_query(monkeypatch, fake_services, chunk):
    async def flaky(query, top_k=5, request_id=None, transport=None):
        if "công chứng" in query:
            raise rag_client.RAGError("timeout")
        return [{**chunk, "id": query}]

    monkeypatch.setattr(rag_client, "search", flaky)
    result = review()

    assert len(result["metadata"]["rag_chunk_ids"]) == 3 and "generate" in fake_services


def test_review_raises_rag_error_when_every_query_fails(monkeypatch, fake_services):
    async def broken(*args, **kwargs):
        raise rag_client.RAGError("down")

    monkeypatch.setattr(rag_client, "search", broken)

    with pytest.raises(rag_client.RAGError):
        review()
    assert "generate" not in fake_services


def test_review_does_not_swallow_unexpected_errors(monkeypatch, fake_services):
    async def bug(*args, **kwargs):
        raise KeyError("lỗi lập trình")

    monkeypatch.setattr(rag_client, "search", bug)

    with pytest.raises(KeyError):
        review()


def test_short_question_with_documents_is_not_filtered(fake_services):
    assert query_filter.is_meaningless("check")  # không có tài liệu thì câu này bị chặn

    result = review(question="check")

    assert result["content"] != query_filter.DEFAULT_ANSWER
    assert "generate" in fake_services


def test_without_documents_behaviour_is_unchanged(fake_services):
    result = asyncio.run(agent.process_message("Sang tên sổ đỏ cần gì?", []))

    assert len(fake_services["searches"]) == 1 and fake_services["search"]["top_k"] == 4
    assert fake_services["generate"]["options"] == {}  # max tokens, timeout, retry theo cấu hình thường
    prompt = fake_services["generate"]["system_prompt"]
    assert "Câu đầu tiên trả lời thẳng câu hỏi" in prompt and "KHÔNG hỏi lại người dùng" not in prompt
    assert "mode" not in result["metadata"] and "contract_type" not in result["metadata"]
