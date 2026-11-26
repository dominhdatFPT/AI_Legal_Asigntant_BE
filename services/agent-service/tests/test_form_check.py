"""Test POST /api/v1/agent/form-check: lọc PII, cache, hạn mức riêng, lỗi LLM. LLM và Redis giả lập."""
import asyncio
import json
import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.auth import TokenPayload
from app.main import app
from app.schemas.form_check import FormCheckField
from app.services import cache, form_check, llm_client, quota

USER = "11111111-1111-4111-8111-111111111111"
URL = "/api/v1/agent/form-check"


def F(key, label="", value="x", word_key=None, **kw):
    return FormCheckField(key=key, label=label, type="text", value=value, word_key=word_key, **kw)


def body(*fields, **extra):
    return {"procedure_code": "DD-003", "form_name": "Đơn", "fields": [
        {"type": "text", **f} for f in fields], **extra}


def fld(key, label, value, **kw):
    return {"key": key, "label": label, "value": value, **kw}


@pytest.fixture(autouse=True)
def fresh_cache():
    form_check.reset()
    yield
    form_check.reset()


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: TokenPayload(
        sub=USER, role="citizen", type="access", iss="legal-assistant-user-service", iat=0, exp=9999999999)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def llm(monkeypatch):
    """LLM giả: .reply là text trả về (hoặc exception), .prompts ghi lại user prompt."""
    state = SimpleNamespace(reply='{"results": []}', prompts=[], kwargs=[])

    async def fake_generate(system_prompt, history, user_content, **kwargs):
        state.prompts.append(user_content)
        state.kwargs.append(kwargs)
        if isinstance(state.reply, Exception):
            raise state.reply
        return state.reply, 10, "fake-model"

    monkeypatch.setattr(llm_client, "generate", fake_generate)
    return state


def results(llm_reply: list) -> str:
    return "```json\n" + json.dumps({"results": llm_reply}, ensure_ascii=False) + "\n```"


