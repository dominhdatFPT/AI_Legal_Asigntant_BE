import pytest
from fastapi.testclient import TestClient
from form_samples import valid_form

from app.main import app

client = TestClient(app)

USER_A = "11111111-1111-4111-8111-111111111111"
USER_B = "22222222-2222-4222-8222-222222222222"
USER_C = "33333333-3333-4333-8333-333333333333"
ADMIN = "99999999-9999-4999-8999-999999999999"
CCCD_B = "079198004521"

ITEM_KEYS = {"id", "domain", "procedure_code", "procedure_name", "status", "applicant_name", "applicant_id_number",
             "address", "fee", "receiving_agency_level", "created_at", "due_date"}


@pytest.fixture
def seeded(auth):
    def submit(user, payload):
        res = client.post("/api/v1/procedures/applications", json=payload, headers=auth(user))
        assert res.status_code == 200, res.text

    submit(USER_A, {"procedure_code": "DD-001", "status": "submitted",
                    "form_data": valid_form("DD-001", applicant_name="Nguyễn Văn An", applicant_id_number="001095012849",
                                            land_address="Phường Kim Mã, Hà Nội")})
    submit(USER_A, {"procedure_code": "HKD-001", "status": "draft",
                    "form_data": valid_form("HKD-001", applicant_name="Nguyễn Văn An", headquarters_address="Quận 1, TP.HCM")})
    submit(USER_B, {"procedure_code": "DD-002", "status": "submitted",
                    "form_data": valid_form("DD-002", applicant_name="Trần Thị Mai", applicant_id_number=CCCD_B)})


def test_list_my_applications_only_mine(seeded, auth):
    res = client.get("/api/v1/procedures/applications/me", headers=auth(USER_A))
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["pagination"] == {"page": 1, "page_size": 10, "total": 2, "total_pages": 1}
    assert {i["procedure_code"] for i in data["items"]} == {"DD-001", "HKD-001"}
    assert all(set(i) == ITEM_KEYS for i in data["items"])


def test_list_item_fields_from_procedure(seeded, auth):
    items = client.get("/api/v1/procedures/applications/me?domain=land", headers=auth(USER_A)).json()["data"]["items"]
    assert len(items) == 1
    land = items[0]
    assert land["procedure_name"] == "Đăng ký biến động quyền sử dụng đất do chuyển nhượng"
    assert land["address"] == "Phường Kim Mã, Hà Nội"
    assert land["receiving_agency_level"] == "district"
    assert land["fee"] == 0
    # DD-001 xử lý 10 ngày kể từ ngày nộp
    assert land["due_date"] is not None and len(land["due_date"]) == 10

    biz = client.get("/api/v1/procedures/applications/me?domain=business", headers=auth(USER_A)).json()["data"]["items"][0]
    assert biz["address"] == "Quận 1, TP.HCM"
    assert biz["status"] == "draft"


def test_due_date_null_without_processing_days(seeded, auth):
    item = client.get("/api/v1/procedures/applications/me", headers=auth(USER_B)).json()["data"]["items"][0]
    assert item["procedure_code"] == "DD-002"
    assert item["due_date"] is None


def test_list_my_applications_empty(seeded, auth):
    data = client.get("/api/v1/procedures/applications/me", headers=auth(USER_C)).json()["data"]
    assert data["items"] == []
    assert data["pagination"]["total"] == 0
    assert data["pagination"]["total_pages"] == 0


def test_me_route_not_shadowed(auth):
    res = client.get("/api/v1/procedures/applications/me", headers=auth(USER_C))
    assert res.status_code == 200
    assert res.json()["error"] is None


def test_me_ignores_user_id_query(seeded, auth):
    data = client.get(f"/api/v1/procedures/applications/me?user_id={USER_B}", headers=auth(USER_A)).json()["data"]
    assert data["pagination"]["total"] == 2
    assert "DD-002" not in {i["procedure_code"] for i in data["items"]}


def test_me_filter_status(seeded, auth):
    data = client.get("/api/v1/procedures/applications/me?status=draft", headers=auth(USER_A)).json()["data"]
    assert [i["procedure_code"] for i in data["items"]] == ["HKD-001"]


def test_admin_list_all_and_filter_q(seeded, auth):
    res = client.get("/api/v1/procedures/applications", headers=auth(ADMIN, "admin"))
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["pagination"]["total"] == 3
    assert all("user_id" in i for i in data["items"])

    by_cccd = client.get(f"/api/v1/procedures/applications?q={CCCD_B}", headers=auth(ADMIN, "admin")).json()["data"]
    assert [i["user_id"] for i in by_cccd["items"]] == [USER_B]

    by_name = client.get("/api/v1/procedures/applications?q=nguyễn văn", headers=auth(ADMIN, "admin")).json()["data"]
    assert by_name["pagination"]["total"] == 2


def test_admin_list_paginates(seeded, auth):
    data = client.get("/api/v1/procedures/applications?page=2&page_size=2", headers=auth(ADMIN, "admin")).json()["data"]
    assert len(data["items"]) == 1
    assert data["pagination"] == {"page": 2, "page_size": 2, "total": 3, "total_pages": 2}


@pytest.mark.parametrize("path", ["/api/v1/procedures/applications", "/api/v1/procedures/applications/summary"])
def test_admin_routes_forbidden_for_citizen(seeded, auth, path):
    res = client.get(path, headers=auth(USER_A))
    assert res.status_code == 403
    assert res.json()["data"] is None


def test_summary_counts(seeded, auth):
    res = client.get("/api/v1/procedures/applications/summary", headers=auth(ADMIN, "admin"))
    assert res.status_code == 200
    data = res.json()["data"]
    assert data == {
        "total": 3,
        "by_domain": {"land": 2, "business": 1},
        "by_status": {"draft": 1, "submitted": 2, "processing": 0, "completed": 0, "cancelled": 0},
    }


@pytest.mark.parametrize("query", ["page_size=0", "page_size=51", "page=0", "domain=finance", "status=done"])
def test_list_invalid_params_422(auth, query):
    res = client.get(f"/api/v1/procedures/applications/me?{query}", headers=auth(USER_A))
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("method,path", [
    ("get", "/api/v1/procedures/applications/me"),
    ("get", "/api/v1/procedures/applications"),
    ("get", "/api/v1/procedures/applications/summary"),
    ("get", "/api/v1/procedures/applications/land/5b1d7f3e-8a4c-4f8e-9d2a-0c6b1e7a9f21"),
    ("post", "/api/v1/procedures/applications"),
])
@pytest.mark.parametrize("bad", ["none", "garbage", "expired"])
def test_all_application_routes_require_token(token, method, path, bad):
    headers = {
        "none": {},
        "garbage": {"Authorization": "Bearer not-a-jwt"},
        "expired": {"Authorization": f"Bearer {token(exp=1)}"},
    }[bad]
    kwargs = {"json": {"procedure_code": "DD-001"}} if method == "post" else {}
    res = getattr(client, method)(path, headers=headers, **kwargs)
    assert res.status_code == 401
