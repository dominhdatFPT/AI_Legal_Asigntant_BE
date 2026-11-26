"""Phase 2A: sổ cái $0.01 toàn câu hỏi (nano-USD), cổng admission trước RAG/LLM, giữ chỗ + quyết toán embedding."""
import asyncio
import json
import uuid
from types import SimpleNamespace as NS

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main
from app.api.deps import get_current_user
from app.api.v1 import conversations
from app.core.auth import TokenPayload
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.main import app
from app.repositories import conversation as repo
from app.services import admission, agent, case_budget, llm_client, quota, rag_client
from app.services.case_budget import CaseBudget

CAP = 10_000_000  # $0.01 = 10_000_000 nano-USD
MODEL = "deepseek/deepseek-v4.1-flash"
ROUTE = ("openrouter", MODEL)
USER_A = "11111111-1111-4111-8111-111111111111"
BASE = "/api/v1/agent/conversations"
SIMPLE_Q = "Thủ tục sang tên sổ đỏ cần những giấy tờ gì?"

CAU_1 = (
    "Câu 1: Ngày 12/03/2025, ông Nguyễn Văn An (bên nhận cọc) ký hợp đồng đặt cọc với bà Trần Thị Bình (bên đặt cọc) để chuyển nhượng "
    "thửa đất diện tích 1.200 m² tại xã Tân Phú, huyện Đức Hòa, tỉnh Long An. Thửa đất gồm 300 m² đất ở và 900 m² đất trồng cây lâu năm, "
    "đã có giấy chứng nhận quyền sử dụng đất đứng tên một mình ông An. Giá chuyển nhượng thỏa thuận là 6 tỷ đồng; bà Bình đã đặt cọc "
    "3 tỷ đồng bằng chuyển khoản. Hợp đồng đặt cọc ghi rõ: ông An cam kết làm thủ tục chuyển mục đích sử dụng đất trồng cây lâu năm "
    "sang đất ở trước ngày 30/06/2025, hai bên sẽ ký hợp đồng chuyển nhượng tại văn phòng công chứng trong vòng 15 ngày kể từ khi "
    "hoàn tất việc chuyển mục đích; bên nào vi phạm thì mất cọc hoặc phải trả lại cọc và chịu phạt một khoản tiền tương đương số tiền "
    "đã nhận cọc. Đến hạn, cơ quan có thẩm quyền chưa giải quyết hồ sơ vì quy hoạch địa phương đang được điều chỉnh, ông An cho rằng "
    "đây là trở ngại khách quan nên không có lỗi. Trong thời gian đó, ngày 20/07/2025, ông An lại nhận thêm 2 tỷ đồng tiền đặt cọc "
    "của ông Lê Văn Cường cho chính thửa đất này mà không báo cho bà Bình. Bà Bình phát hiện sự việc vào tháng 09/2025, yêu cầu ông An "
    "trả lại 3 tỷ đồng cùng tiền phạt cọc nhưng ông An từ chối và cho rằng thời hiệu khởi kiện đã hết.\n"
    "Anh/chị hãy cho biết:\n"
    "1) Hợp đồng đặt cọc nói trên có hiệu lực không, khi cam kết chuyển mục đích sử dụng đất phụ thuộc vào quyết định của cơ quan nhà nước?\n"
    "2) Việc ông An không chuyển được mục đích sử dụng đất do quy hoạch thay đổi có được xem là trở ngại khách quan hay là lỗi, và hậu quả "
    "pháp lý đối với khoản cọc 3 tỷ đồng ra sao?\n"
    "3) Việc ông An nhận cọc lần hai của ông Cường ảnh hưởng thế nào đến quyền của bà Bình, và bà Bình được yêu cầu bồi thường, trả lãi "
    "trên khoản tiền nào?\n"
    "4) Thời hiệu khởi kiện yêu cầu trả lại tiền cọc và phạt cọc được tính như thế nào, bà Bình cần chuẩn bị chứng cứ gì?"
)
NINE_QUESTIONS = [
    "Hợp đồng thuê nhà ở có bắt buộc phải công chứng không?",
    "Điều kiện đăng ký kết hôn với người nước ngoài là gì?",
    "Ai được quyền nuôi con khi hai bên thuận tình ly hôn?",
    "Người thừa kế theo pháp luật được chia di sản theo hàng nào?",
    "Mức đóng bảo hiểm xã hội tự nguyện hằng tháng tính ra sao?",
    "Sang tên xe máy đã bán cần nộp lệ phí trước bạ bao nhiêu?",
    "Xây nhà cấp bốn trên đất vườn có phải xin giấy phép không?",
    "Khai sinh quá hạn một năm phải làm thủ tục tại cơ quan nào?",
    "Công dân bị hoãn nghĩa vụ quân sự vì lý do học tập được bao lâu?",
]
NINE_ISSUES = "\n".join(f"Câu {i}: {q}" for i, q in enumerate(NINE_QUESTIONS, 1))
OVER_FLOOR = CAU_1 + "\n" + "Tình tiết bổ sung về thửa đất và các khoản tiền đã giao nhận giữa hai bên. " * 220  # < 20.000 ký tự, 4 nhóm vấn đề