# ------------------------------------------------------------------ sanitize
SENT = [
    ("land_address", "Địa chỉ thửa đất", None),
    ("parcel_address", "Địa chỉ thửa đất", "parcel_address"),
    ("asset_address", "Địa chỉ tài sản / thửa đất", None),
    ("property_address", "Địa chỉ thửa đất", None),
    ("land_location", "Địa điểm thửa đất / khu đất", None),
    ("location", "Vị trí thửa đất đang tranh chấp", None),
    ("use_location", "Địa điểm sử dụng đất", None),
    ("business_address", "Địa chỉ kinh doanh", None),
    ("address", "Địa chỉ mặt bằng", None),
    ("coop_unit_address", "Địa chỉ đơn vị", None),
    ("headquarters_address", "Địa chỉ trụ sở chính", None),
    ("rental_address", "Địa chỉ nơi thuê", None),
    ("topsoil_reuse_location", "Địa điểm tái sử dụng", None),
    ("place", "Địa điểm lập đơn", None),
    ("signing_place", "Địa điểm ký", None),
    ("notary_place", "Địa điểm công chứng", None),
    ("land_type", "Loại đất", None),
    ("building_type", "Loại công trình", None),
    ("project_category_grade", "Cấp công trình", None),
    ("recipient", "Kính gửi", None),
    ("receiving_agency", "Cơ quan tiếp nhận", None),
    ("licensing_authority", "Cơ quan cấp phép", None),
    ("legal_representative_title", "Chức danh", None),
    ("owner_nationality", "Quốc tịch", None),
    ("reason", "Lý do", None),
    ("request_content", "Nội dung đề nghị", None),
    ("group_parcels_2.land_type", "Loại đất", "land_type"),
]
NOT_SENT = [
    ("applicant_permanent_address", "Địa chỉ thường trú", None),
    ("applicant_current_address", "Nơi ở hiện tại", None),
    ("applicant_address", "Địa chỉ / trụ sở chính", None),
    ("heir_address", "Hộ khẩu thường trú", None),
    ("co_user_address", "Địa chỉ", None),
    ("party_address", "Địa chỉ", None),
    ("person_address", "Địa chỉ", None),
    ("address", "Địa chỉ", None),
    ("legal_representative_address", "Địa chỉ liên lạc của người đại diện", None),
    ("address_detail", "Số nhà, đường, tổ/xóm/ấp/thôn", None),
    ("result_address", "Địa chỉ nhận kết quả", None),
    ("result_electronic", "Địa chỉ / cách thức nhận điện tử (nếu chọn)", None),
    ("owner_address", "Địa chỉ chủ sở hữu", None),
    ("organization_address", "Địa chỉ tổ chức", None),
    ("address", "Địa chỉ tại tỉnh khác", None),
    ("property_address", "Địa chỉ tài sản", None),
    ("identity_number", "Số giấy tờ", None),
    ("personal_info_2.id_number", "Số", None),
    ("personal_info_2.phone", "Liên hệ", None),
    ("authorizer_health_card_number", "Số thẻ", None),
    ("authorizer_social_insurance_code", "Mã", None),
    ("fax", "Fax", None),
    ("contact_fax", "x", None),
    ("party_fax", "x", None),
    ("full_name", "Họ và tên", None),
    ("applicant_name", "Người nộp", "applicant_name"),
    ("authorized_representative", "Người được ủy quyền", None),
    ("creditor_signer", "Chủ nợ ký", None),
    ("requester_signer", "Người yêu cầu ký", None),
    ("distribution_receiver", "x", None),
    ("surviving_owner", "x", None),
    ("related_people", "x", None),
    ("tax_agent_staff", "x", None),
    ("management_authority", "Thành viên quản lý / đại diện công ty", None),
    ("legal_representative", "Người nộp thuế / đại diện hợp pháp", None),
    ("investor_representative", "x", None),
    ("contractor_representative", "x", None),
    ("adjacent_owners_info", "x", None),
    ("representative", "x", None),
    ("certificate_number", "Số GCN", None),
    ("parcel_number", "Số thửa", None),
    ("map_sheet_number", "Tờ bản đồ", None),
    ("business_code", "Mã ngành", None),
    ("accounting_year", "Năm", None),
    ("something", "Nội dung", "applicant_cccd"),
]


@pytest.mark.parametrize("key,label,word_key", SENT)
def test_sanitize_keeps_allowed(key, label, word_key):
    assert len(form_check.sanitize([F(key, label, "chữ", word_key)])) == 1


@pytest.mark.parametrize("key,label,word_key", NOT_SENT)
def test_sanitize_drops_personal_and_identifier(key, label, word_key):
    assert form_check.sanitize([F(key, label, "chữ", word_key)]) == []


def test_sanitize_drops_numeric_only_and_blank_values():
    assert form_check.sanitize([F("note", "Ghi chú", "123 / 45"), F("note2", "Ghi chú", "   ")]) == []


def test_sanitize_masks_pii_in_free_text():
    [out] = form_check.sanitize([F("note", "Ghi chú",
                                   "cccd 001095012345, sdt 0912345678, mail a.b@x.com, +84912345678")])
    assert out.value == "cccd [SỐ], sdt [SĐT], mail [EMAIL], [SĐT]"


# ------------------------------------------------------------------ API: xác thực / validation
def test_requires_token():
    app.dependency_overrides.clear()
    r = TestClient(app).post(URL, json=body(fld("land_type", "Loại đất", "x")))
    assert r.status_code == 401


def test_rejects_user_id_and_cccd_type(client):
    assert client.post(URL, json=body(fld("a", "b", "c"), user_id=USER)).status_code == 422
    bad = {"key": "a", "label": "b", "value": "c", "type": "cccd"}
    assert client.post(URL, json={"fields": [bad]}).status_code == 422
    assert client.post(URL, json={"fields": [{"key": "a", "label": "b", "value": "c", "type": "text", "autofill": "x"}]}
                       ).status_code == 422


def test_rejects_more_than_60_fields(client):
    r = client.post(URL, json=body(*[fld(f"k{i}", "Lý do", "x") for i in range(61)]))
    assert r.status_code == 422


