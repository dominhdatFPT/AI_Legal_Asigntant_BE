from fastapi import APIRouter, Depends, FastAPI, Request

from common_auth import CurrentUser
from app.core.errors import install_handlers, success
from app.core.security import contract_user
from app.core.database import database_readiness
from app.api.catalog import router as catalog_router
from app.api.drafts import router as drafts_router
from app.api.identity_suggestions import router as identity_suggestions_router
from app.api.generations import router as generations_router
from app.api.personal_templates import router as personal_templates_router
from app.api.reference_catalog import router as reference_catalog_router


app = FastAPI(title="Contract Service", version="0.8.1")
install_handlers(app)

# API nghiệp vụ đặt trong router này để mặc định cần access token.
# Health đặt ngoài router vì Docker phải kiểm tra được khi chưa đăng nhập.
authenticated_router = APIRouter(
    prefix="/api/v1/contracts", dependencies=[Depends(contract_user)]
)


@app.get("/api/v1/contracts/health")
def health():
    return {"status": "ok", "service": "contract-service"}


@authenticated_router.get("/me")
def auth_context(
    request: Request,
    user: CurrentUser = Depends(contract_user),
):
    """Kiểm tra JWT đã gắn; không truy vấn hồ sơ hay database."""
    return success(request, {"user_id": user.id, "role": user.role})


@authenticated_router.get("/ready")
def readiness(request: Request):
    """Authenticated DB/schema check only; health remains a liveness probe."""
    return success(request, database_readiness())


@authenticated_router.get("/templates")
def list_templates(request: Request):
    return success(request, {
        "items": [
            {
                "id": "CONTRACT-001",
                "name": "Hợp đồng thuê nhà",
                "version": 1,
                "fields": [
                    {
                        "name": "landlord_name",
                        "label": "Tên bên cho thuê",
                        "type": "text",
                        "required": True,
                    }
                ],
            }
        ]
    })


app.include_router(authenticated_router)
app.include_router(catalog_router)
app.include_router(drafts_router)
app.include_router(identity_suggestions_router)
app.include_router(generations_router)
app.include_router(personal_templates_router)
app.include_router(reference_catalog_router)