pytestmark = pytest.mark.usefixtures("paid")


@pytest.fixture
def paid(monkeypatch):
    """Cấu hình gần production: OpenRouter + route đã xác minh giá, trần $0.01 BẬT, cap đầu ra theo mục tiêu Phase 2B."""
    for key, value in {"CASE_COST_BUDGET_MICRO_USD": "10000", "LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "k",
                       "OPENROUTER_MODEL": "", "OPENROUTER_AUX_MODEL": "", "ADAPTIVE_RAG_ENABLED": "true",
                       "PLANNER_MAX_OUTPUT_TOKENS": "896", "CASE_COMPLEX_MAX_OUTPUT_TOKENS": "3500",
                       "CASE_MAX_OUTPUT_TOKENS": "3500"}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def env(monkeypatch, **values):
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: TokenPayload(
        sub=USER_A, role="citizen", type="access", iss="legal-assistant-user-service", iat=0, exp=9999999999)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def spies(monkeypatch):
    """Mọi đường tốn tiền hoặc ghi dữ liệu đều đếm; nếu bị gọi thì nổ để lỗi hiện rõ."""
    calls = {"rag": 0, "embedding": 0, "llm": 0, "save": 0, "quota_add": 0}

    async def rag_post(path, payload, request_id, transport):
        calls["rag"] += 1
        calls["embedding"] += bool(payload.get("embedding_token_budget"))
        raise AssertionError("rag-service must not be called")

    def llm():
        calls["llm"] += 1
        raise AssertionError("LLM provider must not be called")

    def save(*args, **kwargs):
        calls["save"] += 1
        raise AssertionError("no message pair may be saved")

    def add(*args, **kwargs):
        calls["quota_add"] += 1

    monkeypatch.setattr(rag_client, "_post", rag_post)
    for name in ("_get_openrouter_client", "_get_gemini_client", "_get_groq_client"):
        monkeypatch.setattr(llm_client, name, llm)
    monkeypatch.setattr(conversations, "_save_pair", save)
    monkeypatch.setattr(quota, "add_daily_tokens", add)
    return calls


def new_conversation(client):
    return client.post(BASE, json={}).json()["data"]["id"]


# ── 1. Biên nano-USD: ngay dưới, đúng và vượt 1 nano trần, gồm LLM + embedding ────────────────────────────────────────

LLM_PROMPT = 32_821                       # 300 * 32.821 = 9.846.300 nano
LLM_COST = LLM_PROMPT * 300
EMBEDDING_COST = 1024 * 150               # 153.600 nano -> tổng 9.999.900


def ledger_with_llm_and_embedding(cap):
    budget = CaseBudget(max_cost_nano_usd=cap, responder_reserved_output=0)
    assert budget.reserve("planner", *ROUTE, LLM_PROMPT, 0)
    return budget, budget.reserve_embedding(1024)


def test_boundary_just_below_cap_fits_llm_and_embedding():
    budget, embedding = ledger_with_llm_and_embedding(CAP)
    assert embedding.tokens == 1024
    assert budget.ledger_total_nano == LLM_COST + EMBEDDING_COST == 9_999_900 < CAP


def test_boundary_exactly_at_cap_is_accepted_and_nothing_more_fits():
    budget, embedding = ledger_with_llm_and_embedding(LLM_COST + EMBEDDING_COST)
    assert embedding.tokens == 1024 and budget.ledger_total_nano == budget.max_cost_nano_usd
    assert budget.reserve_embedding(1) is None
    assert budget.reserve("planner", *ROUTE, 1, 0) is None


