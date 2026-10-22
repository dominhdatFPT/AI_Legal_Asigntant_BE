from fastapi import APIRouter, Depends, FastAPI, Request

from common_auth import CurrentUser

from app.core.errors import install_error_handlers, success
from app.core.security import document_user
from app.api.files import router as files_router
from app.api.internal_files import router as internal_files_router
from app.api.identity_ocr import router as identity_router
from app.api.identity_profile import router as profile_router
from app.api.processing import router as processing_router


app = FastAPI(title="Document Service", version="0.9.0")
install_error_handlers(app)
authenticated_router = APIRouter(prefix="/api/v1/documents", dependencies=[Depends(document_user)])


@app.get("/api/v1/documents/health")
def health():
    return {"status": "ok", "service": "document-service"}


@authenticated_router.get("/me")
def auth_context(request: Request, user: CurrentUser = Depends(document_user)):
    """JWT diagnostic only; not a user profile or DB/storage readiness check."""
    return success(request, {"user_id": user.id, "role": user.role})


authenticated_router.include_router(identity_router)
authenticated_router.include_router(profile_router)
authenticated_router.include_router(processing_router)
authenticated_router.include_router(files_router)
app.include_router(authenticated_router)
app.include_router(internal_files_router)
