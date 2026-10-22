import pytest
from fastapi.testclient import TestClient
from form_samples import valid_form
from app.main import app

client = TestClient(app)


def test_health():
    res = client.get("/api/v1/procedures/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "service": "procedure-service"}


def test_list_domains():
    res = client.get("/api/v1/procedures/domains")
    assert res.status_code == 200
    body = res.json()
    data = body["data"]
    assert len(data) >= 2
    codes = [d["code"] for d in data]
    assert "business" in codes
    assert "land" in codes
    assert "total_procedures" in body
    assert body["total_procedures"] >= 2


def test_get_procedures_by_domain():
    res = client.get("/api/v1/procedures/domains/land")
    assert res.status_code == 200
    body = res.json()
    assert body["error"] is None
    data = body["data"]
    assert data["domain"] == "land"
    assert len(data["items"]) > 0
    first = data["items"][0]
    assert "name" in first
    assert "code" in first
    assert "processing_days" in first


def test_get_procedure_names_by_domain():
    res = client.get("/api/v1/procedures/domains/business/names")
    assert res.status_code == 200
    body = res.json()
    assert body["error"] is None
    data = body["data"]
    assert data["domain"] == "business"
    assert len(data["items"]) > 0
    first = data["items"][0]
    assert "id" in first
    assert "code" in first
    assert "name" in first


def test_list_procedures():
    res = client.get("/api/v1/procedures")
    assert res.status_code == 200
    body = res.json()
    assert body["error"] is None
    assert "items" in body["data"]
    assert "pagination" in body["data"]
    assert len(body["data"]["items"]) > 0
    codes = [i["code"] for i in body["data"]["items"]]
    assert codes == sorted(codes)


def test_list_procedures_filter_domain():
    res_biz = client.get("/api/v1/procedures?domain=business")
    assert res_biz.status_code == 200
    for item in res_biz.json()["data"]["items"]:
        assert item["domain"] == "business"

    res_land = client.get("/api/v1/procedures?domain=land")
    assert res_land.status_code == 200
    for item in res_land.json()["data"]["items"]:
        assert item["domain"] == "land"


def test_search_procedures():
    # Tìm có kết quả
    res = client.post("/api/v1/procedures/search", json={"query": "đăng ký hộ kinh doanh"})
    assert res.status_code == 200
    items = res.json()["data"]["items"]
    assert len(items) > 0
    assert any(i["code"] == "HKD-001" for i in items)

    # Tìm không dấu
    res_no_accent = client.post("/api/v1/procedures/search", json={"query": "HO KINH DOANH"})
    assert res_no_accent.status_code == 200
    assert len(res_no_accent.json()["data"]["items"]) > 0

    # Tìm không ra
    res_none = client.post("/api/v1/procedures/search", json={"query": "xin chào không có thủ tục này"})
    assert res_none.status_code == 200
    assert res_none.json()["data"]["items"] == []


def test_search_invalid_input():
    res = client.post("/api/v1/procedures/search", json={"query": "   "})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_get_procedure_by_id_and_code():
    # Tìm theo mã HKD-001
    res_code = client.get("/api/v1/procedures/HKD-001")
    assert res_code.status_code == 200
    data_code = res_code.json()["data"]
    assert data_code["code"] == "HKD-001"
    assert len(data_code["legal_bases"]) > 0
    assert len(data_code["requirements"]) > 0

    # Tìm theo UUID
    proc_id = data_code["id"]
    res_id = client.get(f"/api/v1/procedures/{proc_id}")
    assert res_id.status_code == 200
    assert res_id.json()["data"]["id"] == proc_id


def test_get_procedure_sub_resources():
    # Requirements
    res_req = client.get("/api/v1/procedures/HKD-001/requirements")
    assert res_req.status_code == 200
    assert "requirements" in res_req.json()["data"]
    assert len(res_req.json()["data"]["requirements"]) > 0

    # Conditions
    res_cond = client.get("/api/v1/procedures/HKD-001/conditions")
    assert res_cond.status_code == 200
    assert "conditions" in res_cond.json()["data"]

    # Fees
    res_fees = client.get("/api/v1/procedures/HKD-001/fees")
    assert res_fees.status_code == 200
    assert "fees" in res_fees.json()["data"]


def test_get_procedure_not_found():
    res = client.get("/api/v1/procedures/UNKNOWN-999")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "PROCEDURE_NOT_FOUND"


def test_get_procedure_form_schema():
    # Test DD-001
    res_dd = client.get("/api/v1/procedures/form-schema/DD-001")
    assert res_dd.status_code == 200
    body_dd = res_dd.json()["data"]
    assert body_dd["procedure_code"] == "DD-001"
    assert body_dd["domain"] == "land"
    assert body_dd["target_table"] == "land_procedure_records"
    assert len(body_dd["sections"]) > 0

    # Test HKD-001
    res_hkd = client.get("/api/v1/procedures/HKD-001/form-schema")
    assert res_hkd.status_code == 200
    body_hkd = res_hkd.json()["data"]
    assert body_hkd["procedure_code"] == "HKD-001"
    assert body_hkd["domain"] == "business"
    assert body_hkd["target_table"] == "business_procedure_records"

    # Test invalid code
    res_inv = client.get("/api/v1/procedures/form-schema/UNKNOWN-999")
    assert res_inv.status_code == 404
    assert res_inv.json()["error"]["code"] == "SCHEMA_NOT_FOUND"


def test_submit_and_get_applications(auth):
    headers = auth()
    # Submit land application
    payload_land = {
        "procedure_code": "DD-001",
        "status": "draft",
        "form_data": valid_form(
            "DD-001",
            applicant_name="Lê Văn C",
            applicant_id_number="079090123456",
            applicant_phone="0988776655",
            plot_number="99",
            map_sheet_number="12",
            land_address="Xã Tân An, TP. Hội An, Quảng Nam",
            land_area=250.0,
        ),
        "extra_data": {"source": "unit_test"},
    }
    res_sub_land = client.post("/api/v1/procedures/applications", json=payload_land, headers=headers)
    assert res_sub_land.status_code == 200
    land_rec = res_sub_land.json()["data"]
    assert "id" in land_rec
    assert land_rec["procedure_code"] == "DD-001"
    land_id = land_rec["id"]

    # Get land application by ID
    res_get_land = client.get(f"/api/v1/procedures/applications/land/{land_id}", headers=headers)
    assert res_get_land.status_code == 200
    fetched_land = res_get_land.json()["data"]
    assert fetched_land["id"] == land_id
    assert fetched_land["applicant_name"] == "Lê Văn C"

    # Submit business application
    payload_biz = {
        "procedure_code": "HKD-001",
        "status": "submitted",
        "form_data": valid_form(
            "HKD-001",
            applicant_name="Phạm Thị D",
            business_name_vn="CỬA HÀNG HOA TƯƠI MAI",
            headquarters_address="Quận 3, TP. Hồ Chí Minh",
            charter_capital=50000000,
        ),
    }
    res_sub_biz = client.post("/api/v1/procedures/applications", json=payload_biz, headers=headers)
    assert res_sub_biz.status_code == 200
    biz_rec = res_sub_biz.json()["data"]
    assert "id" in biz_rec
    assert biz_rec["procedure_code"] == "HKD-001"
    biz_id = biz_rec["id"]

    # Get business application by ID
    res_get_biz = client.get(f"/api/v1/procedures/applications/business/{biz_id}", headers=headers)
    assert res_get_biz.status_code == 200
    fetched_biz = res_get_biz.json()["data"]
    assert fetched_biz["id"] == biz_id
    assert fetched_biz["business_name_vn"] == "CỬA HÀNG HOA TƯƠI MAI"

    # Test invalid domain
    res_bad_domain = client.get(f"/api/v1/procedures/applications/finance/{land_id}", headers=headers)
    assert res_bad_domain.status_code == 400
    assert res_bad_domain.json()["error"]["code"] == "INVALID_DOMAIN"

    # Test lookup by composite code (e.g. DD-001-XXXXXXXX)
    short_land_id = land_id[:8].upper()
    comp_code = f"DD-001-{short_land_id}"
    res_lookup = client.get(f"/api/v1/procedures/applications/lookup/{comp_code}", headers=headers)
    assert res_lookup.status_code == 200
    lookup_data = res_lookup.json()["data"]
    assert lookup_data["application_code"] == comp_code
    assert lookup_data["record"]["id"] == land_id
    assert lookup_data["record"]["applicant_name"] == "Lê Văn C"
    assert lookup_data["record"]["form_data"]["applicant_name"] == "Lê Văn C"
    assert "schema" in lookup_data
    assert "sections" in lookup_data["schema"]

    # Test lookup by short id
    res_lookup_short = client.get(f"/api/v1/procedures/applications/lookup/{short_land_id}", headers=headers)
    assert res_lookup_short.status_code == 200
    assert res_lookup_short.json()["data"]["record"]["id"] == land_id

    # Test lookup not found
    res_lookup_nf = client.get("/api/v1/procedures/applications/lookup/NON-EXISTENT-9999", headers=headers)
    assert res_lookup_nf.status_code == 404

