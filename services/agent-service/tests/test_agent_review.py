"""Chế độ rà soát của agent.process_message (có tài liệu đính kèm): RAG tìm theo nội dung hợp đồng, prompt riêng."""
import asyncio
import logging

import pytest

from app.services import (
    agent, contract_review, document_gate, prompts, query_filter, rag_client, responder,
)
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
    assert prompts.INJECTION_RULE in prompt and prompts.OCR_RULE in prompt
    assert "<<<TÀI LIỆU 1 (Word DOCX)>>>" in prompt


def test_review_searches_by_contract_aspects_with_top_k_3(fake_services):
    review()

    searches = fake_services["searches"]
    assert [s["query"] for s in searches] == ASPECTS
    assert all(s["top_k"] == 3 and s["request_id"] == "req-7" for s in searches)


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

    assert len(fake_services["searches"]) == 1 and fake_services["search"]["top_k"] == 3
    assert fake_services["generate"]["options"] == {}  # max tokens, timeout, retry theo cấu hình thường
    prompt = fake_services["generate"]["system_prompt"]
    assert "Câu đầu tiên trả lời thẳng câu hỏi" in prompt and "KHÔNG hỏi lại người dùng" not in prompt
    assert "mode" not in result["metadata"] and "contract_type" not in result["metadata"]


