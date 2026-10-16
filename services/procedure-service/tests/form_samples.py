from app.services.procedure_form_service import get_procedure_form_schema

# Giá trị khớp các validation_regex đang có trong schema.
REGEX_SAMPLES = {"^[0-9]{10,11}$": "0901234567", "^[0-9]{9,12}$": "012345678901"}


def valid_form(code: str, **overrides) -> dict:
    """form_data hợp lệ: điền mọi ô bắt buộc của schema, rồi ghi đè bằng overrides."""
    data = {}
    for f in get_procedure_form_schema(code).fields:
        if not f.required:
            continue
        kind = f.field_type.value
        if f.options:
            data[f.name] = f.options[0].value
        elif kind == "checkbox":
            data[f.name] = True
        elif kind == "number":
            data[f.name] = 1
        elif kind == "date":
            data[f.name] = "2000-01-01"
        else:
            data[f.name] = REGEX_SAMPLES.get(f.validation_regex, "Giá trị mẫu")
    return {**data, **overrides}