def test_rejects_duplicate_field_keys(client, llm):
    r = client.post(URL, json=body(fld("reason", "Lý do", "một"), fld("reason", "Lý do", "hai")))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert llm.prompts == []


def test_empty_after_filter_skips_llm_and_quota(client, llm, monkeypatch):
    monkeypatch.setattr(quota, "_execute", lambda op: pytest.fail("không được trừ lượt"))
    r = client.post(URL, json=body(fld("full_name", "Họ và tên", "Nguyễn Văn A")))
    assert r.status_code == 200
    assert r.json()["data"] == {"checked_keys": [], "unchecked_keys": [], "results": [], "cached": 0, "model": None}
    assert llm.prompts == []
    assert r.headers["X-Request-ID"] == r.json()["request_id"]


# ------------------------------------------------------------------ API: kết quả LLM
def test_results_filtered_and_prompt_has_no_pii(client, llm):
    llm.reply = results([
        {"key": "building_type", "status": "suggest", "suggested_value": "Nhà ở riêng lẻ", "reason": "Gọi đúng loại nhà"},
        {"key": "land_type", "status": "ok"},
        {"key": "reason", "status": "suggest", "suggested_value": "[SỐ] abc", "reason": "x"},
        {"key": "land_location", "status": "suggest", "suggested_value": "cùng giá trị", "reason": "x"},
        {"key": "not_sent", "status": "warning", "reason": "key lạ"},
    ])
    r = client.post(URL, headers={"X-Request-ID": "req-test-1"}, json=body(
        fld("building_type", "Loại công trình", "nhà"),
        fld("land_type", "Loại đất", "Đất ở"),
        fld("reason", "Lý do", "CCCD 001095012345 gọi 0912345678"),
        fld("land_location", "Địa điểm thửa đất / khu đất", "cùng  giá trị"),
        fld("full_name", "Họ và tên", "Nguyễn Văn Test"),
        fld("fax", "Fax", "0281234567"),
        fld("something", "Cơ quan tiếp nhận", "ubnd"),
    ))
    assert r.status_code == 200
    assert r.headers["X-Request-ID"] == "req-test-1"
    data = r.json()["data"]
    assert data["checked_keys"] == ["building_type", "land_type", "reason", "land_location"]
    assert data["unchecked_keys"] == ["something"]
    assert data["results"] == [
        {"key": "building_type", "status": "suggest", "suggested_value": "Nhà ở riêng lẻ", "reason": "Gọi đúng loại nhà"},
        {"key": "reason", "status": "warning", "suggested_value": None, "reason": "x"},
    ]
    assert data["model"] == "fake-model" and data["cached"] == 0
    prompt = llm.prompts[0]
    for secret in ("001095012345", "0912345678", "Nguyễn Văn Test", "0281234567"):
        assert secret not in prompt
    assert "[SỐ]" in prompt and "Điều 9. Phân loại đất" in prompt  # nhãn "Loại đất" → glossary đất
    assert "Ủy ban nhân dân xã/phường" in prompt  # nhãn "Cơ quan tiếp nhận"
    assert llm.kwargs[0]["max_retries"] == 0 and llm.kwargs[0]["max_output_tokens"] == 1200


@pytest.mark.parametrize(("value", "suggested"), [
    ("gọi 0912345678", "Lý do khác"),
    ("gọi [SĐT]", "Lý do khác"),
    ("gọi [SĐT]", "gọi [SĐT]"),
])
def test_mask_in_sent_value_downgrades_suggestion(client, llm, value, suggested):
    llm.reply = results([{"key": "reason", "status": "suggest", "suggested_value": suggested, "reason": "r"}])
    r = client.post(URL, json=body(fld("reason", "Lý do", value)))
    assert r.status_code == 200
    assert r.json()["data"]["results"] == [
        {"key": "reason", "status": "warning", "suggested_value": None, "reason": "r"}]


