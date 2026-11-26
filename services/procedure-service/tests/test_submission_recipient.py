import json
from io import BytesIO
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import text

from app.main import app
from app.services.pdf_export_service import generate_application_dossier_pdf
from app.services.procedure_form_service import get_entry_form_schema, get_procedure_form_schema
from app.services.submission_recipient import optional_template_recipients
from form_samples import valid_form
from tests.test_mau18_api import payload as mau18_payload

client = TestClient(app)
root = Path(__file__).resolve().parents[1]
base = "/api/v1/procedures"


@pytest.mark.parametrize(
    ("code", "key"),
    [
        ("DD-001", "receiving_agency"),
        ("DD-006", "receiving_agency"),
        ("DD-013", "receiving_authority"),
        ("DD-014", "mediation_commune_committee"),
        ("DD-015", "survey_receiving_agency"),
        ("DD-018", "licensing_authority"),
        ("DD-019", "licensing_authority"),
        ("DD-021", "licensing_authority"),
        ("HKD-003", "receiving_authority"),
    ],
)
def test_static_field_is_hidden_but_valid_for_old_clients(code, key):
    full = get_procedure_form_schema(code)
    field = next(field for field in full.fields if field.name == key)
    assert field.required is False and field.default_value is None

    entry = get_entry_form_schema(code)
    assert key not in {field.name for field in entry.fields}
    assert {section.key for section in entry.sections} == {field.section for field in entry.fields}


def test_static_schema_creation_draft_submit_and_legacy_pdf(auth):
    headers = auth()
    schema = client.get(f"{base}/form-schema/DD-001")
    assert schema.status_code == 200, schema.text
    assert all(field["name"] != "receiving_agency" for field in schema.json()["data"]["fields"])
    assert all(section["key"] != "receiving" for section in schema.json()["data"]["sections"])

    form_data = valid_form("DD-001")
    assert "receiving_agency" not in form_data
    draft = client.post(f"{base}/applications", headers=headers, json={
        "procedure_code": "DD-001", "status": "draft", "form_data": form_data,
    })
    assert draft.status_code == 200, draft.text
    record_id = draft.json()["data"]["id"]
    submitted = client.post(f"{base}/applications/land/{record_id}/submit", headers=headers)
    assert submitted.status_code == 200, submitted.text

    legacy_office = "Legacy Receiving Office"
    legacy = client.post(f"{base}/applications", headers=headers, json={
        "procedure_code": "DD-001", "status": "submitted",
        "form_data": {**form_data, "receiving_agency": legacy_office},
    })
    assert legacy.status_code == 200, legacy.text
    record = legacy.json()["data"]
    old_pdf = client.get(f"{base}/applications/lookup/{record['application_code']}/export-pdf", headers=headers)
    assert old_pdf.status_code == 200, old_pdf.text
    assert legacy_office in PdfReader(BytesIO(old_pdf.content)).pages[0].extract_text()

    new_pdf = generate_application_dossier_pdf({"record": {"form_data": form_data}})
    page = PdfReader(BytesIO(new_pdf)).pages[0].extract_text()
    assert "Kính gửi" not in page
    assert "Chi nhánh Văn phòng Đăng ký Đất đai" not in page


