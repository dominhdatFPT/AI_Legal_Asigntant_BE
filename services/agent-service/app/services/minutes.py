"""Biên bản hội thoại user - luật sư (API nội bộ của consultation-service). Không lưu gì, không log nội dung tin nhắn.
Agent chỉ bảo đảm trả về một object JSON; kiểm schema và kiểm số liệu nguyên văn là việc của consultation-service."""
import asyncio
import html
import json
import re

from app.core.config import get_settings
from app.schemas.minutes import MinutesRequest
from app.services import llm_client, minutes_prompt

PROVIDER_TIMEOUT_SECONDS = 140.0
TOTAL_TIMEOUT_SECONDS = 145.0  # consultation-service chờ tối đa 150 giây (việc nền, không có người chờ)
# Đo 08/11/2026 với recursive/flint-v1.0:free: 4 tin mất ~6400 token (5600 là suy luận bắt buộc, không tắt được) và ~37 giây.
# max_tokens tính cả suy luận nên phải rộng, nếu không model hết token trước khi ra JSON.
MAX_OUTPUT_TOKENS = 16000

DOMAINS = ["dat_dai", "ho_kinh_doanh", "dan_su", "lao_dong", "hon_nhan_gia_dinh", "hanh_chinh", "hinh_su", "khac"]
_PARTY = {"enum": ["citizen", "lawyer"]}
_IDS = {"type": "array", "items": {"type": "string"}, "minItems": 1}
_NULL_STR = {"type": ["string", "null"]}


def _text(limit: int = 300) -> dict:
    return {"type": "string", "maxLength": limit}


def _items(properties: dict, ids: dict = _IDS) -> dict:
    item = {"type": "object", "additionalProperties": False, "required": [*properties, "source_ids"],
            "properties": {**properties, "source_ids": ids}}
    return {"type": "array", "maxItems": 15, "items": item}


# Schema JSON đã chốt (schema_version 1); consultation-service có bản kiểm tương ứng trong transcript_validator.py.
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["has_substance", "legal_domains", "overview", "citizen_issues", "facts", "lawyer_advice",
                 "legal_references", "deadlines", "next_steps", "conflicts", "open_questions"],
    "properties": {
        "has_substance": {"type": "boolean"},
        "legal_domains": {"type": "array", "items": {"enum": DOMAINS}},
        "overview": {"type": ["string", "null"], "maxLength": 600},
        "citizen_issues": _items({"content": _text()}),
        "facts": _items({
            "type": {"enum": ["date", "amount", "area", "person", "organization", "document_number", "land_parcel",
                              "location", "other"]},
            "value": {"type": "string", "description": "Nguyên văn trong tin nhắn"},
            "context": _text(), "stated_by": _PARTY}),
        "lawyer_advice": _items({"content": _text()}),
        "legal_references": _items({
            "document": _NULL_STR, "article": _NULL_STR, "clause": _NULL_STR, "point": _NULL_STR,
            "raw_text": {"type": "string", "description": "Nguyên văn cụm chứa căn cứ"}, "cited_by": _PARTY}),
        "deadlines": _items({"value": {"type": "string", "description": "Nguyên văn mốc thời gian"},
                             "description": _text(), "stated_by": _PARTY}),
        "next_steps": _items({"actor": _PARTY, "content": _text()}),
        "conflicts": _items({"topic": _text(), "citizen_statement": _text(), "lawyer_statement": _text()},
                            ids={"type": "array", "items": {"type": "string"}, "minItems": 2}),
        "open_questions": _items({"content": _text()}, ids={"type": "array", "items": {"type": "string"}}),
    },
}
_SCHEMA_TEXT = json.dumps(SCHEMA, ensure_ascii=False)


def build_user_prompt(req: MinutesRequest) -> str:
    """Nội dung tin được escape & < > để một tin không thể đóng thẻ </msg> hay </new_messages> giả."""
    previous = req.previous_summary.model_dump_json() if req.previous_summary else "KHÔNG CÓ"
    messages = "\n".join(
        f'<msg id="{html.escape(m.id)}" role="{m.role}" time="{m.time.isoformat()}">'
        f"{html.escape(m.content, quote=False)}</msg>" for m in req.messages)
    start = req.window_start.isoformat() if req.window_start else "null"
    return (
        f"<conversation_meta>\nconsultation_id: {req.consultation_id}\nwindow_start: {start}\n"
        f"window_end: {req.window_end.isoformat()}\n</conversation_meta>\n\n"
        f"<previous_summary>\n{previous}\n</previous_summary>\n\n<new_messages>\n{messages}\n</new_messages>\n\n"
        f"<output_schema>\n{_SCHEMA_TEXT}\n</output_schema>\n\n"
        "Hãy trích xuất đoạn hội thoại trong <new_messages> theo đúng các quy tắc và schema.")


def parse_json_object(text: str) -> dict:
    """Lấy object JSON từ câu trả lời; bỏ hàng rào ``` nếu model lỡ bọc. ValueError nếu không phải object."""
    body = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("không phải object JSON")
    return value


async def summarize(req: MinutesRequest) -> tuple[dict, str | None]:
    """Trả (biên bản, model). llm_client.LLMError nếu LLM lỗi / quá giờ; ValueError nếu kết quả không phải JSON object."""
    model = get_settings().minutes_model or None
    try:
        text, _tokens, model = await asyncio.wait_for(
            llm_client.generate(
                minutes_prompt.SYSTEM_PROMPT, [], build_user_prompt(req),
                max_output_tokens=MAX_OUTPUT_TOKENS, timeout_seconds=PROVIDER_TIMEOUT_SECONDS, max_retries=0,
                model=model, reasoning_max_tokens=0 if model else None,  # model riêng: tắt suy luận (model free không tắt được)
            ),
            timeout=TOTAL_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as e:
        raise llm_client.LLMError(f"Biên bản quá {TOTAL_TIMEOUT_SECONDS:.0f} giây") from e
    return parse_json_object(text), model
