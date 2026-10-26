import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

SAMPLE_DRAFT_CONTENT = {
    "schema_version": 1,
    "name": "Đơn đăng ký biến động đất đai",
    "code": "DD-001/M18",
    "domain": "dat_dai",
    "procedure_id": "DD-001",
    "sections": []
}

SAMPLE_COMPLETE_CONTENT = {
    "schema_version": 1,
    "name": "Đơn đăng ký biến động đất đai hoàn chỉnh",
    "code": "DD-001/M18",
    "domain": "dat_dai",
    "procedure_id": "DD-001",
    "sections": [
        {
            "id": "sec_1",
            "title": "I. Thông tin người sử dụng đất",
            "order": 1,
            "blocks": [
                {
                    "instance_id": "personal_info_1",
                    "block_key": "personal_info",
                    "order": 1,
                    "fields": [
                        {
                            "key": "full_name",
                            "label": "Họ và tên",
                            "type": "text",
                            "required": True,
                            "autofill": "profile.full_name"
                        },
                        {
                            "key": "cccd_number",
                            "label": "Số CCCD",
                            "type": "cccd",
                            "required": True,
                            "autofill": "profile.cccd_number"
                        }
                    ]
                }
            ]
        },
        {
            "id": "sec_2",
            "title": "II. Thông tin bổ sung",
            "order": 2,
            "blocks": [
                {
                    "instance_id": "free_1",
                    "block_key": "free_field",
                    "order": 1,
                    "fields": [
                        {
                            "key": "note",
                            "label": "Ghi chú thêm",
                            "type": "textarea",
                            "required": False
                        }
                    ]
                }
            ]
        }
    ]
}


def test_admin_create_draft_success(auth):
    """Admin tạo mẫu đơn nháp mới thành công (201)."""
    headers = auth(role="admin")
    res = client.post("/api/v1/procedures/admin/form-templates", json=SAMPLE_DRAFT_CONTENT, headers=headers)
    assert res.status_code == 201, res.text
    data = res.json()["data"]
    assert data["name"] == SAMPLE_DRAFT_CONTENT["name"]
    assert data["status"] == "draft"
    assert data["version"] == 1
    assert data["template_group_id"] is not None


def test_citizen_forbidden_on_admin_routes(auth):
    """Citizen không thể gọi API admin (403)."""
    headers = auth(role="citizen")
    res = client.post("/api/v1/procedures/admin/form-templates", json=SAMPLE_DRAFT_CONTENT, headers=headers)
    assert res.status_code == 403


