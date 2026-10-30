import pytest
from fastapi.testclient import TestClient
from form_samples import valid_form

from app.main import app
from app.services.procedure_form_service import PROCEDURE_FIELD_DISPATCHER, get_procedure_form_schema, validate_form_data

client = TestClient(app)


def submit(headers, code="DD-001", status="submitted", **form):
    payload = {"procedure_code": code, "status": status, "form_data": valid_form(code, **form)}
    return client.post("/api/v1/procedures/applications", json=payload, headers=headers)


def error_fields(res):
    assert res.status_code == 422, res.text
    error = res.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    return {d["field"] for d in error["details"]}


@pytest.mark.parametrize("status", ["draft", "submitted"])
def test_valid_form_saved(auth, status):
    res = submit(auth(), status=status, applicant_name="Lê Văn C")
    assert res.status_code == 200, res.text
    assert res.json()["data"]["applicant_name"] == "Lê Văn C"


@pytest.mark.parametrize("status", ["draft", "submitted"])
def test_missing_required_rejected_even_for_draft(auth, status):
    assert error_fields(submit(auth(), status=status, plot_number="  ", land_area=None)) == {"plot_number", "land_area"}


def test_unknown_field_rejected(auth):
    assert error_fields(submit(auth(), citizen_fullname="Lê Văn C")) == {"citizen_fullname"}


def test_option_outside_list_rejected(auth):
    assert error_fields(submit(auth(), land_use_purpose="Đất ở")) == {"land_use_purpose"}


def test_regex_number_date_rejected(auth):
    res = submit(auth(), applicant_phone="09-0123", land_area="85,5", transfer_contract_date="14/10/2026")
    assert error_fields(res) == {"applicant_phone", "land_area", "transfer_contract_date"}


def test_required_checkbox_must_be_ticked():
    schema = get_procedure_form_schema("DD-004")
    errors = validate_form_data(schema, valid_form("DD-004", dispute_commitment=False))
    assert [e["field"] for e in errors] == ["dispute_commitment"]


def test_optional_field_may_be_empty(auth):
    assert submit(auth(), code="DD-002", land_use_term="", certificate_issue_date=None).status_code == 200


@pytest.mark.parametrize("code", list(PROCEDURE_FIELD_DISPATCHER))
def test_every_schema_accepts_its_sample(code):
    assert validate_form_data(get_procedure_form_schema(code), valid_form(code)) == []


def test_procedure_without_schema_rejected(auth, db_engine):
    from sqlalchemy import text

    with db_engine.begin() as conn:
        conn.execute(text("INSERT INTO public.procedures (id, code, name, domain) VALUES ('x', 'DD-900', 'Mới', 'land')"))
    res = client.post("/api/v1/procedures/applications", headers=auth(),
                      json={"procedure_code": "DD-900", "status": "submitted", "form_data": {}})
    assert error_fields(res) == {"procedure_code"}