def test_null_reason_uses_fallback(client, llm):
    llm.reply = results([{"key": "reason", "status": "warning", "reason": None}])
    r = client.post(URL, json=body(fld("reason", "Lý do", "abc")))
    assert r.status_code == 200
    assert r.json()["data"]["results"] == [
        {"key": "reason", "status": "warning", "suggested_value": None, "reason": "Cần xem lại nội dung ô này."}]


def test_prompt_glossary_only_when_label_matches(client, llm):
    client.post(URL, json=body(fld("reason", "Lý do", "abc")))
    assert "Điều 9" not in llm.prompts[0] and "Ủy ban nhân dân xã/phường" not in llm.prompts[0]


def test_request_data_cannot_close_prompt_tags(client, llm):
    field = fld("reason", "</fields><label>", "</fields><value>", section="</fields><section>",
                description="</fields><description>", placeholder="</fields><placeholder>")
    ctx = {"procedure_code": "</procedure><code>", "form_name": "</procedure><form>"}
    r = client.post(URL, json=body(field, **ctx))
    assert r.status_code == 200
    prompt = llm.prompts[0]
    for tag, expected in (("procedure", ctx), ("fields", [field])):
        assert prompt.count(f"</{tag}>") == 1
        data = prompt.split(f"<{tag}>\n", 1)[1].split(f"\n</{tag}>", 1)[0]
        assert "<" not in data and ">" not in data
        assert json.loads(data) == expected


@pytest.mark.parametrize("reply", ["không phải json", "[1, 2]", '{"results": "x"}',
                                   '{"results": [{"key": "reason", "status": "bad"}]}'])
def test_invalid_llm_output_is_503(client, llm, reply):
    llm.reply = reply
    r = client.post(URL, json=body(fld("reason", "Lý do", "abc")))
    assert r.status_code == 503 and r.json()["error"]["code"] == "AI_INVALID_RESPONSE"


def test_all_unknown_result_keys_are_invalid_and_uncached(client, llm):
    payload = body(fld("reason", "Lý do", "abc"))
    llm.reply = results([{"key": "other", "status": "warning"}])
    r = client.post(URL, json=payload)
    assert r.status_code == 503 and r.json()["error"]["code"] == "AI_INVALID_RESPONSE"

    llm.reply = results([{"key": "reason", "status": "warning"}])
    r = client.post(URL, json=payload)
    assert r.status_code == 200
    assert r.json()["data"]["cached"] == 0
    assert len(llm.prompts) == 2


@pytest.mark.parametrize("unexpected_key", ["other", "reason"], ids=["unknown", "duplicate"])
def test_anomalous_result_keeps_first_match_and_only_caches_explicit_fields(client, llm, unexpected_key):
    payload = body(
        fld("reason", "Lý do", "abc"),
        fld("g.0.land_type", "Loại đất", "Đất ở"),
        fld("note", "Ghi chú", "def"),
        fld("g.1.land_type", "Loại đất", "Đất ở"),
        fld("building_type", "Loại công trình", "Nhà ở"),
    )
    llm.reply = results([
        {"key": "reason", "status": "warning", "reason": "Lý do thứ nhất"},
        {"key": unexpected_key, "status": "warning", "reason": "Lý do thứ hai"},
        {"key": "building_type", "status": "ok"},
    ])
    r = client.post(URL, json=payload)
    assert r.status_code == 200
    first = r.json()["data"]
    assert first["checked_keys"] == ["reason", "building_type"]
    assert first["unchecked_keys"] == ["g.0.land_type", "note", "g.1.land_type"]
    assert [(item["key"], item["reason"]) for item in first["results"]] == [("reason", "Lý do thứ nhất")]

    llm.reply = results([{"key": "note", "status": "warning"}])
    r = client.post(URL, json=payload)
    assert r.status_code == 200
    second = r.json()["data"]
    assert second["cached"] == 2
    assert second["checked_keys"] == [field["key"] for field in payload["fields"]]
    assert second["unchecked_keys"] == []
    assert [item["key"] for item in second["results"]] == ["reason", "note"]
    assert len(llm.prompts) == 2
    retried_fields = json.loads(llm.prompts[1].split("<fields>\n", 1)[1].split("\n</fields>", 1)[0])
    assert [field["key"] for field in retried_fields] == ["g.0.land_type", "note"]


