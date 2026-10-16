import pytest
from app.services.procedure_form_service import (
    PROCEDURE_FIELD_DISPATCHER,
    PROCEDURE_METADATA,
    get_procedure_form_schema,
)

# 25 Mã thủ tục chi tiết
ALL_25_CODES = [
    # 12 Đất đai
    "DD-001", "DD-002", "DD-003", "DD-004", "DD-005", "DD-006",
    "DD-007", "DD-008", "DD-009", "DD-010", "DD-011", "DD-012",
    # 13 Kinh doanh
    "HKD-001", "HKD-002",
    "DN-001", "DN-002", "DN-003", "DN-004", "DN-005",
    "DN-006", "DN-007", "DN-008", "DN-009", "DN-010", "DN-011",
]


def test_all_25_procedures_have_dispatchers():
    """Kiểm tra có đầy đủ 25 hàm chi tiết được đăng ký trong Dispatcher"""
    assert len(ALL_25_CODES) == 25
    for code in ALL_25_CODES:
        assert code in PROCEDURE_FIELD_DISPATCHER, f"Thiếu handler cho thủ tục {code}"
        assert code in PROCEDURE_METADATA, f"Thiếu metadata cho thủ tục {code}"


def test_get_procedure_form_schema_all_25():
    """Kiểm tra hàm trung gian điều phối chính xác cả 25 thủ tục"""
    for code in ALL_25_CODES:
        schema = get_procedure_form_schema(code)
        assert schema.procedure_code == code
        assert len(schema.procedure_name) > 0
        assert schema.domain in ("land", "business")
        
        if code.startswith("DD"):
            assert schema.target_table == "land_procedure_records"
        else:
            assert schema.target_table == "business_procedure_records"

        assert len(schema.sections) >= 2, f"Thủ tục {code} phải có ít nhất 2 sections"
        assert len(schema.fields) >= 5, f"Thủ tục {code} phải có ít nhất 5 trường"

        # Kiểm tra tính duy nhất của tên cột/trường trong mỗi schema
        field_names = [f.name for f in schema.fields]
        assert len(field_names) == len(set(field_names)), f"Thủ tục {code} có trường bị trùng tên: {field_names}"


def test_invalid_procedure_code():
    """Kiểm tra hàm trung gian báo lỗi rõ ràng khi truyền mã không hợp lệ"""
    with pytest.raises(ValueError) as excinfo:
        get_procedure_form_schema("UNKNOWN-999")
    assert "Không tìm thấy cấu hình biểu mẫu" in str(excinfo.value)
