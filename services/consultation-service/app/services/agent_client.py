"""Gọi Agent Service (AGENT_SERVICE_URL). Contract: services/agent-service/openapi.yaml, API nội bộ tóm tắt hội thoại."""

import uuid
from datetime import datetime

from app.services.http_client import UpstreamError, bad_response, call

SERVICE = "AGENT"
SUMMARY_TIMEOUT_SECONDS = 60.0  # Agent gọi LLM để tóm tắt
TRANSCRIPT_TIMEOUT_SECONDS = 150.0  # biên bản là việc nền; model suy luận mất ~40 giây cho 4 tin


def summarize(conversation_id: uuid.UUID, user_id: uuid.UUID, until: datetime) -> str | None:
    """POST /internal/v1/agent/conversations/{id}/summary → tóm tắt các tin trước `until`.
    None khi không có tóm tắt: hội thoại không còn / không thuộc user (404) hoặc chưa có tin nào trước `until` (422)."""
    try:
        data = call(SERVICE, "POST", f"/internal/v1/agent/conversations/{conversation_id}/summary",
                    json={"user_id": str(user_id), "until": until.isoformat()}, timeout=SUMMARY_TIMEOUT_SECONDS)
    except UpstreamError as e:
        if (e.status, e.code) in ((404, "CONVERSATION_NOT_FOUND"), (422, "CONVERSATION_EMPTY")):
            return None
        raise bad_response(SERVICE) from None
    text = data.get("summary") if isinstance(data, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise bad_response(SERVICE)
    return text


def summarize_transcript(payload: dict) -> tuple[dict, str | None]:
    """POST /internal/v1/agent/transcripts/summary → (biên bản JSON, phiên bản prompt).
    payload: consultation_id, window_start, window_end, previous_summary (overview + legal_domains hoặc None),
    messages [{id, role CITIZEN|LAWYER, time, content đã che PII}]. Contract bên Agent: docs/api/CONSULTATION_DESIGN.md."""
    data = call(SERVICE, "POST", "/internal/v1/agent/transcripts/summary", json=payload,
                timeout=TRANSCRIPT_TIMEOUT_SECONDS)
    summary = data.get("summary") if isinstance(data, dict) else None
    if not isinstance(summary, dict):
        raise bad_response(SERVICE)
    version = data.get("prompt_version")
    return summary, version if isinstance(version, str) else None
