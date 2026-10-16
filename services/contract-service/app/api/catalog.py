from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from common_auth import CurrentUser

from app.core.errors import ContractError, success
from app.core.security import catalog_admin, catalog_user
from app.repositories.catalog import CatalogRepository
from app.schemas.catalog import TemplateInput, TemplatePatch, VersionInput

router = APIRouter(prefix="/api/v1/contracts", tags=["contracts"])


def repository():
    return CatalogRepository()


@router.get("/template-catalog")
def list_catalog(request: Request, page: int = Query(1, ge=1, le=1000000), page_size: int = Query(20, ge=1, le=100),
    include_hidden: bool = False, user: CurrentUser = Depends(catalog_user), repo=Depends(repository)):
    if include_hidden and user.role != "admin":
        raise ContractError("FORBIDDEN", "Chỉ admin được xem mẫu ẩn.", 403)
    return success(request, repo.list_templates(user.role == "admin", include_hidden, page, page_size))


@router.post("/template-catalog", status_code=201)
def create_template(request: Request, body: TemplateInput, user=Depends(catalog_admin), repo=Depends(repository)):
    return success(request, repo.create_template(body))


@router.get("/template-catalog/{template_id}")
def get_template(request: Request, template_id: UUID, user=Depends(catalog_user), repo=Depends(repository)):
    return success(request, repo.get_template(template_id, user.role == "admin"))


@router.patch("/template-catalog/{template_id}")
def patch_template(request: Request, template_id: UUID, body: TemplatePatch, user=Depends(catalog_admin), repo=Depends(repository)):
    return success(request, repo.update_template(template_id, body))


@router.delete("/template-catalog/{template_id}", status_code=204)
def delete_template(template_id: UUID, user=Depends(catalog_admin), repo=Depends(repository)):
    repo.delete_template(template_id)
    return Response(status_code=204)


@router.get("/template-catalog/{template_id}/versions")
def list_versions(request: Request, template_id: UUID, page: int = Query(1, ge=1, le=1000000), page_size: int = Query(20, ge=1, le=100),
    user=Depends(catalog_user), repo=Depends(repository)):
    return success(request, repo.list_versions(template_id, user.role == "admin", page, page_size))


@router.post("/template-catalog/{template_id}/versions", status_code=201)
def create_version(request: Request, template_id: UUID, body: VersionInput, user=Depends(catalog_admin), repo=Depends(repository)):
    return success(request, repo.create_version(template_id, body))


@router.get("/template-versions/{version_id}")
def get_version(request: Request, version_id: UUID, user=Depends(catalog_user), repo=Depends(repository)):
    return success(request, repo.get_version(version_id, user.role == "admin", user.id))


@router.patch("/template-versions/{version_id}")
def patch_version(request: Request, version_id: UUID, body: VersionInput, user=Depends(catalog_admin), repo=Depends(repository)):
    return success(request, repo.update_version(version_id, body))


@router.delete("/template-versions/{version_id}", status_code=204)
def delete_version(version_id: UUID, user=Depends(catalog_admin), repo=Depends(repository)):
    repo.delete_version(version_id)
    return Response(status_code=204)


@router.post("/template-versions/{version_id}/publish")
def publish(request: Request, version_id: UUID, user=Depends(catalog_admin), repo=Depends(repository)):
    return success(request, repo.publish_version(version_id))
