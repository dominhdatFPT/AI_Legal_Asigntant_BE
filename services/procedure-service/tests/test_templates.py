import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_list_templates_success():
    """Kiểm tra API lấy danh sách biểu mẫu trả về HTTP 200 và cấu trúc chuẩn."""
    response = client.get("/api/v1/procedures/templates")
    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert "data" in payload
    data = payload["data"]
    assert "items" in data
    assert "total" in data
    assert data["total"] > 0
    assert len(data["items"]) == data["total"]

    first = data["items"][0]
    assert "id" in first
    assert "procedure_code" in first
    assert "domain" in first
    assert "name" in first
    assert "format" in first
    assert "size" in first


def test_list_templates_alias_route():
    """Kiểm tra alias route /api/procedures/templates hoạt động đồng nhất."""
    response = client.get("/api/procedures/templates")
    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["total"] > 0


def test_filter_templates_by_domain_land():
    """Kiểm tra bộ lọc theo lĩnh vực đất đai (domain=land)."""
    response = client.get("/api/v1/procedures/templates?domain=land")
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert len(items) > 0
    for it in items:
        assert it["domain"] == "land"
        assert it["domain_label"] == "Đất đai & Bất động sản"


def test_filter_templates_by_domain_business():
    """Kiểm tra bộ lọc theo lĩnh vực doanh nghiệp (domain=business)."""
    response = client.get("/api/v1/procedures/templates?domain=business")
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert len(items) > 0
    for it in items:
        assert it["domain"] == "business"
        assert it["domain_label"] == "Doanh nghiệp & Hộ kinh doanh"


def test_filter_templates_by_procedure_code():
    """Kiểm tra bộ lọc theo mã thủ tục chi tiết (procedure_code=DN-003)."""
    response = client.get("/api/v1/procedures/templates?procedure_code=DN-003")
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert len(items) > 0
    for it in items:
        assert it["procedure_code"] == "DN-003"


def test_search_templates_query():
    """Kiểm tra tìm kiếm biểu mẫu tiếng Việt không phân biệt dấu."""
    response = client.get("/api/v1/procedures/templates?q=chuyen nhuong")
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert len(items) > 0


def test_download_word_template_by_procedure_code():
    """Kiểm tra tải trực tiếp file Word theo mã thủ tục."""
    response = client.get("/api/v1/procedures/templates/by-procedure/DD-001/word")
    assert response.status_code == 200
    assert len(response.content) > 1000
    assert "attachment" in response.headers.get("content-disposition", "")
    assert ".docx" in response.headers.get("content-disposition", "").lower() or ".doc" in response.headers.get("content-disposition", "").lower()


def test_download_word_template_not_found():
    """Kiểm tra mã thủ tục không tồn tại trả về lỗi 404."""
    response = client.get("/api/v1/procedures/templates/by-procedure/UNKNOWN-999/word")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "TEMPLATE_NOT_FOUND"


def test_download_template_by_id_success():
    """Kiểm tra tải file biểu mẫu dựa vào ID."""
    list_res = client.get("/api/v1/procedures/templates?domain=business")
    items = list_res.json()["data"]["items"]
    assert len(items) > 0
    target_id = items[0]["id"]

    response = client.get(f"/api/v1/procedures/templates/{target_id}/file?download=true")
    assert response.status_code == 200
    assert len(response.content) > 0
    assert "attachment" in response.headers.get("content-disposition", "")


def test_download_template_by_id_not_found():
    """Kiểm tra ID biểu mẫu không tồn tại trả về 404."""
    response = client.get("/api/v1/procedures/templates/00000000-0000-0000-0000-000000000000/file")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "TEMPLATE_NOT_FOUND"


def test_view_template_file_inline_default():
    """Kiểm tra xem trước PDF trực tiếp mặc định trả về inline và application/pdf."""
    list_res = client.get("/api/v1/procedures/templates?domain=land")
    items = list_res.json()["data"]["items"]
    pdf_items = [it for it in items if it.get("format") == "PDF"]
    assert len(pdf_items) > 0
    target_id = pdf_items[0]["id"]

    # 1. Không truyền param -> mặc định xem trước (inline)
    res_default = client.get(f"/api/v1/procedures/templates/{target_id}/file")
    assert res_default.status_code == 200
    assert "inline" in res_default.headers.get("content-disposition", "")
    assert res_default.headers.get("content-type") == "application/pdf"
    assert res_default.content.startswith(b"%PDF")

    # 2. Truyền download=false -> xem trước (inline)
    res_inline = client.get(f"/api/v1/procedures/templates/{target_id}/file?download=false")
    assert res_inline.status_code == 200
    assert "inline" in res_inline.headers.get("content-disposition", "")


def test_local_fallback_file_retrieval():
    """Kiểm tra cơ chế fallback đọc từ đĩa cục bộ với ID dạng local-{domain}-{fname}."""
    from app.repositories.template_repository import _local_disk_fallback_list, TemplateRepository

    local_items = _local_disk_fallback_list()
    assert len(local_items) > 0
    pdf_locals = [it for it in local_items if it.get("format") == "PDF"]
    assert len(pdf_locals) > 0

    target = pdf_locals[0]
    file_info = TemplateRepository.get_template_file(target["id"])
    assert file_info is not None
    assert file_info["media_type"] == "application/pdf"
    assert len(file_info["file_bytes"]) > 0
    assert file_info["file_bytes"].startswith(b"%PDF")
