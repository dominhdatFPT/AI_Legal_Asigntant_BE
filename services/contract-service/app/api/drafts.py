from uuid import UUID

from common_auth.core import ACCESS_COOKIE, extract_token
from fastapi import APIRouter, Depends, Query, Request, Response

from app.core.errors import success
from app.core.security import contract_user, dossier_creator
from app.repositories.drafts import DraftRepository
from app.schemas.drafts import DraftInput, DraftPatch
from app.services.draft_references import DraftReferences

router = APIRouter(prefix="/api/v1/contracts", tags=["contracts"])


def repository():
    return DraftRepository()


def references():
    return DraftReferences.from_env()


def bearer(request):
    return extract_token(request.headers.get("Authorization"), request.cookies.get(ACCESS_COOKIE))


@router.post("/drafts", status_code=201)
def create(request: Request, body: DraftInput, user=Depends(dossier_creator), repo=Depends(repository), refs=Depends(references)):
    repo.admit(body, user.id)
    refs.ensure(user.id, bearer(request), request.state.request_id, body.document_ids, body.procedure_id)
    return success(request, repo.create(body, user.id))


@router.get("/drafts")
def list_drafts(request: Request, page: int = Query(1, ge=1, le=1000000), page_size: int = Query(20, ge=1, le=100),
    user=Depends(contract_user), repo=Depends(repository)):
    return success(request, repo.list(user.id, page, page_size))


@router.get("/drafts/{draft_id}")
def get(request: Request, draft_id: UUID, user=Depends(contract_user), repo=Depends(repository)):
    return success(request, repo.get(draft_id, user.id))


@router.patch("/drafts/{draft_id}")
def update(request: Request, draft_id: UUID, body: DraftPatch, user=Depends(contract_user), repo=Depends(repository), refs=Depends(references)):
    # Check owner before making upstream calls (no oracle for another user's links).
    repo.admit_update(draft_id, body, user.id)
    if "document_ids" in body.model_fields_set:
        refs.ensure(user.id, bearer(request), request.state.request_id, body.document_ids)
    return success(request, repo.update(draft_id, body, user.id))


@router.delete("/drafts/{draft_id}", status_code=204)
def delete(draft_id: UUID, user=Depends(contract_user), repo=Depends(repository)):
    repo.delete(draft_id, user.id)
    return Response(status_code=204)
