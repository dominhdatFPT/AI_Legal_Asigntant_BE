"""Run with PYTHONPATH=backend/services/document-service python this_file.py."""
import unittest

from app.services.identity_parser import (ADDITIONAL_FIELDS, FIELDS, field_diagnostics,
                                          parse_additional_fields, parse_fields, resolve_label)
from app.services.vietocr_local import residence_value_regions


def line(text):
    return {"text": text, "confidence": .98,
            "words": [{"text": text, "confidence": .98}]}


class SemanticLabels(unittest.TestCase):
    def test_aliases_and_ocr_slips(self):
        cases = {
            "Nơi c ư trú": "residence_address",
            "Place of residenee": "residence_address",
            "Date of expirv": "expiry_date",
            "Số định danh cá nhân": "identity_number",
            "Registered place of birth": "registered_birth_place",
            "Nơi khai sinh": "place_of_origin",
            "Địa chỉ đăng ký thường trú": "permanent_address",
            "Issued by": "issue_place",
            "DOB": "date_of_birth",
            "Gender": "sex",
            "Citizenship": "nationality",
        }
        for label, field in cases.items():
            with self.subTest(label=label):
                self.assertEqual(resolve_label(label)["field"], field)
        self.assertEqual(resolve_label("Số nhà 12")["field"], "unknown_field")

    def test_fields_keep_distinct_meanings_and_dates(self):
        raw = {"side": "front", "lines": [line(text) for text in (
            "Họ và tên: NGUYỄN VĂN AN", "No. 012345678901", "Ngày sinh: 01.02.2000",
            "Nơi thường trú: 2 Lê Lợi, Hà Nội", "Place of residenee: 1 Main St",
            "Date of expirv: 2030-02-01", "Registered place of birth: Hải Phòng",
        )]}
        fields, extra = parse_fields(raw), parse_additional_fields(raw)
        self.assertEqual(fields["identity_number"], "012345678901")
        self.assertEqual(fields["date_of_birth"], "2000-02-01")
        self.assertEqual(fields["permanent_address"], "2 Lê Lợi, Hà Nội")
        self.assertEqual(extra["residence_address"], "1 Main St")
        self.assertEqual(extra["registered_birth_place"], "Hải Phòng")
        self.assertEqual(extra["expiry_date"], "2030-02-01")
        diagnostics = field_diagnostics(raw, {}, {**fields, **extra})
        self.assertEqual(set(diagnostics), set(FIELDS + ADDITIONAL_FIELDS))
        self.assertEqual(diagnostics["nationality"]["reason"], "not_present_on_card")

    def test_bilingual_permanent_heading_does_not_become_current_residence(self):
        raw = {"side": "front", "lines": [line("Nơi thường trú / Place of residence:"),
                                           line("12 Đường Kiểm Thử, Tỉnh Giả Lập")]}
        self.assertEqual(parse_fields(raw)["permanent_address"], "12 Đường Kiểm Thử, Tỉnh Giả Lập")
        self.assertIsNone(parse_additional_fields(raw)["residence_address"])
        raw["attempts"] = [{"source": "vietocr_field", "field": "residence_address",
                            "lines": [line("Place ofresidence")]}]
        diagnostics = field_diagnostics(raw, {}, {"residence_address": None})
        self.assertEqual(diagnostics["residence_address"]["reason"], "not_present_on_card")

    def test_two_independent_address_regions_restore_only_matching_suffix(self):
        raw = {"side": "back", "lines": [{"text": "Ấp Mẫu", "confidence": .3,
               "words": [{"text": "Ấp", "confidence": .96}, {"text": "Mẫu", "confidence": .94}]}],
               "attempts": [
                   {"side": "back", "source": "vietocr_field", "field": "residence_address",
                    "lines": [{"text": "Áp Mẫu Xã Thư, Tỉnh Giả", "confidence": .80}]},
                   {"side": "back", "source": "vietocr_field", "field": "registered_birth_place",
                    "lines": [{"text": "Xã Thử, Tỉnh Giả", "confidence": .91}]},
               ]}
        extra = parse_additional_fields(raw)
        self.assertEqual(extra["residence_address"], "Ấp Mẫu Xã Thử, Tỉnh Giả")
        self.assertEqual(extra["registered_birth_place"], "Xã Thử, Tỉnh Giả")

    def test_damaged_english_residence_heading_still_locates_its_two_rows(self):
        heading = {"text": "of vesicence: Ấp Duyên Lãng",
                   "polygon": [[112, 64], [652, 64], [652, 119], [112, 119]],
                   "words": [
                       {"text": "of", "bbox": [309, 64, 15, 55]},
                       {"text": "vesicence:", "bbox": [337, 75, 98, 40]},
                       {"text": "Ấp", "bbox": [450, 71, 36, 32]},
                       {"text": "Duyên", "bbox": [496, 75, 82, 27]},
                       {"text": "Lãng", "bbox": [589, 75, 63, 25]}]}
        suffix = {"text": "Nhân Nghĩa, Cẩm Mỹ, Đồng Nai",
                  "polygon": [[109, 102], [558, 102], [558, 163]], "words": []}
        raw = {"side": "back", "ocr_size": {"width": 1200, "height": 720},
               "lines": [heading, suffix]}
        regions = residence_value_regions(raw, 1200, 720)
        self.assertIsNotNone(regions)
        self.assertLess(regions["detail"][0], regions["detail"][2])
        self.assertLess(regions["suffix"][1], regions["suffix"][3])

    def test_complete_old_card_address_overrides_only_truncated_crop(self):
        complete = "Khu Tiên Xá Thị trấn Vương, Tiên Lữ, Hưng Yên"
        raw = {"side": "front", "lines": [], "attempts": [
            {"lines": [line("Nơi thường trú: Khu T Thị tran Vương, Tiên Lữ, Hưng Yên")]},
            {"source": "vietocr_field", "field": "permanent_address", "variant": "old_card_layout",
             "lines": [{"text": complete, "confidence": .85}]},
        ]}
        self.assertEqual(parse_fields(raw)["permanent_address"], complete)
        raw["attempts"][0]["lines"] = [line("Nơi thường trú / Place of residenoe: Khu T Thị tran Vương, Tiên Lữ, Hưng Yên")]
        self.assertEqual(parse_fields(raw)["permanent_address"], complete)
        raw["attempts"][0]["lines"] = [line("Nơi thường trú: Khu Khác Thị tran Vương, Tiên Lữ, Hưng Yên")]
        self.assertIsNone(parse_fields(raw)["permanent_address"])


if __name__ == "__main__":
    unittest.main()
