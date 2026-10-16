from uuid import UUID
from fastapi import APIRouter, Depends, Request
from app.core.config import ConfigurationError, Settings
from app.core.errors import DocumentError, success
from app.core.security import document_user
from app.repositories.database import Database
from app.repositories.processing import Processing

router = APIRouter()


def get_processing():
    try:
        return Processing(Database(Settings.from_env()))
    except ConfigurationError:
        raise DocumentError("CONFIGURATION_ERROR", "Cấu hình Document chưa sẵn sàng.") from None


@router.get("/{document_id}/processing")
def status(request: Request, document_id: UUID, user=Depends(document_user), service=Depends(get_processing)):
    return success(request, service.status(user.id, document_id))


@router.get("/{document_id}/extraction")
def extraction(request: Request, document_id: UUID, user=Depends(document_user), service=Depends(get_processing)):
    return success(request, service.extraction(user.id, document_id))
