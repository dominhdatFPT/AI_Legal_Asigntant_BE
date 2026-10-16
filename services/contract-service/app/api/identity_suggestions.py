from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.drafts import bearer, repository
from app.core.errors import success
from app.core.security import contract_user
from app.schemas.identity_suggestions import SuggestionInput
from app.services.confirmed_identity import ConfirmedIdentity
from app.services.identity_suggestions import IdentitySuggestions

router = APIRouter(prefix="/api/v1/contracts", tags=["contracts"])


def provider():
    return ConfirmedIdentity.from_env()


@router.post("/drafts/{draft_id}/identity-suggestions")
def suggest(request: Request, draft_id: UUID, body: SuggestionInput, user=Depends(contract_user), repo=Depends(repository), identity=Depends(provider)):
    result = IdentitySuggestions(repo, identity).suggest(draft_id, user.id, bearer(request), request.state.request_id, body)
    return success(request, result)