def test_boundary_one_nano_over_cap_is_clamped_never_exceeded():
    budget, embedding = ledger_with_llm_and_embedding(LLM_COST + EMBEDDING_COST - 1)
    assert embedding.tokens == 1023  # 150 nano/token: không cấp phần vượt trần dù chỉ 1 nano
    assert budget.ledger_total_nano <= budget.max_cost_nano_usd
    other = CaseBudget(max_cost_nano_usd=LLM_COST - 1, responder_reserved_output=0)
    assert other.reserve("planner", *ROUTE, LLM_PROMPT, 0) is None


@pytest.mark.parametrize("actual_usd,blocks", [("0.009999999", False), ("0.010000000", True), ("0.010000001", True)])
def test_settled_cost_at_and_over_cap_blocks_further_spend(actual_usd, blocks):
    budget = CaseBudget(max_cost_nano_usd=CAP, responder_reserved_output=0)
    res = budget.reserve("planner", *ROUTE, 100, 10)
    budget.settle(res, usage_breakdown={"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110, "cost_usd": actual_usd})
    assert budget.exceeded is blocks
    assert budget.reserve_embedding(1) is None  # còn tối đa 1 nano: không đủ cho một token embedding (150 nano)
    assert (budget.reserve("planner", *ROUTE, 1, 0) is None) is True  # 300 nano cho 1 token input: cũng không còn chỗ


def test_embedding_total_cap_is_1024_tokens_per_request():
    budget = CaseBudget(max_cost_nano_usd=CAP, responder_reserved_output=0)
    first = budget.reserve_embedding(5000)
    assert first.tokens == 1024 and first.cost_nano == EMBEDDING_COST
    assert budget.reserve_embedding(1) is None


def test_responder_floor_is_held_from_the_start_for_input_and_output():
    budget = CaseBudget(max_cost_nano_usd=CAP, responder_reserved_input=8000, responder_reserved_output=3500)
    hold = 8000 * 300 + 3500 * 1200
    assert budget.reserved_for_responder_cost_nano == hold
    # planner không được ăn vào phần responder: còn đúng CAP - hold
    assert budget.reserve("planner", *ROUTE, (CAP - hold) // 300 + 1, 0) is None
    assert budget.reserve("planner", *ROUTE, (CAP - hold) // 300, 0)
    assert budget.reserve_responder_floor(10**6, 10**6) is False


# ── 2. Quyết toán embedding ───────────────────────────────────────────────────────────────────────────────────────────

USAGE = {"prompt_tokens": 400, "cached_tokens": 0, "attempts": 1, "cost_nano_usd": 60_000, "budget_capped": False,
         "skipped_reason": None, "model": "gemini-embedding-001", "request_turn_id": "t"}


def test_settle_embedding_uses_actual_usage_and_releases_the_rest():
    budget = CaseBudget(max_cost_nano_usd=CAP)
    res = budget.reserve_embedding(1024)
    budget.settle_embedding(res, USAGE)
    assert (budget.confirmed_cost_nano_usd, budget.in_flight_cost_nano_usd, budget.unsettled_cost_nano_usd) == (60_000, 0, 0)
    assert budget.confirmed_embedding_tokens == 400
    budget.settle_embedding(res, USAGE)  # quyết toán đúng một lần
    assert budget.confirmed_cost_nano_usd == 60_000


@pytest.mark.parametrize("usage", [None, {}, {"skipped_reason": None}])
def test_missing_embedding_usage_keeps_full_reservation_unsettled(usage):
    budget = CaseBudget(max_cost_nano_usd=CAP)
    res = budget.reserve_embedding(1024)
    budget.settle_embedding(res, usage)
    assert budget.unsettled_cost_nano_usd == EMBEDDING_COST and budget.confirmed_cost_nano_usd == 0
    assert budget.ledger_total_nano == EMBEDDING_COST
    assert budget.reserve_embedding(1) is None  # phần unsettled vẫn chiếm trần embedding


def search_with(budget, handler, query="Điều kiện sang tên sổ đỏ", **kwargs):
    return asyncio.run(rag_client.search(query, budget=budget, transport=httpx.MockTransport(handler), **kwargs))


def ok_handler(seen, usage):
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"data": {"chunks": [{"id": "c1", "content": "x"}], "embedding_usage": usage}})
    return handler


def test_rag_search_reserves_before_call_and_settles_embedding_usage():
    budget, seen = CaseBudget(max_cost_nano_usd=CAP), []
    result = search_with(budget, ok_handler(seen, USAGE), embedding_tokens=400)
    payload = seen[0]
    assert 0 < payload["embedding_token_budget"] <= 1024 and payload["request_turn_id"].startswith(budget.request_turn_id)
    assert result == [{"id": "c1", "content": "x"}] and result.embedding_usage == USAGE
    assert budget.confirmed_cost_nano_usd == 60_000 and budget.in_flight_cost_nano_usd == 0
    assert budget.ledger[-1]["stage"] == "embedding" and budget.ledger[-1]["confirmed_cost_nano_usd"] == 60_000


def test_rag_search_forwards_exact_refs_and_expansion_flags():
    seen = []
    refs = [{"doc_ref": "BLDS", "article": "328", "clause": None, "point": None, "title": None}]
    search_with(CaseBudget(max_cost_nano_usd=CAP), ok_handler(seen, USAGE), exact_refs=refs,
                expand_adjacent_clauses=True, follow_citations=True)
    assert seen[0]["exact_refs"] == refs and seen[0]["expand_adjacent_clauses"] is True and seen[0]["follow_citations"] is True


def test_rag_response_without_usage_stays_unsettled():
    budget, seen = CaseBudget(max_cost_nano_usd=CAP), []
    search_with(budget, ok_handler(seen, None))
    assert budget.unsettled_cost_nano_usd == seen[0]["embedding_token_budget"] * 150 > 0
    assert budget.confirmed_cost_nano_usd == 0


@pytest.mark.parametrize("failure", ["timeout", "network", "http500"])
def test_rag_timeout_network_and_5xx_keep_reservation_unsettled(failure):
    budget = CaseBudget(max_cost_nano_usd=CAP)

    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        if failure == "network":
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(500, json={})

    with pytest.raises(rag_client.RAGError):
        search_with(budget, handler)
    assert budget.unsettled_cost_nano_usd > 0 and budget.in_flight_cost_nano_usd == 0
    assert budget.ledger_total_nano == budget.unsettled_cost_nano_usd  # tiền đã gửi mà chưa có usage vẫn tính vào trần


def test_rag_4xx_rejected_before_processing_releases_the_reservation():
    budget = CaseBudget(max_cost_nano_usd=CAP)
    with pytest.raises(rag_client.RAGError):
        search_with(budget, lambda request: httpx.Response(422, json={}))
    assert budget.ledger_total_nano == 0


def test_exhausted_embedding_budget_sends_zero_and_stays_lexical_only():
    budget, seen = CaseBudget(max_cost_nano_usd=CAP, embedding_cap_tokens=0), []
    search_with(budget, ok_handler(seen, {**USAGE, "prompt_tokens": 0, "cost_nano_usd": 0, "skipped_reason": "budget_zero"}))
    assert seen[0]["embedding_token_budget"] == 0 and budget.ledger_total_nano == 0


def test_search_without_budget_keeps_legacy_payload_and_list_shape(chunk):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"data": {"chunks": [chunk]}})

    result = asyncio.run(rag_client.search("sang tên sổ đỏ", transport=httpx.MockTransport(handler)))
    assert seen[0] == {"query": "sang tên sổ đỏ", "top_k": 5} and result == [chunk] and result.embedding_usage is None


