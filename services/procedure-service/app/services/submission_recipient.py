"""Recognize legacy submission-recipient fields by block, key, or label."""
import re
import unicodedata

RECIPIENT_KEYS = {
    "receiving_agency", "receiving_agency_level", "receiving_authority", "survey_receiving_agency",
    "mediation_commune_committee", "licensing_authority", "recipient_unit", "recipient_units",
    "submission_recipient", "addressee", "kinh_gui", "noi_tiep_nhan", "noi_tiep_nhan_ho_so",
    "co_quan_tiep_nhan", "co_quan_tiep_nhan_ho_so", "sel_city", "sel_district",
}
RECIPIENT_LABEL = re.compile(
    r"^(?:kinh_gui|(?:noi|don_vi)_tiep_nhan|noi_nhan_don|noi_nop_ho_so|"
    r"co_quan_nhan_ho_so|co_quan(?:_[a-z0-9]+)*_tiep_nhan)(?:_|$)"
    r"|^co_quan_dang_ky_kinh_doanh_cap_(?:xa|tinh)$"
)
PERSON_RECIPIENT_LABEL = re.compile(r"^(?:nguoi|ten_nguoi|dia_chi|email|so_dien_thoai)(?:_|$)")


def _identifier(value: object) -> str:
    decomposed = unicodedata.normalize("NFD", str(value or "").lower().replace("đ", "d"))
    unaccented = "".join(char for char in decomposed if not unicodedata.category(char).startswith("M"))
    return re.sub(r"[^a-z0-9]+", "_", unaccented).strip("_")


def is_submission_recipient(field: dict, block_key: str | None = None) -> bool:
    if block_key in ("receiving_agency", "field_receiving_agency"):
        return True
    key = _identifier(str(field.get("key") or field.get("name") or "").rsplit(".", 1)[-1])
    word_key = _identifier(field.get("word_key"))
    label = _identifier(field.get("label"))
    if word_key == "requester_signer" or label == "co_quan_dia_diem_luu_van_ban":
        return False
    return (
        key in RECIPIENT_KEYS
        or word_key in RECIPIENT_KEYS
        or bool(RECIPIENT_LABEL.search(label))
        or (key == "recipient" and word_key in ("", "recipient") and not PERSON_RECIPIENT_LABEL.match(label))
    )


def optional_template_recipients(content: dict) -> dict:
    """Relax public template metadata while preserving paths and Word markers."""
    def optional_fields(container: dict) -> dict:
        fields = container.get("fields")
        if not fields:
            return container
        return {
            **container,
            "fields": [
                {**field, "required": False}
                if field.get("required") and is_submission_recipient(field, container.get("block_key"))
                else field
                for field in fields
            ],
        }

    result = dict(content)
    if "sections" in content:
        result["sections"] = [
            {**section, "blocks": [optional_fields(block) for block in section.get("blocks", [])]}
            for section in content["sections"]
        ]
    if "repeat_groups" in content:
        result["repeat_groups"] = [optional_fields(group) for group in content["repeat_groups"]]
    return result
