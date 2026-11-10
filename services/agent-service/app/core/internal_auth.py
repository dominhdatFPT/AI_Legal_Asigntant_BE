"""Xác thực API nội bộ /internal/v1/...: header X-Internal-Token phải khớp INTERNAL_TOKEN (cùng cách kiểm tra của lawyer-service)."""
import hmac

from fastapi import HTTPException, Request

from app.core.config import get_settings


def require_internal_token(request: Request) -> None:
    expected = get_settings().internal_token
    given = request.headers.get("X-Internal-Token", "")
    # Chưa cấu hình INTERNAL_TOKEN thì chặn hết, không bao giờ mở cửa.
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "Thiếu hoặc sai mã xác thực nội bộ."},
        )