# ── 3. Trần cứng không bao giờ vượt trên simple / followup / complex / review ─────────────────────────────────────────

FOLLOWUP_HISTORY = [{"role": "user", "content": "Hỏi về đặt cọc"}, {"role": "assistant", "content": "Câu trả lời dài về đặt cọc. " * 40}]
REVIEW_DOCS = [{"text": "HỢP ĐỒNG ĐẶT CỌC. Điều 1: bên A nhận cọc 3 tỷ đồng. Điều 2: phạt cọc. " * 12, "source_kind": "docx"}]
BRANCHES = {
    "simple": (SIMPLE_Q, [], None),
    "followup": ("Hãy tóm tắt lại câu trả lời trên", FOLLOWUP_HISTORY, None),
    "complex": (CAU_1, [], None),
    "review": ("Rà soát hợp đồng này giúp tôi", [], REVIEW_DOCS),
}


@pytest.mark.parametrize("branch", list(BRANCHES))
def test_hard_cap_holds_on_every_branch_and_responder_keeps_its_floor(branch):
    question, history, documents = BRANCHES[branch]
    decision = admission.admit(question, history, documents=documents)
    budget = decision.budget
    assert decision.branch == branch and budget.max_cost_nano_usd == CAP
    assert decision.floor_nano <= CAP
    assert budget.ledger_total_nano + budget.reserved_for_responder_cost_nano <= CAP  # sổ cái + phần giữ cho responder vừa trần

    if branch == "complex":  # planner và grader dùng hết trần của mình
        planner = budget.reserve("planner", *ROUTE, decision.planner_input_tokens, budget.clamp_output("planner", 3072))
        grader = budget.reserve("relevance", *ROUTE, 3600, 320)
        assert planner and grader
        assert budget.reserve("relevance", *ROUTE, 1, 0) is None  # 3.600 input của stage relevance đã hết
        assert planner.output_tokens == 896  # cap planner đã bị kẹp theo phần admission cấp

    if branch != "followup":  # followup chỉ chạy responder: không tốn embedding
        def handler(request):
            granted = json.loads(request.content)["embedding_token_budget"]
            return httpx.Response(200, json={"data": {"chunks": [], "embedding_usage": {
                "prompt_tokens": granted, "cost_nano_usd": granted * 150, "skipped_reason": None}}})
        for n in range(12):
            asyncio.run(rag_client.search(f"truy vấn pháp lý số {n} " * 30, budget=budget, transport=httpx.MockTransport(handler)))
            assert budget.ledger_total_nano <= CAP
        assert budget.confirmed_embedding_tokens <= 1024
    else:
        assert decision.fixed_nano == 0

    out = budget.available_responder_output_tokens("openrouter", MODEL, decision.responder_input_cap, 10**6, min_output=1)
    assert out >= budget.responder_min_output > 0  # planner/grader/embedding không làm đói output của responder
    assert budget.reserve("responder", *ROUTE, decision.responder_input_cap, out)
    assert budget.ledger_total_nano <= CAP


