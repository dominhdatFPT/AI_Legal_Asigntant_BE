import hashlib
import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import ValidationError

from app.api.drafts import bearer
from app.api.generations import storage
from app.core.errors import ContractError, success
from app.core.security import contract_user
from app.repositories.personal_templates import PersonalRepository
from app.schemas.personal_templates import ImportInput, MappingInput, ConfirmInput
from app.services.document_import import DocumentImport
from app.services.personal_renderer import PersonalRenderer

router=APIRouter(prefix="/api/v1/contracts",tags=["personal templates"])


def repository():
    return PersonalRepository()


def document():
    return DocumentImport.from_env()


def renderer():
    return PersonalRenderer()


def source_bytes(row,owner,store):
    key=row["snapshot_storage_key"]
    if not key.startswith(f"snapshots/{owner}/"):
        raise ContractError("SOURCE_UNAVAILABLE","Snapshot không hợp lệ.")
    return store.read(key,row["source_sha256"],row["source_size_bytes"])


def preview_info(row,owner):
    try:
        info=row["extraction_snapshot"]["contract_import"]["preview"]
        if not info["key"].startswith(f"snapshots/{owner}/"):
            raise ValueError()
        return info
    except (KeyError,TypeError,ValueError,AttributeError):
        raise ContractError("SOURCE_UNAVAILABLE","Snapshot chưa sẵn sàng.") from None


@router.post("/user-templates",status_code=202)
def import_template(request:Request,body:ImportInput,key:UUID=Header(alias="Idempotency-Key"),user=Depends(contract_user),
    repo=Depends(repository),docs=Depends(document),store=Depends(storage),render=Depends(renderer)):
    old=repo.prior(user.id,key,body)
    if old:
        return success(request,old)
    source=docs.load(body.document_id,user.id,bearer(request),request.state.request_id)
    pdf,manifest=render.prepare(source.content,source.format)
    # Validate server suggestions against the public schema and source before making them writable.
    if manifest["fields"]:
        try:
            validated=MappingInput(expected_revision=0,fields=manifest["fields"])
        except ValidationError:
            raise ContractError("INVALID_MAPPING","Không tạo được mapping an toàn; hãy kiểm tra marker nguồn.",422) from None
        manifest["fields"]=[f.model_dump() for f in validated.fields]
    group=uuid4()
    source_key=f"snapshots/{user.id}/{group}/{uuid4()}.{source.format}"
    preview_key=f"snapshots/{user.id}/{group}/{uuid4()}.pdf"
    preview={"key":preview_key,"sha256":hashlib.sha256(pdf).hexdigest(),"size":len(pdf)}
    if len(json.dumps({"document_extraction":source.extraction,"contract_import":{"schema_version":1,"preview":preview,"manifest":manifest}},ensure_ascii=False).encode())>16000000:
        raise ContractError("DOCUMENT_BAD_RESPONSE","Snapshot extraction vượt giới hạn.",502)
    adapter=store()
    adapter.ready()
    attempted=[]
    try:
        attempted.append(source_key)
        adapter.put(source_key,source.content,source.format)
        attempted.append(preview_key)
        adapter.put(preview_key,pdf,"pdf")
        result=repo.create(user.id,key,body,source,source_key,preview,manifest)
        return success(request,result)
    finally:
        for object_key in attempted:
            try:
                if repo.can_discard(user.id,key,object_key):
                    adapter.remove(object_key)
            except ContractError:
                pass # Never delete on ambiguous commit; orphan sweep remains an operational follow-up.


@router.get("/user-templates")
def list_templates(request:Request,page:int=Query(1,ge=1,le=1000000),page_size:int=Query(20,ge=1,le=100),user=Depends(contract_user),repo=Depends(repository)):
    return success(request,repo.list(user.id,page,page_size))


@router.get("/user-templates/{template_id}")
def get_template(request:Request,template_id:UUID,user=Depends(contract_user),repo=Depends(repository)):
    return success(request,repo.get(template_id,user.id))


@router.delete("/user-templates/{template_id}",status_code=204)
def delete(template_id:UUID,user=Depends(contract_user),repo=Depends(repository)):
    repo.delete(template_id,user.id)
    return Response(status_code=204)


@router.get("/template-analyses/{analysis_id}")
def analysis(request:Request,analysis_id:UUID,user=Depends(contract_user),repo=Depends(repository)):
    return success(request,repo.analysis(analysis_id,user.id))


def validated(row,owner,fields,store,render):
    info=preview_info(row,owner)
    adapter=store()
    adapter.ready()
    pdf=adapter.read(info["key"],info["sha256"],info["size"])
    render.validate_mapping(fields,row["extraction_snapshot"]["contract_import"]["manifest"],row["source_format"],pdf)


@router.put("/user-templates/{template_id}/mapping")
def mapping(request:Request,template_id:UUID,body:MappingInput,user=Depends(contract_user),repo=Depends(repository),store=Depends(storage),render=Depends(renderer)):
    row=repo.source(template_id,user.id)
    if row["mapping_revision"]!=body.expected_revision or row["version_status"]!="draft":
        raise ContractError("REVISION_CONFLICT","Tải lại mapping trước khi sửa.",409)
    validated(row,user.id,[f.model_dump() for f in body.fields],store,render)
    return success(request,repo.mapping(template_id,user.id,body))


@router.post("/user-templates/{template_id}/confirm")
def confirm(request:Request,template_id:UUID,body:ConfirmInput,user=Depends(contract_user),repo=Depends(repository),store=Depends(storage),render=Depends(renderer)):
    row=repo.source(template_id,user.id)
    if row["mapping_revision"]!=body.expected_revision or row["version_status"]!="draft":
        raise ContractError("REVISION_CONFLICT","Tải lại mapping trước khi xác nhận.",409)
    if not row["fields"]:
        raise ContractError("INVALID_MAPPING","Cần chọn ít nhất một trường điền.",422)
    validated(row,user.id,row["fields"],store,render)
    return success(request,repo.confirm(template_id,user.id,body.expected_revision))


@router.get("/user-templates/{template_id}/source-preview")
def source_preview(request:Request,template_id:UUID,user=Depends(contract_user),repo=Depends(repository),store=Depends(storage)):
    row=repo.source(template_id,user.id)
    info=preview_info(row,user.id)
    return success(request,store().download(info["key"]))
