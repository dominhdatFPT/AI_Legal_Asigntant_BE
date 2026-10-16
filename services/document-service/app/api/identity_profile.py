import json
import re

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.core.config import ConfigurationError, Settings
from app.core.errors import DocumentError, success
from app.core.security import document_user, document_writer
from app.repositories.database import Database
from app.repositories.identity_profile import IdentityProfile
from app.schemas.identity_profile import ConfirmIdentityProfile

router = APIRouter()
MAX_PROFILE_JSON = 16384

# Keep Swagger usable without bypassing the bounded/duplicate-key JSON reader.
PROFILE_REQUEST_SCHEMA = ConfirmIdentityProfile.model_json_schema()
PROFILE_REQUEST_SCHEMA["properties"]["fields"] = PROFILE_REQUEST_SCHEMA.pop("$defs")["IdentityFields"]
PROFILE_REQUEST_SCHEMA["oneOf"] = [
    {"required": [key], "properties": {key: {"type": "string", "format": "uuid"}}}
    for key in ("source_session_id", "source_document_id")
]


def get_identity_profile():
    try:
        return IdentityProfile(Database(Settings.from_env()))
    except ConfigurationError:
        raise DocumentError("CONFIGURATION_ERROR", "Cấu hình Document chưa sẵn sàng.") from None


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("Duplicate key")
        obj[key] = value
    return obj


@router.get("/identity-profile/me")
def get_profile(request: Request, user=Depends(document_user), service=Depends(get_identity_profile)):
    return success(request, service.get(user.id))


@router.put("/identity-profile/me", openapi_extra={"requestBody": {
    "required": True, "content": {"application/json": {"schema": PROFILE_REQUEST_SCHEMA}}}})
async def confirm_profile(request: Request, user=Depends(document_writer), service=Depends(get_identity_profile)):
    if request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise DocumentError("BAD_REQUEST", "Xác nhận hồ sơ phải gửi application/json.", 400)
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_PROFILE_JSON:
            raise DocumentError("FILE_TOO_LARGE", "JSON hồ sơ vượt giới hạn 16 KiB.", 413)
        content.extend(chunk)
    try:
        payload = json.loads(content, object_pairs_hook=unique_object,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, RecursionError):
        raise DocumentError("BAD_REQUEST", "JSON hồ sơ không hợp lệ.", 400) from None
    try:
        body = ConfirmIdentityProfile.model_validate(payload)
    except ValidationError as exc:
        raise RequestValidationError([{**value, "loc": ("body", *value["loc"])} for value in exc.errors()]) from None
    return success(request, await run_in_threadpool(service.confirm, user.id, body))


@router.delete("/identity-profile/me", status_code=204)
def unlink_profile(request: Request, expected_revision: int = Query(..., ge=1, le=2147483647),
                   user=Depends(document_user), service=Depends(get_identity_profile)):
    revisions = request.query_params.getlist("expected_revision")
    if len(revisions) != 1 or not re.fullmatch(r"[1-9][0-9]*", revisions[0]):
        raise DocumentError("VALIDATION_ERROR", "expected_revision phải là số nguyên dương duy nhất.", 422)
    service.unlink(user.id, expected_revision)
    return Response(status_code=204)
