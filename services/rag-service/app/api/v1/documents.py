from fastapi import APIRouter, Depends
from app.api.deps import require_admin, get_current_user
from app.core.auth import TokenPayload

router = APIRouter(prefix="/api/v1/rag/documents", tags=["documents"])

# TODO: implement document management endpoints
# POST   /  → upload văn bản pháp luật (admin only)
# GET    /  → danh sách tài liệu
# GET    /{id}  → chi tiết tài liệu
# PATCH  /{id}  → cập nhật metadata
# DELETE /{id}  → xoá tài liệu (admin only)
# POST   /{id}/reprocess  → chunk + embed lại (admin only)