def test_published_dynamic_form_hides_legacy_block_and_accepts_both_payloads(auth):
    admin_path = f"{base}/admin/dynamic-form-templates"
    admin = auth(role="admin")
    content = {
        "name": "Đơn kiểm tra cơ quan tiếp nhận", "code": "DD-001/TEST",
        "domain": "dat_dai", "procedure_id": "DD-001",
        "sections": [
            {
                "id": "sec_1", "title": "Cơ quan tiếp nhận", "order": 1,
                "blocks": [{
                    "instance_id": "receiving_agency_1", "block_key": "receiving_agency", "order": 1,
                    "fields": [{"key": "unit", "label": "Đơn vị", "type": "text", "required": True}],
                }],
            },
            {
                "id": "sec_2", "title": "Nội dung đề nghị", "order": 2,
                "blocks": [{
                    "instance_id": "free_1", "block_key": "free_field", "order": 1,
                    "fields": [{"key": "value", "label": "Nội dung", "type": "text", "required": True}],
                }],
            },
        ],
    }
    draft = client.post(admin_path, headers=admin, json=content)
    assert draft.status_code == 201, draft.text
    template_id = draft.json()["data"]["id"]

    def recipient_required(item):
        return item["content"]["sections"][0]["blocks"][0]["fields"][0]["required"]

    def listed_item(response):
        assert response.status_code == 200, response.text
        return next(item for item in response.json()["data"]["items"] if item["id"] == template_id)

    admin_item = listed_item(client.get(admin_path, headers=admin))
    detail = client.get(f"{admin_path}/{template_id}", headers=admin)
    assert detail.status_code == 200, detail.text
    assert admin_item["content"] == detail.json()["data"]["content"]
    assert recipient_required(admin_item) is True

    saved = client.put(f"{admin_path}/{template_id}", headers=admin, json=admin_item["content"])
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["content"] == admin_item["content"]
    assert recipient_required(listed_item(client.get(admin_path, headers=admin))) is True

    published = client.post(f"{admin_path}/{template_id}/publish", headers=admin)
    assert published.status_code == 200, published.text
    public_path = f"{base}/dynamic-form-templates"
    public = client.get(f"{public_path}/{template_id}")
    assert public.status_code == 200, public.text
    public_item = listed_item(client.get(public_path))
    assert public.json()["data"]["content"] == public_item["content"]
    assert recipient_required(public_item) is False
    assert recipient_required(listed_item(client.get(admin_path, headers=admin))) is True
    admin_detail = client.get(f"{admin_path}/{template_id}", headers=admin)
    assert recipient_required(admin_detail.json()["data"]) is True

    entry = client.get(f"{base}/DD-001/form-schema").json()["data"]
    assert [field["name"] for field in entry["fields"]] == ["free_1.value"]
    assert [section["key"] for section in entry["sections"]] == ["sec_2"]
    for values in (
        {"free_1.value": "Đề nghị"},
        {"free_1.value": "Đề nghị", "receiving_agency_1.unit": "Giá trị cũ"},
    ):
        response = client.post(f"{base}/applications", headers=auth(), json={
            "procedure_code": "DD-001", "status": "submitted", "form_data": values,
        })
        assert response.status_code == 200, response.text


def test_builtin_recipient_metadata_keeps_archive_and_signer_fields():
    changes = []
    for path in sorted((root / "app/builtin_forms").glob("*.json")):
        content = json.loads(path.read_text(encoding="utf-8"))
        public = optional_template_recipients(content)
        for before, after in zip(content["sections"], public["sections"], strict=True):
            for old_block, new_block in zip(before["blocks"], after["blocks"], strict=True):
                for old, new in zip(old_block["fields"], new_block["fields"], strict=True):
                    assert old["word_key"] == new["word_key"]
                    if old["word_key"] in {"requester_signer", "receiving_authority"}:
                        assert old == new
                    if old.get("required") and not new.get("required"):
                        changes.append((path.stem, old["word_key"]))
    assert len(changes) == len({name for name, _ in changes}) == 21