def test_admin_update_draft_success(auth):
    """Admin cập nhật nội dung bản nháp (200)."""
    headers = auth(role="admin")
    created = client.post("/api/v1/procedures/admin/form-templates", json=SAMPLE_DRAFT_CONTENT, headers=headers).json()["data"]
    template_id = created["id"]

    updated_content = dict(SAMPLE_DRAFT_CONTENT)
    updated_content["name"] = "Tên mẫu đơn mới đã sửa"
    res = client.put(f"/api/v1/procedures/admin/form-templates/{template_id}", json=updated_content, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["data"]["name"] == "Tên mẫu đơn mới đã sửa"


def test_admin_publish_incomplete_rejected(auth):
    """Mẫu chưa đủ điều kiện (sections rỗng) không thể xuất bản (422)."""
    headers = auth(role="admin")
    created = client.post("/api/v1/procedures/admin/form-templates", json=SAMPLE_DRAFT_CONTENT, headers=headers).json()["data"]
    template_id = created["id"]

    res = client.post(f"/api/v1/procedures/admin/form-templates/{template_id}/publish", headers=headers)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_admin_full_lifecycle(auth):
    """Luồng đầy đủ: Tạo -> Publish -> Hide -> Re-publish -> New Version -> Archive cũ."""
    headers = auth(role="admin")

    # 1. Tạo bản nháp hoàn chỉnh
    created = client.post("/api/v1/procedures/admin/form-templates", json=SAMPLE_COMPLETE_CONTENT, headers=headers).json()["data"]
    t1_id = created["id"]
    group_id = created["template_group_id"]
    assert created["status"] == "draft"

    # 2. Xuất bản v1
    pub_res = client.post(f"/api/v1/procedures/admin/form-templates/{t1_id}/publish", headers=headers)
    assert pub_res.status_code == 200, pub_res.text
    assert pub_res.json()["data"]["status"] == "published"

    # 3. PUT khi đã xuất bản bị từ chối (409)
    put_res = client.put(f"/api/v1/procedures/admin/form-templates/{t1_id}", json=SAMPLE_COMPLETE_CONTENT, headers=headers)
    assert put_res.status_code == 409
    assert put_res.json()["error"]["code"] == "TEMPLATE_NOT_DRAFT"

    # 4. Ẩn (hide)
    hide_res = client.post(f"/api/v1/procedures/admin/form-templates/{t1_id}/hide", headers=headers)
    assert hide_res.status_code == 200
    assert hide_res.json()["data"]["status"] == "hidden"

    # 5. Xuất bản lại từ hidden
    repub_res = client.post(f"/api/v1/procedures/admin/form-templates/{t1_id}/publish", headers=headers)
    assert repub_res.status_code == 200
    assert repub_res.json()["data"]["status"] == "published"

    # 6. Tạo version mới (v2 draft)
    nv_res = client.post(f"/api/v1/procedures/admin/form-templates/{t1_id}/new-version", headers=headers)
    assert nv_res.status_code == 201
    v2_data = nv_res.json()["data"]
    assert v2_data["version"] == 2
    assert v2_data["status"] == "draft"
    assert v2_data["template_group_id"] == group_id

    # 7. Gọi new-version lần 2 khi v2 đang draft -> Idempotent trả lại v2 kèm 200 OK
    nv2_res = client.post(f"/api/v1/procedures/admin/form-templates/{t1_id}/new-version", headers=headers)
    assert nv2_res.status_code == 200
    assert nv2_res.json()["data"]["id"] == v2_data["id"]

    # 8. Xuất bản v2 -> v1 tự động chuyển archived
    t2_id = v2_data["id"]
    pub2_res = client.post(f"/api/v1/procedures/admin/form-templates/{t2_id}/publish", headers=headers)
    assert pub2_res.status_code == 200
    assert pub2_res.json()["data"]["status"] == "published"

    # Kiểm tra v1 đã là archived
    t1_check = client.get(f"/api/v1/procedures/admin/form-templates/{t1_id}", headers=headers).json()["data"]
    assert t1_check["status"] == "archived"


def test_citizen_public_access(auth):
    """Công dân chỉ xem được bản published."""
    headers = auth(role="admin")

    # Tạo 1 bản published
    created = client.post("/api/v1/procedures/admin/form-templates", json=SAMPLE_COMPLETE_CONTENT, headers=headers).json()["data"]
    tid = created["id"]
    client.post(f"/api/v1/procedures/admin/form-templates/{tid}/publish", headers=headers)

    # Tạo 1 bản draft
    draft = client.post("/api/v1/procedures/admin/form-templates", json=SAMPLE_DRAFT_CONTENT, headers=headers).json()["data"]
    draft_id = draft["id"]

    # Công dân lấy danh sách (không cần token)
    pub_list = client.get("/api/v1/procedures/form-templates").json()["data"]["items"]
    ids = [item["id"] for item in pub_list]
    assert tid in ids
    assert draft_id not in ids

    # Công dân xem chi tiết bản published -> 200
    assert client.get(f"/api/v1/procedures/form-templates/{tid}").status_code == 200

    # Công dân xem chi tiết bản draft -> 404
    assert client.get(f"/api/v1/procedures/form-templates/{draft_id}").status_code == 404


def test_soft_delete_and_restore(auth):
    """Xóa mềm và khôi phục mẫu đơn."""
    headers = auth(role="admin")
    created = client.post("/api/v1/procedures/admin/form-templates", json=SAMPLE_DRAFT_CONTENT, headers=headers).json()["data"]
    tid = created["id"]

    # Xóa mềm -> 204
    del_res = client.delete(f"/api/v1/procedures/admin/form-templates/{tid}", headers=headers)
    assert del_res.status_code == 204

    # GET admin mặc định không thấy
    adm_list = client.get("/api/v1/procedures/admin/form-templates", headers=headers).json()["data"]["items"]
    assert tid not in [it["id"] for it in adm_list]

    # GET admin có include_deleted=true thì thấy
    del_list = client.get("/api/v1/procedures/admin/form-templates?include_deleted=true", headers=headers).json()["data"]["items"]
    assert tid in [it["id"] for it in del_list]

    # Khôi phục (restore) -> 200
    res_rest = client.post(f"/api/v1/procedures/admin/form-templates/{tid}/restore", headers=headers)
    assert res_rest.status_code == 200
    assert res_rest.json()["data"]["deleted_at"] is None


def test_admin_routes_require_token():
    """Không có token -> 401."""
    assert client.get("/api/v1/procedures/admin/form-templates").status_code == 401


def test_invalid_template_id_returns_422(auth):
    """id không phải UUID -> 422, không để Postgres báo lỗi thành 500."""
    assert client.get("/api/v1/procedures/form-templates/khong-phai-uuid").status_code == 422
    res = client.get("/api/v1/procedures/admin/form-templates/khong-phai-uuid", headers=auth(role="admin"))
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_draft_free_field_without_label_saved_but_not_published(auth):
    """Nháp được lưu khi trường tự do chưa có tiêu đề; xuất bản thì bị chặn."""
    headers = auth(role="admin")
    content = {**SAMPLE_COMPLETE_CONTENT, "sections": [{**SAMPLE_COMPLETE_CONTENT["sections"][1], "blocks": [
        {"instance_id": "free_1", "block_key": "free_field", "order": 1,
         "fields": [{"key": "value", "label": "", "type": "text", "required": False}]}]}]}
    res = client.post("/api/v1/procedures/admin/form-templates", json=content, headers=headers)
    assert res.status_code == 201, res.text
    pub = client.post(f"/api/v1/procedures/admin/form-templates/{res.json()['data']['id']}/publish", headers=headers)
    assert pub.status_code == 422
    assert pub.json()["error"]["details"][0]["field"].endswith("fields[0].label")


@pytest.mark.parametrize("broken", ["not_configured", "connection_error"])
def test_db_unavailable_returns_503(monkeypatch, broken):
    """DB chưa cấu hình hoặc lỗi truy vấn -> 503 PROCEDURE_DB_UNAVAILABLE."""
    import app.database as database
    from sqlalchemy import create_engine

    if broken == "not_configured":
        monkeypatch.setattr(database, "_engine", None)
        monkeypatch.setenv("PROCEDURE_DATABASE_URL", "")
    else:
        monkeypatch.setattr(database, "_engine", create_engine("sqlite:///Z:/khong/ton/tai/db.sqlite"))
    res = client.get("/api/v1/procedures/form-templates")
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "PROCEDURE_DB_UNAVAILABLE"
