from typing import Literal

from app.schemas.catalog import Payload
from app.schemas.drafts import validate_values
from app.core.errors import ContractError


class GenerationInput(Payload):
    format: Literal["docx", "pdf"]


def validate_generation(context, kind, format=None):
    version = context["version"]
    # C4 is explicitly a plain-text template catalog. Binary mapping belongs to C8.
    extraction = version.get("extraction_snapshot") or {}
    personal = version["kind"] == "personal" and extraction.get("contract_import", {}).get("schema_version") == 1 and version["snapshot_storage_key"] is not None
    from app.services.catalog_source import catalog_manifest, validate_catalog
    catalog = catalog_manifest(version) is not None and version.get("snapshot_storage_key") is not None
    if not personal and not catalog and (version["kind"] != "system" or version["source_format"] != "docx" or version["snapshot_storage_key"] is not None):
        raise ContractError("SOURCE_RENDERER_NOT_SUPPORTED", "Mẫu file gốc cần luồng import/mapping C8; chưa thể xuất bằng renderer văn bản C7.", 422)
    if (personal or catalog) and version["source_format"] == "pdf" and format == "docx":
        raise ContractError("SOURCE_FORMAT_NOT_SUPPORTED", "Mẫu PDF chỉ xuất PDF giữ nền, không tự chuyển sang Word.", 422)
    validate_values(context["draft"]["field_values"], context["fields"])
    missing = [f["name"] for f in context["fields"] if f["required"] and (
        context["draft"]["field_values"].get(f["name"]) is None or
        isinstance(context["draft"]["field_values"].get(f["name"]), str) and not context["draft"]["field_values"][f["name"]].strip())]
    if kind == "final" and missing:
        raise ContractError("VALIDATION_ERROR", "Cần điền đủ các trường bắt buộc trước khi xuất.", 422,
            details=[{"field": name, "message": "Trường bắt buộc chưa điền."} for name in missing])
    if kind == "final" and context["draft"]["procedure_id"] is not None:
        raise ContractError("PROCEDURE_CHECKLIST_NOT_READY", "Checklist thủ tục chưa chốt; chưa thể xuất bản cuối cho nháp gắn thủ tục.", 422)
    if catalog:
        validate_catalog(version, context["draft"]["field_values"], final=kind=="final")
    return ["MISSING_REQUIRED:" + name for name in missing]
