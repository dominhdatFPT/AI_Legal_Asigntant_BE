"""Nhật ký nạp (bảng ingest_log): ai làm gì, lúc nào, kết quả.

Ghi nhật ký là việc PHỤ: `record` tự mở và đóng một session riêng, commit riêng và nuốt mọi lỗi (chỉ ghi log WARNING),
nên không bao giờ làm hỏng việc chính (tạo nháp, xóa nháp, duyệt). Không ghi nội dung văn bản, không ghi khóa hay token.
"""

import logging
import re
import uuid
from datetime import datetime, timezone

from app.core.config import get_settings
from app.models import IngestLog

logger = logging.getLogger(__name__)

MAX_DETAIL_CHARS = 500

# Chuỗi kết nối, khóa API: không bao giờ được vào ingest_error hay nhật ký
_SECRET_PATTERNS = [
    re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://\S+"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"(?i)(key|token|password|secret)=\S+"),
]


def mask_secrets(text: str) -> str:
    """Thay khóa API, token nội bộ, chuỗi kết nối DB và các mẫu khóa phổ biến bằng ***. Không cắt độ dài."""
    s = get_settings()
    for secret in (s.gemini_api_key, s.internal_token, s.rag_database_url):
        if secret:
            text = text.replace(secret, "***")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("***", text)
    return text


def record(
    action: str,
    *,
    actor_user_id: str | None = None,
    actor_role: str | None = None,
    document_id: uuid.UUID | None = None,
    doc_number: str | None = None,
    title: str | None = None,
    detail: str | None = None,
    chunk_count: int | None = None,
    duration_ms: int | None = None,
) -> None:
    """Ghi một dòng nhật ký. Không bao giờ ném ngoại lệ."""
    # Lấy chỗ mở session từ document_approval để test chỉ phải thay một chỗ (import muộn vì document_approval cũng import module này)
    from app.services import document_approval

    db = None
    try:
        db = document_approval._open_session()
        db.add(
            IngestLog(
                # Đặt ở phía Python (có micro giây) để các dòng ghi sát nhau vẫn xếp đúng thứ tự
                created_at=datetime.now(timezone.utc),
                actor_user_id=actor_user_id[:64] if actor_user_id else None,
                actor_role=actor_role[:20] if actor_role else None,
                action=action,
                document_id=document_id,
                doc_number=doc_number[:100] if doc_number else None,
                title=title[:500] if title else None,
                detail=mask_secrets(detail)[:MAX_DETAIL_CHARS] if detail else None,
                chunk_count=chunk_count,
                duration_ms=duration_ms,
            )
        )
        db.commit()
    except Exception as e:  # noqa: BLE001 - nhật ký là việc phụ, không được làm hỏng việc chính
        logger.warning("Không ghi được nhật ký nạp (%s, văn bản %s), bỏ qua: %s", action, document_id, type(e).__name__)
        if db is not None:
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                logger.warning("Không đóng được session ghi nhật ký nạp")
