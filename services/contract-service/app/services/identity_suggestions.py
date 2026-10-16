"""Deterministic, explicit, review-only identity suggestions. Never writes a draft."""
from app.core.errors import ContractError
from app.schemas.identity_suggestions import DATE_KEYS

INSTRUCTIONS = {
    "already_filled": "Giữ nguyên thông tin bạn đã nhập; hệ thống không gợi ý ghi đè.",
    "manual_only": "Bạn tự nhập thông tin của đúng bên theo thỏa thuận; hệ thống không tự suy đoán mục này.",
    "profile_missing": "Bạn nhập tay hoặc xác nhận hồ sơ CCCD ở Document trước khi lấy gợi ý.",
    "identity_missing": "Hồ sơ CCCD chưa có thông tin này; bạn kiểm tra giấy tờ và tự nhập.",
}


def filled(values, name):
    # 0, false and whitespace count as entered; do not use truthiness here.
    return name in values and values[name] is not None and values[name] != ""


class IdentitySuggestions:
    def __init__(self, repo, provider):
        self.repo, self.provider = repo, provider

    def suggest(self, draft_id, owner, token, request_id, body):
        context = self.repo.suggestion_context(draft_id, owner)
        fields = {field["name"]: field for field in context["fields"]}
        bindings = {}
        for binding in body.bindings:
            field = fields.get(binding.field_name)
            expected_type = "date" if binding.identity_key in DATE_KEYS else "text"
            if field is None or field.get("identity_key") != binding.identity_key or field["type"] != expected_type:
                raise ContractError("VALIDATION_ERROR", "Mapping phải khớp trường CCCD đã khai báo và đúng kiểu dữ liệu.", 422,
                    details=[{"field":"bindings", "message":"Kiểm tra trường và identity_key của version mẫu."}])
            bindings[binding.field_name] = binding.identity_key
        needed = any(not filled(context["draft"]["field_values"], name) for name in bindings)
        profile = self.provider.read(owner, token, request_id) if needed else None
        values = profile.fields.model_dump() if profile else {}
        suggestions, manual = [], []
        for field in context["fields"]:
            name = field["name"]
            key = bindings.get(name)
            if filled(context["draft"]["field_values"], name):
                reason = "already_filled"
            elif key is None:
                reason = "manual_only"
            elif profile is None:
                reason = "profile_missing"
            elif values[key] is None:
                reason = "identity_missing"
            else:
                suggestions.append({"field_name":name, "identity_key":key, "value":values[key], "is_review_required":True})
                continue
            manual.append({"field_name":name, "reason":reason, "instruction":INSTRUCTIONS[reason]})
        # Recheck after the HTTP call; no transaction is held while contacting Document.
        self.repo.assert_suggestion_current(draft_id, owner, context["revision"])
        return {"draft_id":context["draft"]["id"], "draft_updated_at":context["draft"]["updated_at"],
            "identity_profile_revision":profile.revision if profile else None,
            "suggestions":suggestions, "manual_fields":manual,
            "warnings":["Chưa có hồ sơ CCCD đã xác nhận; bạn vẫn có thể nhập tay."] if needed and profile is None else []}
