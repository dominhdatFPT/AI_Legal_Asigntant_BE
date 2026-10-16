"""API nội bộ cho Lawyer Service (X-Internal-Token): chủ + JSON OCR của ảnh thẻ, link xem ảnh cho admin duyệt đơn.
Gateway không chuyển tiếp /internal/..."""

import hmac
import os
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.lawyer_card_ocr import get_lawyer_cards
from app.core.errors import DocumentError, success


def require_internal_token(request: Request) -> None:
    expected = os.getenv("INTERNAL_TOKEN", "")
    given = request.headers.get("X-Internal-Token", "")
    # Chưa cấu hình INTERNAL_TOKEN thì chặn hết, không bao giờ mở cửa.
    if not expected or not hmac.compare_digest(given, expected):
        raise DocumentError("UNAUTHORIZED", "Thiếu hoặc sai X-Internal-Token.", 401)


router = APIRouter(prefix="/internal/v1/documents/lawyer-cards", dependencies=[Depends(require_internal_token)])


@router.get("/{card_id}")
def lawyer_card_internal(card_id: UUID, request: Request, cards=Depends(get_lawyer_cards)):
    return success(request, cards.internal(card_id))


@router.post("/{card_id}/view-url")
def lawyer_card_view_url(card_id: UUID, request: Request, cards=Depends(get_lawyer_cards)):
    return success(request, cards.view_url(card_id))
