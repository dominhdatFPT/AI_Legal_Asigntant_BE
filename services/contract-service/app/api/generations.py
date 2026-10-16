from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request

from app.api.drafts import bearer, references
from app.core.errors import ContractError, success
from app.core.security import contract_user
from app.repositories.generations import GenerationRepository
from app.schemas.generations import GenerationInput
from app.services.contract_renderer import ContractRenderer
from app.services.contract_storage import ContractStorage

router=APIRouter(prefix="/api/v1/contracts",tags=["contracts"])


def repository():
    return GenerationRepository()


def storage():
    # Lazy construction: owner is checked before touching config or Storage.
    return ContractStorage.from_env


def renderer():
    return ContractRenderer()


def enqueue(request,draft_id,user,key,kind,format,repo,refs,store_factory,render):
    context,prior=repo.admit_generation(draft_id,user.id,key,kind,format)
    if prior:
        return success(request,prior)
    refs.ensure(user.id,bearer(request),request.state.request_id,context["draft"]["document_ids"])
    store_factory().ready()
    render.ready(format)
    return success(request,repo.enqueue(draft_id,user.id,key,kind,format,context["revision"]))


@router.post("/drafts/{draft_id}/generations",status_code=202)
def generate(request:Request,draft_id:UUID,body:GenerationInput,key:UUID=Header(alias="Idempotency-Key"),
    user=Depends(contract_user),repo=Depends(repository),refs=Depends(references),store=Depends(storage),render=Depends(renderer)):
    return enqueue(request,draft_id,user,key,"final",body.format,repo,refs,store,render)


@router.post("/drafts/{draft_id}/preview",status_code=202)
async def preview(request:Request,draft_id:UUID,key:UUID=Header(alias="Idempotency-Key"),
    user=Depends(contract_user),repo=Depends(repository),refs=Depends(references),store=Depends(storage),render=Depends(renderer)):
    if await request.body():
        raise ContractError("VALIDATION_ERROR","Preview không nhận request body.",422)
    # Run blocking DB/HTTP off the event loop.
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(enqueue,request,draft_id,user,key,"preview","pdf",repo,refs,store,render)


@router.get("/drafts/{draft_id}/generations")
def list_jobs(request:Request,draft_id:UUID,page:int=Query(1,ge=1,le=1000000),page_size:int=Query(20,ge=1,le=100),user=Depends(contract_user),repo=Depends(repository)):
    return success(request,repo.list_generations(draft_id,user.id,page,page_size))


@router.get("/generations/{generation_id}")
def get_job(request:Request,generation_id:UUID,user=Depends(contract_user),repo=Depends(repository)):
    return success(request,repo.get_generation(generation_id,user.id))


@router.get("/generations/{generation_id}/download")
def download(request:Request,generation_id:UUID,user=Depends(contract_user),repo=Depends(repository),store=Depends(storage)):
    result=repo.result(generation_id,user.id)
    if not result["storage_key"].startswith(f"results/{user.id}/{generation_id}/"):
        raise ContractError("RESULT_UNAVAILABLE","Metadata file không hợp lệ.")
    return success(request,store().download(result["storage_key"]))