def test_custom_submissions_and_exports_with_optional_recipient(auth, db_engine):
    template_id = "00000000-0000-4000-8000-000000000001"
    recipient = {"key": "receiving_agency", "word_key": "receiving_agency",
                 "label": "Cơ quan tiếp nhận", "type": "text", "required": True}
    applicant = {"key": "name", "word_key": "applicant_name",
                 "label": "Họ tên", "type": "text", "required": True}
    content = {
        "name": "Mẫu cũ", "domain": "dat_dai",
        "sections": [{"id": "sec_1", "title": "Người làm đơn", "blocks": [
            {"instance_id": "agency", "block_key": "receiving_agency", "fields": [recipient]},
            {"instance_id": "applicant", "block_key": "free_field", "fields": [applicant]},
        ]}],
        "repeat_groups": [{"key": "people", "title": "Người liên quan", "fields": [recipient]}],
    }
    doc = Document()
    doc.add_paragraph("Kính gửi: {{receiving_agency}}")
    doc.add_paragraph("Người làm đơn: {{applicant_name}}")
    doc.add_table(rows=1, cols=1).cell(0, 0).text = "{{#people}}{{people.receiving_agency}}{{/people}}"
    word = BytesIO()
    doc.save(word)
    with db_engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO public.custom_form_templates
                (id, template_group_id, status, name, domain, content, word_data)
            VALUES (:id, :id, 'published', 'Mẫu cũ', 'dat_dai', :content, :word)
        """), {"id": template_id, "content": json.dumps(content), "word": word.getvalue()})

    headers = auth()
    url = f"{base}/form-templates/{template_id}"
    public = client.get(url)
    assert public.status_code == 200, public.text
    public_content = public.json()["data"]["content"]
    assert public_content["sections"][0]["blocks"][0]["fields"][0]["required"] is False
    assert public_content["repeat_groups"][0]["fields"][0]["required"] is False

    body = {"values": {"applicant.name": "Nguyễn Văn An"}, "groups": {"people": [{}]}}
    created = client.post(f"{url}/submissions", headers=headers, json={**body, "status": "submitted"})
    assert created.status_code == 201, created.text
    with db_engine.connect() as conn:
        stored = conn.execute(
            text("SELECT form_values FROM public.custom_form_submissions WHERE id = :id"),
            {"id": created.json()["data"]["id"]},
        ).scalar_one()
    assert json.loads(stored) == body

    pdf = client.post(f"{url}/preview-pdf", headers=headers, json=body)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    assert client.post(f"{url}/download-word", headers=headers, json={}).status_code == 422

    for office in ("", "Legacy Receiving Office"):
        values = dict(body["values"])
        if office:
            values["agency.receiving_agency"] = office
        response = client.post(f"{url}/download-word", headers=headers, json={**body, "values": values})
        assert response.status_code == 200, response.text
        filled = Document(BytesIO(response.content))
        assert filled.paragraphs[0].text == f"Kính gửi: {office}"
        assert "Nguyễn Văn An" in filled.paragraphs[1].text
        assert "{{" not in filled.tables[0].cell(0, 0).text


@pytest.mark.parametrize("separate_recipient_section", [False, True])
def test_custom_conditions_across_submission_and_exports(auth, db_engine, separate_recipient_section):
    template_id = "00000000-0000-4000-8000-000000000002"
    agency = {"instance_id": "agency", "block_key": "free_field", "fields": [
        {"key": "office", "word_key": "office", "label": "Cơ quan tiếp nhận", "type": "text", "required": True},
    ]}
    legacy = {"field": "agency.office", "equals": "Office A"}
    individual = {"field": "main.kind", "equals": "individual"}
    organization = {"field": "main.kind", "equals": "organization"}

    def field(key, condition):
        return {"key": key, "word_key": key, "label": key, "type": "text", "required": True, "condition": condition}

    main = {"instance_id": "main", "block_key": "free_field", "fields": [
        {"key": "kind", "word_key": "kind", "label": "Kind", "type": "select", "required": True,
         "options": ["individual", "organization"]},
        field("person", individual),
        field("organization", organization),
        field("office_note", legacy),
    ]}
    sections = [{"id": "main", "title": "Applicant", "blocks": [main]}]
    if separate_recipient_section:
        sections.insert(0, {"id": "agency", "title": "Cơ quan tiếp nhận", "blocks": [agency]})
    else:
        sections[0]["blocks"].insert(0, agency)
    row = {"key": "name", "word_key": "name", "label": "Name", "type": "text", "required": True}
    content = {
        "name": "Conditional template", "domain": "dat_dai", "sections": sections,
        "repeat_groups": [
            {"key": "contacts", "title": "Contacts", "condition": legacy, "fields": [row]},
            {"key": "members", "title": "Members", "condition": organization, "fields": [row]},
        ],
    }
    doc = Document()
    for key in ("office", "kind", "person", "organization", "office_note"):
        doc.add_paragraph("{{" + key + "}}")
    for key in ("contacts", "members"):
        doc.add_table(rows=1, cols=1).cell(0, 0).text = (
            "{{#" + key + "}} {{" + key + ".name}} {{/" + key + "}}"
        )
    word = BytesIO()
    doc.save(word)
    with db_engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO public.custom_form_templates
                (id, template_group_id, status, name, domain, content, word_data)
            VALUES (:id, :id, 'published', 'Conditional template', 'dat_dai', :content, :word)
        """), {"id": template_id, "content": json.dumps(content), "word": word.getvalue()})

    headers = auth()
    url = f"{base}/form-templates/{template_id}"
    body = {
        "values": {"main.kind": "individual", "main.person": "Active Person", "main.office_note": "Kept Note"},
        "groups": {"contacts": [{"name": "Kept Contact"}]},
    }
    invalid = {"values": {"main.kind": "individual"}, "groups": {}}
    assert client.post(f"{url}/submissions", headers=headers, json=invalid).status_code == 422
    assert client.post(f"{url}/download-word", headers=headers, json=invalid).status_code == 422
    incomplete_row = {**body, "groups": {"contacts": [{}]}}
    assert client.post(f"{url}/submissions", headers=headers, json=incomplete_row).status_code == 422
    no_note = {"main.kind": "individual", "main.person": "Active Person"}
    assert client.post(f"{url}/download-word", headers=headers, json={"values": no_note}).status_code == 200
    assert client.post(f"{url}/download-word", headers=headers,
                       json={"values": {**no_note, "agency.office": "Office A"}}).status_code == 422

    submitted = client.post(f"{url}/submissions", headers=headers, json={**body, "status": "submitted"})
    assert submitted.status_code == 201, submitted.text
    with db_engine.connect() as conn:
        stored = conn.execute(text("SELECT form_values FROM public.custom_form_submissions WHERE id = :id"),
                              {"id": submitted.json()["data"]["id"]}).scalar_one()
    assert json.loads(stored) == body

    # Old clients can send data from both branches. Only the applicable branch is printed.
    values = {**body["values"], "main.organization": "Inactive Organization"}
    groups = {**body["groups"], "members": [{"name": "Inactive Member"}]}
    payload = {"values": values, "groups": groups}
    for office in (None, "", "Office A", "Office B"):
        request = {"values": dict(values), "groups": groups}
        if office is not None:
            request["values"]["agency.office"] = office
        response = client.post(f"{url}/download-word", headers=headers, json=request)
        assert response.status_code == 200, response.text
        filled = Document(BytesIO(response.content))
        assert filled.paragraphs[0].text == (office or "")
        assert filled.paragraphs[2].text == "Active Person"
        assert filled.paragraphs[3].text == ""
        assert filled.paragraphs[4].text == ("" if office == "Office B" else "Kept Note")
        assert [table.cell(0, 0).text.strip() for table in filled.tables] == (
            [] if office == "Office B" else ["Kept Contact"]
        )

    pdf = client.post(f"{url}/preview-pdf", headers=headers, json=payload)
    assert pdf.status_code == 200, pdf.text
    pdf_text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(pdf.content)).pages)
    for expected in ("Active Person", "Kept Note", "Kept Contact"):
        assert expected in pdf_text
    assert "Inactive" not in pdf_text

    admin_word = client.post(f"{base}/admin/form-templates/{template_id}/download-word",
                             headers=auth(role="admin"), json=body)
    assert admin_word.status_code == 200, admin_word.text
    filled = Document(BytesIO(admin_word.content))
    assert filled.paragraphs[4].text == ""
    assert not filled.tables


def test_standalone_mau18_word_and_pdf_without_recipient(auth, monkeypatch):
    source = (root / "database/mau18-two-person.docx").read_bytes()
    monkeypatch.setattr("app.api.mau18.source_mau18", lambda: source)
    values = mau18_payload()
    del values["receiving_agency"]
    url = f"{base}/templates/mau18-two-person"
    word = client.post(f"{url}/docx", headers=auth(), json=values)
    assert word.status_code == 200, word.text
    assert "{{receiving_agency}}" not in "\n".join(
        paragraph.text for paragraph in Document(BytesIO(word.content)).paragraphs
    )
    pdf = client.post(f"{url}/preview-filled", headers=auth(), json=values)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
