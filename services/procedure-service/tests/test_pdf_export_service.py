import io
from app.services.pdf_export_service import generate_application_dossier_pdf


def test_generate_application_dossier_pdf_valid():
    sample_data = {
        "application_code": "DD-001-811F7E21",
        "record": {
            "status": "submitted",
            "created_at": "2026-10-22T03:00:00Z",
            "form_data": {
                "applicant_name": "Bùi Võ Đức Trọng",
                "applicant_gender": "Nam",
                "applicant_dob": "2006-02-16",
                "applicant_id_type": "Căn cước công dân",
                "applicant_id_number": "123456789012",
                "applicant_phone": "0918262416",
                "applicant_email": "buildpc1234@gmail.com",
                "applicant_permanent_address": "Quận 2, Phường Sông Đà, Thị xã Mường Lay, Tỉnh Điện Biên",
                "land_address": "Tổ dân phố 1, Phường Sông Đà, Thị xã Mường Lay",
                "land_area": "150 m²",
            },
        },
        "procedure": {
            "name": "Đăng ký biến động quyền sử dụng đất do chuyển nhượng",
            "receiving_agency_level": "district",
        },
        "schema": {
            "sections": [
                {"key": "applicant", "title": "1. Thông tin người làm đơn"},
                {"key": "property", "title": "2. Thông tin thửa đất / Bất động sản"},
            ],
            "fields": [
                {"name": "applicant_name", "label": "Họ và tên người làm đơn", "section": "applicant"},
                {"name": "applicant_gender", "label": "Giới tính", "section": "applicant"},
                {"name": "applicant_dob", "label": "Ngày sinh", "section": "applicant"},
                {"name": "applicant_id_type", "label": "Loại định danh", "section": "applicant"},
                {"name": "applicant_id_number", "label": "Số CCCD", "section": "applicant"},
                {"name": "applicant_phone", "label": "Số điện thoại", "section": "applicant"},
                {"name": "applicant_email", "label": "Email", "section": "applicant"},
                {"name": "applicant_permanent_address", "label": "Địa chỉ thường trú", "section": "applicant"},
                {"name": "land_address", "label": "Địa chỉ đất", "section": "property"},
                {"name": "land_area", "label": "Diện tích", "section": "property"},
            ],
        },
    }

    pdf_bytes = generate_application_dossier_pdf(sample_data)
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 1000
    assert pdf_bytes.startswith(b"%PDF-")


def test_generate_application_dossier_pdf_fallback():
    # Test with minimal data when schema is empty
    minimal_data = {
        "application_code": "TEST-001",
        "record": {
            "status": "draft",
            "form_data": {"ghi_chu": "Dữ liệu mẫu kiểm thử"},
        },
    }

    pdf_bytes = generate_application_dossier_pdf(minimal_data)
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 500
    assert pdf_bytes.startswith(b"%PDF-")