# ── 4. Câu 1 vượt qua admission ───────────────────────────────────────────────────────────────────────────────────────

def test_cau_1_passes_admission_within_one_cent():
    assert 1700 <= len(CAU_1) <= 2000
    decision = admission.admit(CAU_1, [])
    assert decision.branch == "complex" and decision.issue_groups == 4
    assert decision.floor_nano == decision.fixed_nano + 300 * (decision.planner_input_tokens + decision.responder_input_cap) \
        + 1200 * decision.budget.responder_min_output or decision.floor_nano < CAP
    assert decision.fixed_nano == 2_692_800 and decision.floor_nano <= CAP
    assert decision.responder_output_cap >= 1600 + 225 * 4 and decision.responder_input_cap >= decision.responder_fixed_tokens + 1200 + 450 * 4
    assert decision.budget.responder_reserved_input == decision.responder_input_cap
    assert decision.min_quota_tokens <= get_settings().daily_token_limit  # vừa hạn mức token ngày 20.000


def test_admission_rejects_before_any_io_when_gate_functions_are_called_directly():
    with pytest.raises(admission.AdmissionError) as too_long:
        admission.admit("x" * 20001, [])
    assert (too_long.value.status_code, too_long.value.code) == (413, "QUESTION_TOO_LONG")
    with pytest.raises(admission.AdmissionError) as many:
        admission.admit(NINE_ISSUES, [])
    assert many.value.status_code == 422 and "Lượt này chưa trừ hạn mức." in many.value.message
    assert admission.estimate_issue_groups(NINE_ISSUES) == 9 and admission.estimate_issue_groups("Không có dấu hỏi") == 1


# ── 5. Từ chối admission trên POST, SSE và public chat: 0 RAG / 0 embedding / 0 LLM / 0 quota / 0 tin nhắn ────────────

REJECTIONS = {
    "too_long": ({}, "x" * 20001, 413, "QUESTION_TOO_LONG"),
    "too_many_issue_groups": ({}, NINE_ISSUES, 422, "QUESTION_EXCEEDS_BUDGET"),
    "cold_floor_over_one_cent": ({}, OVER_FLOOR, 422, "QUESTION_EXCEEDS_BUDGET"),
    "quota_below_floor": ({"DAILY_TOKEN_QUOTA_ENABLED": "true"}, SIMPLE_Q, 429, "DAILY_QUOTA_EXCEEDED"),
    "unverified_pricing_route": ({"OPENROUTER_MODEL": "vendor/unverified-model"}, SIMPLE_Q, 503, "AI_BUDGET_CONFIGURATION_UNAVAILABLE"),
}