def test_llm_error_and_timeout_are_503(client, llm, monkeypatch):
    llm.reply = llm_client.LLMError("boom")
    r = client.post(URL, json=body(fld("reason", "Lý do", "abc")))
    assert r.status_code == 503 and r.json()["error"]["code"] == "AI_SERVICE_ERROR"

    async def timeout(coro, **k):
        coro.close()
        raise asyncio.TimeoutError

    llm.reply = '{"results": []}'
    monkeypatch.setattr(asyncio, "wait_for", timeout)
    r = client.post(URL, json=body(fld("reason", "Lý do", "abc2")))
    assert r.status_code == 503 and r.json()["error"]["code"] == "AI_SERVICE_ERROR"


@pytest.mark.parametrize("headers", [{}, {"X-Request-ID": "req-from-gateway"}])
def test_request_id_matches_error_response_and_log(client, llm, caplog, headers):
    llm.reply = llm_client.LLMError("boom")
    r = client.post(URL, json=body(fld("reason", "Lý do", "abc")), headers=headers)
    assert r.status_code == 503
    request_id = r.json()["request_id"]
    assert request_id and r.headers["X-Request-ID"] == request_id
    if headers:
        assert request_id == headers["X-Request-ID"]
    [record] = [record for record in caplog.records if record.name == "app.api.v1.form_check"]
    assert record.args == (request_id,)


def test_logs_do_not_contain_values(client, llm, caplog):
    llm.reply = results([{"key": "reason", "status": "suggest", "suggested_value": "Giá trị mới", "reason": "r"}])
    with caplog.at_level(logging.DEBUG):
        client.post(URL, json=body(fld("reason", "Nhãn bí mật", "GIATRIBIMAT")))
    assert "GIATRIBIMAT" not in caplog.text and "Nhãn bí mật" not in caplog.text


# ------------------------------------------------------------------ cache
def test_second_call_is_served_from_cache(client, llm, monkeypatch):
    llm.reply = results([{"key": "building_type", "status": "suggest", "suggested_value": "Nhà ở riêng lẻ", "reason": "r"}])
    charges = []
    monkeypatch.setattr(quota, "_execute", lambda op: charges.append(1) or 1)
    payload = body(fld("building_type", "Loại công trình", "nhà"), fld("land_type", "Loại đất", "Đất ở"))
    first = client.post(URL, json=payload).json()["data"]
    second = client.post(URL, json=payload).json()["data"]
    assert len(llm.prompts) == 1 and len(charges) == 1
    assert second["results"] == first["results"] and second["cached"] == 2 and second["model"] is None


def test_case_difference_is_cache_miss(client, llm):
    client.post(URL, json=body(fld("building_type", "Loại công trình", "nhà")))
    client.post(URL, json=body(fld("building_type", "Loại công trình", "Nhà")))
    assert len(llm.prompts) == 2


@pytest.mark.parametrize("metadata", ["form_name", "section", "description", "placeholder", "word_key"])
def test_context_difference_is_cache_miss(client, llm, metadata):
    first = body(fld("reason", "Lý do", "abc"))
    if metadata == "form_name":
        second = {**first, "form_name": "Mẫu khác"}
    else:
        second = body(fld("reason", "Lý do", "abc", **{metadata: "ngữ cảnh khác"}))
    assert client.post(URL, json=first).status_code == 200
    r = client.post(URL, json=second)
    assert r.status_code == 200
    assert r.json()["data"]["cached"] == 0
    assert len(llm.prompts) == 2


