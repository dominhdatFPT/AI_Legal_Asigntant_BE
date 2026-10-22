import sys
import os
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.procedure_service import ProcedureService
from form_samples import valid_form

OWNER = "11111111-1111-4111-8111-111111111111"


class TestRecordPersistence(unittest.TestCase):
    def test_01_land_submit_and_retrieve(self):
        form_data = valid_form(
            "DD-001",
            applicant_name="Nguyễn Văn A",
            applicant_id_number="012345678901",
            applicant_phone="0901234567",
            plot_number="123",
            map_sheet_number="45",
            land_address="Số 10 Đường ABC, Phường 1, Quận 1, TP.HCM",
            land_area=120.5,
            land_use_purpose="Đất ở tại đô thị (ODT)",
            land_use_origin="Nhận chuyển nhượng quyền sử dụng đất",
        )
        res = ProcedureService.submit_application(
            procedure_code="DD-001",
            user_id=OWNER,
            status="draft",
            form_data=form_data,
            extra_data={"note": "Test save land record"},
        )
        self.assertIn("id", res)
        self.assertEqual(res["procedure_code"], "DD-001")
        self.assertEqual(res["applicant_name"], "Nguyễn Văn A")
        self.assertEqual(res["domain"], "land")
        self.assertEqual(res["target_table"], "land_procedure_records")

        record_id = res["id"]
        fetched = ProcedureService.get_application_record("land", record_id, owner_id=OWNER)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["id"], record_id)
        self.assertEqual(fetched["applicant_name"], "Nguyễn Văn A")
        self.assertEqual(float(fetched["land_area"]), 120.5)
        self.assertEqual(fetched["form_data"]["land_use_purpose"], "Đất ở tại đô thị (ODT)")
        print(f"\n[PASS] Saved & Retrieved Land record ID: {record_id}")

    def test_02_business_submit_and_retrieve(self):
        form_data = valid_form(
            "HKD-001",
            applicant_name="Trần Thị B",
            applicant_id_number="098765432109",
            applicant_phone="0912345678",
            business_name_vn="HỘ KINH DOANH BÌNH MINH",
            headquarters_address="Số 50 Đường XYZ, Phường 2, TP. Đà Nẵng",
            charter_capital=100000000,
            main_business_lines="Bán lẻ hàng tạp phẩm, nước giải khát",
        )
        res = ProcedureService.submit_application(
            procedure_code="HKD-001",
            user_id=OWNER,
            status="draft",
            form_data=form_data,
            extra_data={"note": "Test save business record"},
        )
        self.assertIn("id", res)
        self.assertEqual(res["procedure_code"], "HKD-001")
        self.assertEqual(res["applicant_name"], "Trần Thị B")
        self.assertEqual(res["domain"], "business")
        self.assertEqual(res["target_table"], "business_procedure_records")

        record_id = res["id"]
        fetched = ProcedureService.get_application_record("business", record_id, owner_id=OWNER)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["id"], record_id)
        self.assertEqual(fetched["applicant_name"], "Trần Thị B")
        self.assertEqual(fetched["business_name_vn"], "HỘ KINH DOANH BÌNH MINH")
        self.assertEqual(fetched["charter_capital"], 100000000)
        self.assertEqual(fetched["form_data"]["main_business_lines"], "Bán lẻ hàng tạp phẩm, nước giải khát")
        print(f"\n[PASS] Saved & Retrieved Business record ID: {record_id}")

    def test_03_lookup_by_code_and_short_id(self):
        form_data = valid_form(
            "DD-001",
            applicant_name="Bùi Võ Đức Trọng",
            applicant_id_number="012345678901",
            applicant_phone="0901234567",
            plot_number="128",
            map_sheet_number="14",
            land_address="Số 10 Đường ABC, Phường 1, Quận 1, TP.HCM",
            land_area=120.5,
            land_use_purpose="Đất ở tại đô thị (ODT)",
            land_use_origin="Nhận chuyển nhượng quyền sử dụng đất",
        )
        res = ProcedureService.submit_application(
            procedure_code="DD-001",
            user_id=OWNER,
            status="submitted",
            form_data=form_data,
        )
        record_id = res["id"]
        short_id = record_id[:8].upper()
        composite_code = f"DD-001-{short_id}"

        # 1. Lookup bằng mã ghép chuẩn (DD-001-XXXXXXXX)
        lookup1 = ProcedureService.lookup_application(composite_code, owner_id=OWNER)
        self.assertIsNotNone(lookup1)
        self.assertEqual(lookup1["application_code"], composite_code)
        self.assertEqual(lookup1["record"]["applicant_name"], "Bùi Võ Đức Trọng")
        self.assertEqual(lookup1["record"]["form_data"]["plot_number"], "128")
        self.assertIsNotNone(lookup1["schema"])

        # 2. Lookup bằng tiền tố 8 ký tự
        lookup2 = ProcedureService.lookup_application(short_id, owner_id=OWNER)
        self.assertIsNotNone(lookup2)
        self.assertEqual(lookup2["record"]["id"], record_id)

        # 3. Lookup bằng full UUID
        lookup3 = ProcedureService.lookup_application(record_id, owner_id=OWNER)
        self.assertIsNotNone(lookup3)
        self.assertEqual(lookup3["record"]["id"], record_id)
        print(f"\n[PASS] Lookup by composite code '{composite_code}' succeeded with full form_data!")


if __name__ == "__main__":
    unittest.main()
