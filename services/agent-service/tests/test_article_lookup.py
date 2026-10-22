"""Tra cứu nguyên văn điều luật: nhận diện câu (article_lookup.parse), gọi rag (rag_client.get_article),
và agent trả nguyên văn mà KHÔNG gọi AI."""
import asyncio
import json

import httpx
import pytest

from app.services import agent, article_lookup, llm_client, rag_client

ARTICLE = {
    "document": {"id": "d1", "title": "Luật Đất đai 2024", "doc_number": "31/2024/QH15", "doc_type": "luat", "status": "active"},
    "chuong": "XIII",
    "dieu": "220",
    "khoan": "2",
    "content": "Điều 220. Tách thửa đất, hợp thửa đất\n2. Trường hợp tách thửa đất thì ...",
    "note": None,
}


@pytest.mark.parametrize(
    "message, expected",
    [
        ("Điều 45 Luật Đất đai", ("Luật Đất đai", "45", None)),
        ("khoản 2 điều 220 luật đất đai 2024 quy định gì", ("luật đất đai 2024", "220", "2")),
        ("cho tôi xem điều 38 luật doanh nghiệp", ("luật doanh nghiệp", "38", None)),
        ("dieu 45 luat dat dai", ("luat dat dai", "45", None)),
        ("Luật đất đai điều 220 khoản 2", ("Luật đất đai", "220", "2")),
        ("Điều 45, khoản 1 Luật Đất đai?", ("Luật Đất đai", "45", "1")),
        ("Điều 45 của Luật Đất đai năm 2024 có nội dung gì", ("Luật Đất đai năm 2024", "45", None)),
        ("Điều 12 31/2024/QH15", ("31/2024/QH15", "12", None)),
        ("nội dung Điều 10A Luật Đầu tư", ("Luật Đầu tư", "10a", None)),
    ],
)
def test_nhan_ra_cau_tra_cuu(message, expected):
    law, dieu, khoan = expected
    assert article_lookup.parse(message) == {"law": law, "dieu": dieu, "khoan": khoan}


@pytest.mark.parametrize(
    "message",
    [
        # Nhắc điều luật nhưng hỏi việc khác: để AI trả lời
        "Theo Điều 45 Luật Đất đai thì tôi có được tặng cho đất không?",
        "Điều 45 Luật Đất đai, tôi muốn tặng cho con",
        "Điều 45 Luật Đất đai và Điều 46 Luật Đất đai",
        "Điều kiện tách thửa theo luật đất đai",
        "luật đất đai quy định thế nào về tách thửa",
        "sang tên sổ đỏ cần gì",
    ],
)
def test_cau_hoi_thuong_khong_phai_tra_cuu(message):
    assert article_lookup.parse(message) is None


def test_dinh_dang_nguyen_van():
    text = article_lookup.format_article(ARTICLE)
    assert text == (
        "Nguyên văn Khoản 2 Điều 220, Luật Đất đai 2024 (31/2024/QH15):\n\n"
        "Điều 220. Tách thửa đất, hợp thửa đất\n2. Trường hợp tách thửa đất thì ..."
    )
    old = {**ARTICLE, "khoan": None, "note": "Kho không có bản năm 2013, đang hiển thị Luật Đất đai 2024.",
           "document": {**ARTICLE["document"], "status": "superseded"}}
    text = article_lookup.format_article(old)
    assert text.startswith("Nguyên văn Điều 220, Luật Đất đai 2024 (31/2024/QH15):")
    assert "Lưu ý: Kho không có bản năm 2013" in text and "không còn hiệu lực" in text


# ── rag_client.get_article ─────────────────────────────────────────────────────

def run_get(handler, **kw):
    return asyncio.run(rag_client.get_article("Luật Đất đai", "220", "2", transport=httpx.MockTransport(handler), **kw))


def test_get_article_gui_dung_hop_dong():
    seen = {}

    def handler(request):
        seen["request"] = request
        return httpx.Response(200, json={"data": ARTICLE, "error": None, "request_id": "req-1"})

    assert run_get(handler, request_id="req-1") == ARTICLE
    request = seen["request"]
    assert str(request.url) == "http://rag-test:8000/internal/v1/rag/articles"
    assert request.headers["X-Internal-Token"] == "test-internal-token"
    assert request.headers["X-Request-ID"] == "req-1"
    assert json.loads(request.read()) == {"law": "Luật Đất đai", "dieu": "220", "khoan": "2"}


@pytest.mark.parametrize("status", [404, 409])
def test_get_article_khong_co_thi_bao_article_not_found(status):
    def handler(request):
        return httpx.Response(status, json={"data": None, "request_id": "r",
                                            "error": {"code": "ARTICLE_NOT_FOUND", "message": "Không có Điều 999."}})

    with pytest.raises(rag_client.ArticleNotFound) as e:
        run_get(handler)
    assert (e.value.code, e.value.message) == ("ARTICLE_NOT_FOUND", "Không có Điều 999.")


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(500, json={}),
    lambda r: httpx.Response(200, json={"data": None}),
    lambda r: (_ for _ in ()).throw(httpx.ConnectTimeout("chậm")),
])
def test_get_article_loi_khac_thi_bao_rag_error(handler):
    with pytest.raises(rag_client.RAGError):
        run_get(handler)


# ── agent.process_message ──────────────────────────────────────────────────────

@pytest.fixture
def calls(monkeypatch):
    calls = {}

    async def fake_get_article(law, dieu, khoan=None, request_id=None, transport=None):
        calls["get_article"] = {"law": law, "dieu": dieu, "khoan": khoan, "request_id": request_id}
        if dieu == "999":
            raise rag_client.ArticleNotFound("ARTICLE_NOT_FOUND", "Luật Đất đai 2024 không có Điều 999 (văn bản này có 260 Điều).")
        return ARTICLE

    async def fake_search(*a, **k):
        calls["search"] = True
        return []

    async def fake_generate(*a, **k):
        calls["generate"] = True
        return "AI trả lời", 10, "gemini-test"

    monkeypatch.setattr(rag_client, "get_article", fake_get_article)
    monkeypatch.setattr(rag_client, "search", fake_search)
    monkeypatch.setattr(llm_client, "generate", fake_generate)
    return calls


def test_tra_cuu_tra_nguyen_van_khong_goi_ai(calls):
    result = asyncio.run(agent.process_message("khoản 2 điều 220 luật đất đai", [], request_id="req-7"))
    assert calls["get_article"] == {"law": "luật đất đai", "dieu": "220", "khoan": "2", "request_id": "req-7"}
    assert "search" not in calls and "generate" not in calls
    assert result["content"].startswith("Nguyên văn Khoản 2 Điều 220, Luật Đất đai 2024 (31/2024/QH15):")
    assert result["metadata"] == {"rag_chunk_ids": [], "model": None, "tokens_used": None, "type": "article_lookup"}


def test_khong_co_dieu_thi_bao_ro_khong_goi_ai(calls):
    result = asyncio.run(agent.process_message("Điều 999 Luật Đất đai", []))
    assert result["content"] == "Luật Đất đai 2024 không có Điều 999 (văn bản này có 260 Điều)."
    assert "generate" not in calls


def test_cau_hoi_co_nhac_dieu_luat_van_di_luong_ai(calls):
    result = asyncio.run(agent.process_message("Theo Điều 45 Luật Đất đai thì tôi có được tặng cho đất không?", []))
    assert "get_article" not in calls and calls["search"] and calls["generate"]
    assert result["content"] == "AI trả lời"