def test_cache_is_isolated_by_authenticated_user(client, llm):
    payload = body(fld("reason", "Lý do", "abc"))
    llm.reply = results([{"key": "reason", "status": "warning", "reason": "Nội dung của người dùng đầu tiên"}])
    first = client.post(URL, json=payload)
    assert first.status_code == 200 and first.json()["data"]["results"]

    token = app.dependency_overrides[get_current_user]()
    app.dependency_overrides[get_current_user] = lambda: token.model_copy(
        update={"sub": "22222222-2222-4222-8222-222222222222"})
    llm.reply = results([])
    r = client.post(URL, json=payload)
    assert r.status_code == 200
    assert r.json()["data"]["cached"] == 0 and r.json()["data"]["results"] == []
    assert len(llm.prompts) == 2


def test_repeated_rows_with_same_value_reach_llm_once(client, llm):
    llm.reply = results([{"key": "g.0.land_type", "status": "warning", "reason": "xem lại"}])
    r = client.post(URL, json=body(
        fld("g.0.land_type", "Loại đất", "dat vuon", word_key="land_type"),
        fld("g.1.land_type", "Loại đất", "dat vuon", word_key="land_type")))
    assert llm.prompts[0].count("dat vuon") == 1
    assert [x["key"] for x in r.json()["data"]["results"]] == ["g.0.land_type", "g.1.land_type"]


def test_repeated_rows_with_different_context_are_not_deduplicated(client, llm):
    r = client.post(URL, json=body(
        fld("g.0.land_type", "Loại đất", "dat vuon", word_key="land_type"),
        fld("g.1.land_type", "Loại đất", "dat vuon", word_key="land_purpose")))
    assert r.status_code == 200
    assert llm.prompts[0].count("dat vuon") == 2


# ------------------------------------------------------------------ hạn mức riêng
class FakeRedis:
    def __init__(self):
        self.values = {}

    def eval(self, script, numkeys, key, amount, ttl):
        self.values[key] = self.values.get(key, 0) + amount
        return self.values[key]


class FakeBreaker:
    def allow(self):
        return True

    def success(self):
        pass

    def failure(self, exc):
        pass


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(quota.cache, "_use_redis", lambda s: True)
    monkeypatch.setattr(quota.cache, "shared_breaker", lambda s: FakeBreaker())
    monkeypatch.setattr(quota.cache, "_get_client", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def memory_cache(monkeypatch):
    mem = cache.TTLCache(100, 60)  # để make_cache không dựng RedisCache trên FakeRedis
    monkeypatch.setattr(form_check, "_get_cache", lambda: mem)


def test_daily_limit_is_separate_from_chat_quota(client, llm, redis, monkeypatch):
    monkeypatch.setenv("FORM_CHECK_DAILY_LIMIT", "1")
    from app.core.config import get_settings
    get_settings.cache_clear()
    assert client.post(URL, json=body(fld("reason", "Lý do", "một"))).status_code == 200
    r = client.post(URL, json=body(fld("reason", "Lý do", "hai")))
    assert r.status_code == 429 and r.json()["error"]["code"] == "FORM_CHECK_LIMIT_EXCEEDED"
    assert list(redis.values) == [f"legal:formcheck:{USER}:{quota.get_today_vn()}"]  # không có legal:quota:*
    assert client.post(URL, json=body(fld("reason", "Lý do", "một"))).status_code == 200  # cache hit không tốn lượt
    assert len(llm.prompts) == 1


def test_redis_unavailable_fails_open(client, llm, monkeypatch):
    monkeypatch.setenv("FORM_CHECK_DAILY_LIMIT", "1")
    from app.core.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setattr(quota, "_execute", lambda op: None)
    for v in ("một", "hai", "ba"):
        assert client.post(URL, json=body(fld("reason", "Lý do", v))).status_code == 200


def test_unlimited_user_is_exempt(client, llm, redis, monkeypatch):
    monkeypatch.setenv("FORM_CHECK_DAILY_LIMIT", "1")
    monkeypatch.setenv("TEST_UNLIMITED_USERS", USER)
    from app.core.config import get_settings
    get_settings.cache_clear()
    for v in ("một", "hai"):
        assert client.post(URL, json=body(fld("reason", "Lý do", v))).status_code == 200
    assert redis.values == {}