def test_bypass_review_shows_queries_and_mode(monkeypatch, fake_services):
    from app.core.config import get_settings
    from app.services import llm_client

    async def forbidden(*args, **kwargs):
        raise AssertionError("llm_client.generate không được gọi khi LLM_BYPASS bật")

    monkeypatch.setenv("LLM_BYPASS", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(llm_client, "generate", forbidden)

    result = review()

    meta = result["metadata"]
    assert result["content"].startswith(responder.BYPASS_HEADER)
    assert meta["queries"] == ASPECTS
    assert meta["mode"] == "contract_review" and meta["contract_type"] == "chuyen_nhuong_dat"
    assert meta["model"] == "no-llm" and "KHÔNG hỏi lại người dùng" in meta["system_prompt"]


# ── Dữ kiện tự kiểm tra đưa vào prompt rà soát (DOCUMENT_FACTS_ENABLED) ──────────

BLANK_TEXT = CONTRACT_TEXT + "\n" + "Họ và tên người bán: ........\n" * 10
FACTS = [
    "Tài liệu có 10 chỗ để trống, nhiều khả năng là mẫu chưa điền.",
    "Hệ thống chỉ đọc 100 trên 200 ký tự của tài liệu, phần còn lại không được xem xét.",
]


def previous_format_documents(documents):
    """format_documents trước khi tách clip_document ra dùng chung: kết quả mới phải y hệt."""
    limit = prompts.MAX_DOCUMENT_CHARS // len(documents)
    blocks = []
    for i, d in enumerate(documents, start=1):
        text = d["text"].strip()
        if len(text) > limit:
            text = f"{text[:limit].rstrip()}\n{prompts.TRUNCATED_MARK}"
        label = prompts.SOURCE_KIND_LABELS.get(d["source_kind"], d["source_kind"])
        blocks.append(f"<<<TÀI LIỆU {i} ({label})>>>\n{text or prompts.EMPTY_DOCUMENT}\n<<<HẾT TÀI LIỆU {i}>>>")
    return "\n\n".join(blocks)


def previous_review_prompt(chunks, documents):
    context = prompts._context(chunks) or prompts.NO_CONTEXT
    return prompts.REVIEW_SYSTEM_PROMPT.format(context=context) + prompts.REVIEW_DOCUMENT_PROMPT.replace(
        "{documents}", previous_format_documents(documents),
    )


def set_facts(monkeypatch, on=True):
    from app.core.config import get_settings

    monkeypatch.setenv("DOCUMENT_FACTS_ENABLED", "true" if on else "false")
    get_settings.cache_clear()


@pytest.mark.parametrize("documents", [
    [doc("ngắn")],
    [doc(""), doc("  x  ", "image")],
    [doc("a" * 20000)],
    [doc("abcd " * 5000), doc("b" * 9000, "pdf_scan"), doc("c" * 100, "image")],
    [doc("giá {x} và {documents}"), doc("Điều khoản " * 900 + "   \n\n" + "y" * 5000, "pdf_text")],
])
def test_format_documents_is_unchanged_by_clip_document(documents):
    assert prompts.format_documents(documents) == previous_format_documents(documents)


def test_clip_document_keeps_limit_divided_by_count():
    assert prompts.clip_document("a" * 100, 1) == ("a" * 100, False)
    assert prompts.clip_document("a" * 13000, 1) == ("a" * 12000, True)
    assert prompts.clip_document("a" * 13000, 3) == ("a" * 4000, True)
    assert prompts.clip_document(("ab " * 8000).strip(), 1) == ("ab " * 3999 + "ab", True)  # cắt đúng chỗ dấu cách, rstrip


@pytest.mark.parametrize("facts", ["omitted", None, []])
def test_build_review_prompt_without_facts_is_identical_to_before(chunk, facts):
    documents = [doc(BLANK_TEXT), doc("a" * 9000)]
    if facts == "omitted":
        prompt = prompts.build_review_prompt([chunk], documents)
    else:
        prompt = prompts.build_review_prompt([chunk], documents, facts=facts)

    assert prompt == previous_review_prompt([chunk], documents)
    assert prompts.REVIEW_FACTS_HEADER not in prompt


def test_build_review_prompt_puts_facts_block_just_before_documents(chunk):
    documents = [doc(BLANK_TEXT)]
    base = previous_review_prompt([chunk], documents)

    prompt = prompts.build_review_prompt([chunk], documents, facts=FACTS)

    block = "\n\n" + prompts.REVIEW_FACTS_HEADER + "\n- " + "\n- ".join(FACTS)
    assert prompt.replace(block, "", 1) == base  # chỉ chèn thêm khối, phần còn lại y hệt
    assert prompts.format_chunk(1, chunk) in prompt.split(prompts.REVIEW_FACTS_HEADER)[0]
    assert prompt.index(prompts.REVIEW_FACTS_HEADER) < prompt.index("Tài liệu người dùng cung cấp") < prompt.index("<<<TÀI LIỆU 1")
    assert "đã đếm bằng quy tắc" in prompts.REVIEW_FACTS_HEADER and "không tự đếm lại" in prompts.REVIEW_FACTS_HEADER
    assert all(f"- {fact}" in prompt for fact in FACTS)


def test_build_review_prompt_with_braces_in_facts_and_document(chunk):
    prompt = prompts.build_review_prompt([chunk], [doc("giá {x} và {documents}")], facts=["Có {x} và {documents} chỗ"])

    assert "- Có {x} và {documents} chỗ" in prompt and "giá {x} và {documents}" in prompt
    assert prompt.count("{documents}") == 2  # một trong dữ kiện, một trong chữ tài liệu; không bị thay


def test_review_facts_off_by_default_keeps_prompt_and_skips_facts(fake_services, chunk, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("document_gate.facts không được gọi khi cờ tắt")

    monkeypatch.setattr(document_gate, "facts", forbidden)

    result = review(text=BLANK_TEXT)

    prompt = fake_services["generate"]["system_prompt"]
    assert prompt == previous_review_prompt([chunk], [doc(BLANK_TEXT)])
    assert prompts.REVIEW_FACTS_HEADER not in prompt and result["metadata"]["mode"] == "contract_review"


def test_review_facts_explicitly_off_skips_facts(fake_services, monkeypatch):
    set_facts(monkeypatch, on=False)
    monkeypatch.setattr(document_gate, "facts", lambda *a, **k: pytest.fail("không được gọi khi cờ tắt"))

    review(text=BLANK_TEXT)

    assert prompts.REVIEW_FACTS_HEADER not in fake_services["generate"]["system_prompt"]


def test_review_facts_on_puts_checked_facts_before_documents_in_prompt(fake_services, monkeypatch):
    set_facts(monkeypatch)

    review(text=BLANK_TEXT)

    prompt = fake_services["generate"]["system_prompt"]
    assert prompts.REVIEW_FACTS_HEADER in prompt
    assert "- Tài liệu có 10 chỗ để trống, nhiều khả năng là mẫu chưa điền." in prompt
    assert prompt.index(prompts.REVIEW_FACTS_HEADER) < prompt.index("<<<TÀI LIỆU 1")


def test_review_facts_on_tells_how_much_of_a_long_document_was_read(fake_services, monkeypatch):
    set_facts(monkeypatch)

    review(text="a" * 15000)

    prompt = fake_services["generate"]["system_prompt"]
    assert "- Hệ thống chỉ đọc 12000 trên 15000 ký tự của tài liệu, phần còn lại không được xem xét." in prompt
    assert prompts.TRUNCATED_MARK in prompt  # cách cắt không đổi


def test_review_facts_on_with_nothing_to_report_keeps_prompt_unchanged(fake_services, chunk, monkeypatch):
    set_facts(monkeypatch)

    review()

    assert fake_services["generate"]["system_prompt"] == previous_review_prompt([chunk], [doc(CONTRACT_TEXT)])


def test_review_facts_gets_documents_and_request_id(fake_services, monkeypatch):
    set_facts(monkeypatch)
    seen = []

    def spy(documents, request_id=None):
        seen.append((documents, request_id))
        return ["Câu thử."]

    monkeypatch.setattr(document_gate, "facts", spy)

    review()

    assert seen == [([doc(CONTRACT_TEXT)], "req-7")]
    assert "- Câu thử." in fake_services["generate"]["system_prompt"]


def test_review_facts_error_still_calls_ai_as_before(fake_services, chunk, monkeypatch, caplog):
    set_facts(monkeypatch)

    def broken(*args, **kwargs):
        raise RuntimeError("lỗi bất ngờ")

    monkeypatch.setattr(document_gate, "facts", broken)
    caplog.set_level(logging.WARNING, logger="app.services.review_agent")

    result = review(text=BLANK_TEXT)

    assert fake_services["generate"]["system_prompt"] == previous_review_prompt([chunk], [doc(BLANK_TEXT)])
    assert result["content"] == "Theo Khoản 1 Điều 188 Luật Đất đai 2024..."
    warning = next(r for r in caplog.records if r.levelno == logging.WARNING and "dữ kiện" in r.getMessage())
    assert "request_id=req-7" in warning.getMessage()


def test_review_facts_work_together_with_document_gate(fake_services, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("DOCUMENT_GATE_ENABLED", "true")
    set_facts(monkeypatch)
    assert get_settings().document_gate_enabled and get_settings().document_facts_enabled

    result = review(text=BLANK_TEXT)

    assert result["metadata"]["mode"] == "contract_review"
    assert "Tài liệu có 10 chỗ để trống" in fake_services["generate"]["system_prompt"]


# ── Hỏi lại loại hợp đồng (DOCUMENT_ASK_TYPE_ENABLED) ────────────────────────────

UNKNOWN_TEXT = "BIÊN BẢN HỌP GIA ĐÌNH về việc chia tài sản"
NEED_TYPE_METADATA = {
    "rag_chunk_ids": [], "model": None, "tokens_used": None,
    "type": "document_need_type", "mode": "contract_review", "contract_type": "khac",
}


def set_ask_type(monkeypatch, on=True):
    from app.core.config import get_settings

    monkeypatch.setenv("DOCUMENT_ASK_TYPE_ENABLED", "true" if on else "false")
    get_settings.cache_clear()


def forbid_rag_and_llm(monkeypatch):
    from app.services import llm_client

    async def forbidden(*args, **kwargs):
        raise AssertionError("rag_client.search / llm_client.generate không được gọi khi hỏi lại loại hợp đồng")

    monkeypatch.setattr(rag_client, "search", forbidden)
    monkeypatch.setattr(llm_client, "generate", forbidden)


def test_ask_type_off_by_default_reviews_unknown_type_as_before(fake_services, chunk, monkeypatch):
    monkeypatch.setattr(document_gate, "ask_contract_type", lambda *a, **k: pytest.fail("không được gọi khi cờ tắt"))

    result = review(question="kiểm tra giúp tôi", text=UNKNOWN_TEXT)

    assert result["metadata"] == {
        "rag_chunk_ids": [chunk["id"]], "model": "gemini-test", "tokens_used": 512,
        "mode": "contract_review", "contract_type": "khac",
    }
    assert "generate" in fake_services and [s["query"] for s in fake_services["searches"]] == [UNKNOWN_TEXT]


def test_ask_type_explicitly_off_reviews_as_before(fake_services, monkeypatch):
    set_ask_type(monkeypatch, on=False)

    result = review(question="kiểm tra giúp tôi", text=UNKNOWN_TEXT)

    assert "type" not in result["metadata"] and "generate" in fake_services


def test_ask_type_on_unknown_type_generic_question_asks_back_without_rag_or_llm(fake_services, monkeypatch):
    set_ask_type(monkeypatch)
    forbid_rag_and_llm(monkeypatch)

    result = review(question="kiểm tra giúp tôi", text=UNKNOWN_TEXT)

    assert result == {"content": document_gate.ASK_TYPE_ANSWER, "metadata": NEED_TYPE_METADATA}
    assert "searches" not in fake_services and "generate" not in fake_services


def test_ask_type_on_unknown_type_specific_question_reviews_as_before(fake_services, monkeypatch):
    set_ask_type(monkeypatch)

    result = review(question="điều khoản phạt cọc có hợp lý không", text=UNKNOWN_TEXT)

    assert result["metadata"]["mode"] == "contract_review" and "type" not in result["metadata"]
    assert [s["query"] for s in fake_services["searches"]] == ["điều khoản phạt cọc có hợp lý không", UNKNOWN_TEXT]
    assert "generate" in fake_services


def test_ask_type_on_recognised_type_reviews_as_before(fake_services, monkeypatch):
    set_ask_type(monkeypatch)

    result = review(question="kiểm tra giúp tôi")

    assert result["metadata"]["contract_type"] == "chuyen_nhuong_dat" and "type" not in result["metadata"]
    assert [s["query"] for s in fake_services["searches"]] == ASPECTS and "generate" in fake_services


def test_ask_type_error_reviews_as_before(fake_services, monkeypatch, caplog):
    set_ask_type(monkeypatch)

    def broken(*args, **kwargs):
        raise RuntimeError("lỗi bất ngờ")

    monkeypatch.setattr(document_gate, "ask_contract_type", broken)
    caplog.set_level(logging.WARNING, logger="app.services.review_agent")

    result = review(question="kiểm tra giúp tôi", text=UNKNOWN_TEXT)

    assert result["content"] == "Theo Khoản 1 Điều 188 Luật Đất đai 2024..." and "type" not in result["metadata"]
    warning = next(r for r in caplog.records if r.levelno == logging.WARNING and "loại hợp đồng" in r.getMessage())
    assert "request_id=req-7" in warning.getMessage()


def test_ask_type_skipped_when_llm_bypass_on(fake_services, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("LLM_BYPASS", "true")
    set_ask_type(monkeypatch)  # làm sạch cache cấu hình, giữ nguyên bypass
    assert get_settings().llm_bypass and get_settings().document_ask_type_enabled
    monkeypatch.setattr(document_gate, "ask_contract_type", lambda *a, **k: pytest.fail("không được gọi khi LLM_BYPASS bật"))

    result = review(question="kiểm tra giúp tôi", text=UNKNOWN_TEXT)

    assert result["metadata"]["llm_bypass"] is True


def test_ask_type_passes_type_question_and_request_id(fake_services, monkeypatch):
    set_ask_type(monkeypatch)
    seen = []

    def spy(contract_type, user_content, request_id=None):
        seen.append((contract_type, user_content, request_id))

    monkeypatch.setattr(document_gate, "ask_contract_type", spy)

    review(question="kiểm tra giúp tôi", text=UNKNOWN_TEXT)

    assert seen == [("khac", "kiểm tra giúp tôi", "req-7")]


def test_ask_type_next_turn_with_the_type_goes_to_normal_review(fake_services, monkeypatch):
    set_ask_type(monkeypatch)
    documents = [doc(UNKNOWN_TEXT)]

    first = asyncio.run(agent.process_message("kiểm tra giúp tôi", [], documents=documents))
    assert first["metadata"]["type"] == "document_need_type" and "searches" not in fake_services

    history = [{"role": "user", "content": "kiểm tra giúp tôi"}, {"role": "assistant", "content": first["content"]}]
    second = asyncio.run(agent.process_message("đây là hợp đồng thuê nhà", history, documents=documents))

    assert second["metadata"]["mode"] == "contract_review" and "type" not in second["metadata"]
    assert fake_services["searches"][0]["query"] == "đây là hợp đồng thuê nhà"  # câu trả lời của người dùng vào câu tìm luật
    assert "generate" in fake_services and fake_services["generate"]["history"] == history


# ── Mục thường gặp của hợp đồng kiểm bằng từ khoá (DOCUMENT_CHECKLIST_ENABLED) ───

NOT_SEEN_PREFIX = "Kiểm bằng từ khoá, chưa thấy nhắc tới:"


def set_checklist(monkeypatch, on=True):
    from app.core.config import get_settings

    monkeypatch.setenv("DOCUMENT_CHECKLIST_ENABLED", "true" if on else "false")
    get_settings.cache_clear()


def test_checklist_only_flag_puts_not_seen_sentence_in_prompt(fake_services, monkeypatch):
    set_checklist(monkeypatch)  # DOCUMENT_FACTS_ENABLED vẫn tắt

    review()  # CONTRACT_TEXT chỉ có tiêu đề và hai bên: thiếu nhiều mục

    prompt = fake_services["generate"]["system_prompt"]
    assert prompts.REVIEW_FACTS_HEADER in prompt
    assert f"- {NOT_SEEN_PREFIX}" in prompt and "Giải quyết tranh chấp" in prompt
    assert "hãy đọc lại tài liệu để xác nhận trước khi kết luận là thiếu" in prompt
    assert prompt.index(prompts.REVIEW_FACTS_HEADER) < prompt.index("<<<TÀI LIỆU 1")


def test_checklist_only_flag_calls_facts_with_include_checklist(fake_services, monkeypatch):
    set_checklist(monkeypatch)
    seen = []

    def spy(documents, request_id=None, include_checklist=False):
        seen.append((request_id, include_checklist))
        return []

    monkeypatch.setattr(document_gate, "facts", spy)

    review()

    assert seen == [("req-7", True)]


def test_facts_only_flag_calls_facts_as_in_a2_and_adds_no_checklist(fake_services, monkeypatch):
    set_facts(monkeypatch)
    seen = []

    def spy(documents, request_id=None):  # chữ ký của việc A2: không nhận include_checklist
        seen.append(request_id)
        return []

    monkeypatch.setattr(document_gate, "facts", spy)

    review()

    assert seen == ["req-7"]


def test_facts_only_flag_prompt_has_no_checklist_sentence(fake_services, monkeypatch):
    set_facts(monkeypatch)

    review(text=BLANK_TEXT)

    prompt = fake_services["generate"]["system_prompt"]
    assert "Tài liệu có 10 chỗ để trống" in prompt and NOT_SEEN_PREFIX not in prompt


def test_both_checklist_and_facts_flags_off_never_call_facts(fake_services, chunk, monkeypatch):
    set_facts(monkeypatch, on=False)
    set_checklist(monkeypatch, on=False)
    monkeypatch.setattr(document_gate, "facts", lambda *a, **k: pytest.fail("không được gọi khi cả hai cờ tắt"))

    review(text=BLANK_TEXT)

    assert fake_services["generate"]["system_prompt"] == previous_review_prompt([chunk], [doc(BLANK_TEXT)])


def test_checklist_error_still_calls_ai_as_before(fake_services, chunk, monkeypatch, caplog):
    set_checklist(monkeypatch)

    def broken(*args, **kwargs):
        raise RuntimeError("lỗi bất ngờ")

    monkeypatch.setattr(document_gate, "facts", broken)
    caplog.set_level(logging.WARNING, logger="app.services.review_agent")

    result = review()

    assert fake_services["generate"]["system_prompt"] == previous_review_prompt([chunk], [doc(CONTRACT_TEXT)])
    assert result["content"] == "Theo Khoản 1 Điều 188 Luật Đất đai 2024..."
    assert any(r.levelno == logging.WARNING and "request_id=req-7" in r.getMessage() for r in caplog.records)


def test_checklist_unfilled_template_has_no_not_seen_sentence_in_prompt(fake_services, monkeypatch):
    set_checklist(monkeypatch)

    review(text=BLANK_TEXT)

    prompt = fake_services["generate"]["system_prompt"]
    assert "Tài liệu có 10 chỗ để trống" in prompt and NOT_SEEN_PREFIX not in prompt