def prepare_scenario(monkeypatch, settings_env):
    env(monkeypatch, **settings_env)
    if settings_env.get("DAILY_TOKEN_QUOTA_ENABLED"):  # còn đúng 10 token: used < limit nên kiểm tra cũ vẫn cho qua
        today = quota.get_today_vn()
        quota._local_add_usage(quota._key(USER_A, today), get_settings().daily_token_limit - 10)
        quota._local_add_usage(quota._key("public-ip:9.9.9.9", today), get_settings().public_chat_daily_token_limit - 10)


def usage_of(subject):
    return quota._local_get_usage(quota._key(subject, quota.get_today_vn()))


def assert_nothing_spent(spies, db, conversation_id=None):
    assert spies == {"rag": 0, "embedding": 0, "llm": 0, "save": 0, "quota_add": 0}
    if conversation_id:
        assert repo.get_messages(db, uuid.UUID(conversation_id)) == []


@pytest.mark.parametrize("name", list(REJECTIONS))
@pytest.mark.parametrize("path", ["messages", "messages/stream"])
def test_conversation_admission_rejections_are_json_and_spend_nothing(client, db, spies, monkeypatch, name, path):
    settings_env, content, status, code = REJECTIONS[name]
    cid = new_conversation(client)
    prepare_scenario(monkeypatch, settings_env)
    before = usage_of(USER_A)
    r = client.post(f"{BASE}/{cid}/{path}", json={"content": content}, headers={"X-Request-ID": "req-gate-1"})
    assert r.status_code == status
    assert r.headers["content-type"].startswith("application/json")  # SSE bị từ chối TRƯỚC khi mở stream
    body = r.json()
    assert body["error"]["code"] == code and body["request_id"] == "req-gate-1" and r.headers["X-Request-ID"] == "req-gate-1"
    assert body["data"] is None
    if status == 503:
        assert "rút gọn" not in body["error"]["message"].lower()  # lỗi vận hành, không bảo người dùng rút gọn câu hỏi
    if status in (413, 422):
        assert "Lượt này chưa trừ hạn mức." in body["error"]["message"]
    assert_nothing_spent(spies, db, cid)
    assert usage_of(USER_A) == before


@pytest.mark.parametrize("name", list(REJECTIONS))
def test_public_chat_never_swallows_admission_error_into_200(monkeypatch, spies, name):
    settings_env, content, status, code = REJECTIONS[name]
    prepare_scenario(monkeypatch, settings_env)
    procedure_searches = []

    async def found(message, request_id):  # tìm được thủ tục: trước đây đủ để che lỗi thành HTTP 200
        procedure_searches.append(message)
        return {"id": "p1", "code": "DD01", "name": "Sang tên sổ đỏ"}

    monkeypatch.setattr(main, "find_procedure", found)
    before = usage_of("public-ip:9.9.9.9")
    r = TestClient(app).post("/api/v1/agent/chat", json={"message": content}, headers={"X-Real-IP": "9.9.9.9"})
    assert r.status_code == status and r.json()["error"]["code"] == code and r.json()["data"] is None
    assert procedure_searches == []  # bị chặn trước asyncio.gather: không tìm thủ tục, không gọi agent
    assert_nothing_spent(spies, None)
    assert usage_of("public-ip:9.9.9.9") == before


