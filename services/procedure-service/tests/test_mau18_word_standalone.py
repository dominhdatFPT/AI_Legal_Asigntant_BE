from datetime import date
from io import BytesIO
from pathlib import Path
import unittest
from docx import Document

ROOT = Path(__file__).resolve().parents[1]
from app.api.mau18 import Person, Submission, fill_template


class TestMau18(unittest.TestCase):
    def test_filled_word_has_both_people_and_no_markers(self):
        def person(name):
            return Person(name=name, address='Đà Nẵng', identity='123456789012', tax='', share='', note='')
        data = Submission(people=[person('NGUYỄN VĂN AN'), person('TRẦN THỊ BÌNH')],
            receiving_agency='Văn phòng đăng ký đất đai', phone='', email='', change_content='Nhận chuyển nhượng',
            tax_exemption='', attached_documents='Giấy chứng nhận', place='Đà Nẵng', signer_name='NGUYỄN VĂN AN', date=date(2026,10,29))
        result = fill_template((ROOT / 'database/mau18-two-person.docx').read_bytes(), data)
        doc = Document(BytesIO(result))
        content = '\n'.join([p.text for p in doc.paragraphs] + [cell.text for table in doc.tables for row in table.rows for cell in row.cells])
        self.assertIn('NGUYỄN VĂN AN', content)
        self.assertIn('TRẦN THỊ BÌNH', content)
        self.assertIn('ngày 29 tháng 10 năm 2026', content)
        self.assertNotIn('{{', content)
        self.assertIn('Bảng 18a', content)


if __name__ == '__main__':
    unittest.main()
