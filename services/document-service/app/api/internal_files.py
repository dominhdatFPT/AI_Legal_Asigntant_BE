"""API nội bộ (service gọi service, header X-Internal-Token). Gateway không chuyển tiếp /internal/...

Lawyer Service đọc giấy tờ của người xin làm luật sư để admin xem. Vẫn kiểm tra chủ sở hữu và trạng thái quét
như API công khai: chỉ trả file thuộc `owner_user_id` và đã `available`.
"""

import hmac
import os
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.api.files import get_files
from app.core.downloads import content_disposition
from app.core.errors import DocumentError


def require_internal_token(request: Request) -> None:
    expected = os.getenv("INTERNAL_TOKEN", "")
    # Chưa cấu hình INTERNAL_TOKEN thì chặn hết, không bao giờ mở cửa.
    if not expected or not hmac.compare_digest(request.headers.get("X-Internal-Token", ""), expected):
        raise DocumentError("UNAUTHORIZED", "Thiếu hoặc sai X-Internal-Token.", 401)


router = APIRouter(prefix="/internal/v1/documents", dependencies=[Depends(require_internal_token)])


@router.get("/{document_id}/content")
def internal_content(document_id: UUID, owner_user_id: UUID, files=Depends(get_files)):
    content, media_type, filename, digest = files.content(str(owner_user_id), document_id)
    return Response(content=content, media_type=media_type, headers={
        "Content-Disposition": content_disposition(filename), "X-Content-SHA256": digest,
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})