def test_public_chat_quota_floor_uses_public_limit_not_the_conversation_limit(monkeypatch, spies):
    """Hạn mức công khai 8.000 token/ngày: hồ sơ phức tạp vừa $0.01 nhưng không vừa quota công khai thì 429 đúng nguyên nhân."""
    env(monkeypatch, DAILY_TOKEN_QUOTA_ENABLED="true")
    r = TestClient(app).post("/api/v1/agent/chat", json={"message": CAU_1}, headers={"X-Real-IP": "8.8.8.8"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "DAILY_QUOTA_EXCEEDED"
    assert_nothing_spent(spies, None)


# ── 6. Câu 1 và request hợp lệ đi qua cổng, agent nhận đúng CaseBudget ─────────────────────────────────────────────

@pytest.fixture
def fake_agent(monkeypatch):
    calls = []

    async def process_message(user_content, history, request_id=None, documents=None, owner_scope=None, budget=None):
        calls.append({"history": history, "budget": budget, "owner_scope": owner_scope})
        return {"content": "Trả lời.", "metadata": {"rag_chunk_ids": [], "model": "m", "tokens_used": 10}}

    monkeypatch.setattr(agent, "process_message", process_message)
    return calls


def test_cau_1_is_admitted_over_post_and_agent_receives_the_admitted_budget(client, monkeypatch, fake_agent):
    env(monkeypatch, DAILY_TOKEN_QUOTA_ENABLED="true")
    cid = new_conversation(client)
    r = client.post(f"{BASE}/{cid}/messages", json={"content": CAU_1})
    assert r.status_code == 200
    budget = fake_agent[0]["budget"]
    assert budget.max_cost_nano_usd == CAP and budget.branch == "complex" and budget.responder_reserved_input > 0
    assert fake_agent[0]["owner_scope"] == USER_A  # BOLA: chủ hội thoại lấy từ JWT, không từ body


def test_idempotency_key_replays_without_a_second_agent_call(client, fake_agent):
    cid = new_conversation(client)
    first = client.post(f"{BASE}/{cid}/messages", json={"content": SIMPLE_Q}, headers={"Idempotency-Key": "k-1"})
    again = client.post(f"{BASE}/{cid}/messages", json={"content": SIMPLE_Q}, headers={"Idempotency-Key": "k-1"})
    assert first.status_code == again.status_code == 200 and len(fake_agent) == 1
    assert first.json()["data"]["assistant_message"]["id"] == again.json()["data"]["assistant_message"]["id"]
    other = client.post(f"{BASE}/{cid}/messages", json={"content": "Câu hỏi khác hẳn?"}, headers={"Idempotency-Key": "k-1"})
    assert other.status_code == 409 and other.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED" and len(fake_agent) == 1
    client.post(f"{BASE}/{cid}/messages", json={"content": SIMPLE_Q})  # không có khoá: chạy bình thường
    assert len(fake_agent) == 2


# ── 7. TEST_UNLIMITED_USERS: miễn quota token ngày nhưng VẪN bị trần $0.01 ─────────────────────────────────────────

def test_unlimited_user_skips_daily_quota_but_still_gets_the_one_cent_budget(client, monkeypatch, fake_agent):
    env(monkeypatch, DAILY_TOKEN_QUOTA_ENABLED="true", TEST_UNLIMITED_USERS=USER_A)
    quota._local_add_usage(quota._key(USER_A, quota.get_today_vn()), get_settings().daily_token_limit)  # đã hết quota ngày
    cid = new_conversation(client)
    r = client.post(f"{BASE}/{cid}/messages", json={"content": CAU_1})
    assert r.status_code == 200
    assert fake_agent[0]["budget"].max_cost_nano_usd == CAP and fake_agent[0]["budget"].enabled


@pytest.mark.parametrize("name", ["too_many_issue_groups", "cold_floor_over_one_cent", "unverified_pricing_route"])
@pytest.mark.parametrize("path", ["messages", "messages/stream"])
def test_unlimited_user_is_still_rejected_by_cost_gate(client, db, spies, monkeypatch, name, path):
    settings_env, content, status, code = REJECTIONS[name]
    env(monkeypatch, DAILY_TOKEN_QUOTA_ENABLED="true", TEST_UNLIMITED_USERS=USER_A, **settings_env)
    cid = new_conversation(client)
    r = client.post(f"{BASE}/{cid}/{path}", json={"content": content})
    assert r.status_code == status and r.json()["error"]["code"] == code
    assert_nothing_spent(spies, db, cid)


def test_unlimited_user_is_exempt_from_the_quota_floor_check(monkeypatch):
    env(monkeypatch, DAILY_TOKEN_QUOTA_ENABLED="true", TEST_UNLIMITED_USERS=USER_A)
    quota._local_add_usage(quota._key(USER_A, quota.get_today_vn()), 19_999)
    decision = admission.admit(SIMPLE_Q, [], subject=USER_A)  # không ném 429 dù chỉ còn 1 token
    assert decision.budget.max_cost_nano_usd == CAP
    other = "someone-else-with-no-exemption"  # exemption chỉ theo đúng user_id đã khai báo
    quota._local_add_usage(quota._key(other, quota.get_today_vn()), 19_999)
    with pytest.raises(HTTPException) as err:
        admission.admit(SIMPLE_Q, [], subject=other)
    assert err.value.status_code == 429


# ── 8. llm_client: ghi sổ theo từng stage, giữ unsettled khi timeout, không retry ngầm ───────────────────────────────

def fake_openrouter(monkeypatch, outcome, **settings):
    calls = []
    cfg = Settings(_env_file=None, llm_provider="openrouter", openrouter_model=MODEL, openrouter_api_key="t", **settings)
    monkeypatch.setattr(llm_client, "get_settings", lambda: cfg)

    async def create(**kwargs):
        calls.append(kwargs)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(llm_client, "_get_openrouter_client", lambda: NS(chat=NS(completions=NS(create=create))))
    return calls


def test_llm_dispatch_records_stage_telemetry_and_exact_cost(monkeypatch):
    usage = NS(total_tokens=1500, prompt_tokens=1000, completion_tokens=500, cost=0.0009,
               prompt_tokens_details=NS(cached_tokens=200), completion_tokens_details=NS(reasoning_tokens=0))
    response = NS(usage=usage, choices=[NS(message=NS(content="ok"), finish_reason="stop")])
    calls = fake_openrouter(monkeypatch, response)
    budget = CaseBudget(max_cost_nano_usd=CAP, responder_output_cap=300, responder_reserved_output=0)
    asyncio.run(llm_client.generate("s", [], "q", model=MODEL, budget=budget, budget_stage="responder", max_output_tokens=3500))
    entry = budget.ledger[0]
    assert calls[0]["max_tokens"] == 300 == entry["max_output_cap_sent"]  # cap gửi đi bằng cap đã giữ chỗ (admission clamp)
    assert (entry["stage"], entry["input_tokens"], entry["cached_tokens"], entry["output_tokens"], entry["finish_reason"]) == \
        ("responder", 1000, 200, 500, "stop")
    assert entry["confirmed_cost_nano_usd"] == 900_000 == budget.confirmed_cost_nano_usd
    assert entry["provider"] == "openrouter" and entry["model"] == MODEL and entry["request_turn_id"] == budget.request_turn_id
    assert budget.in_flight_cost_nano_usd == 0 and budget.unsettled_cost_nano_usd == 0


@pytest.mark.parametrize("retries", [0, 3])
def test_llm_timeout_keeps_reserved_cost_unsettled_and_never_retries_under_budget(monkeypatch, retries):
    calls = fake_openrouter(monkeypatch, asyncio.TimeoutError(), openrouter_max_retries=retries)
    budget = CaseBudget(max_cost_nano_usd=CAP, responder_reserved_output=0)
    with pytest.raises(llm_client.LLMError):
        asyncio.run(llm_client.generate("s", [], "q", model=MODEL, budget=budget, max_output_tokens=300))
    assert len(calls) == 1  # không retry ngầm: mỗi lần gửi thật phải có khoản giữ chỗ riêng
    assert budget.unsettled_cost_nano_usd > 0 and budget.in_flight_cost_nano_usd == 0
    assert budget.ledger_total_nano == budget.unsettled_cost_nano_usd <= CAP


def test_llm_never_dispatches_when_reservation_would_exceed_cap(monkeypatch):
    calls = fake_openrouter(monkeypatch, NS(usage=None, choices=[]))
    budget = CaseBudget(max_cost_nano_usd=1000, responder_reserved_output=0)
    with pytest.raises(case_budget.BudgetExhaustedError):
        asyncio.run(llm_client.generate("s", [], "q", model=MODEL, budget=budget, max_output_tokens=300))
    assert calls == [] and budget.attempts == 0


def test_upper_bound_estimator_covers_utf8_and_chat_template_framing():
    text = "á" * 8000  # 16.000 byte UTF-8
    assert case_budget.estimate_text_tokens(text) == 8_000
    assert case_budget.estimate_text_tokens(text, strict_byte_upper_bound=True) == 16_000
    one = case_budget.estimate_messages_input_upper_bound([("user", text)])
    two = case_budget.estimate_messages_input_upper_bound([("system", ""), ("user", text)])
    assert one >= 8_000 + case_budget.CONVERSATION_FRAMING_TOKENS + case_budget.MESSAGE_FRAMING_TOKENS
    assert two - one >= case_budget.MESSAGE_FRAMING_TOKENS  # thêm một message thì thêm đúng phần khung
    assert case_budget.estimate_chat_input_upper_bound("s", [{"role": "user", "content": "a"}], "q") > \
        case_budget.estimate_chat_input_upper_bound("s", [], "q")
