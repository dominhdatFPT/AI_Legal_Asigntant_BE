import os
from datetime import timedelta

from app.core.responses import INTERNAL_ERROR, ApiError

# Ba hằng số nghiệp vụ (docs/api/CONSULTATION_DESIGN.md mục 6), chỉ khai báo ở đây.
MAX_WAITING_PER_USER = 10
MAX_OPEN_PER_LAWYER = 3
RETENTION = timedelta(days=10)
# Tóm tắt hội thoại: im lặng đủ lâu thì tóm tắt; chat liên tục thì đủ tin cũng tóm tắt; mỗi lần gửi tối đa BATCH tin.
SUMMARY_IDLE = timedelta(minutes=10)
SUMMARY_BATCH = 50
SUMMARY_SWEEP_SECONDS = 60


def allowed_origins() -> set[str]:
    """Origin được dùng cookie mở WebSocket (CONSULTATION_ALLOWED_ORIGINS, cách nhau bằng dấu phẩy)."""
    raw = os.getenv("CONSULTATION_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    return {origin.strip() for origin in raw.split(",") if origin.strip()}


def terms_version() -> str:
    """Phiên bản điều khoản hiện hành (CONSULTATION_TERMS_VERSION). Thiếu biến thì lỗi 500, không đoán."""
    version = os.getenv("CONSULTATION_TERMS_VERSION", "").strip()
    if not version:
        raise ApiError(500, INTERNAL_ERROR)
    return version
