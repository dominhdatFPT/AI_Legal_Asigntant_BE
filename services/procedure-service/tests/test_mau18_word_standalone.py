import ast
from datetime import date
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import unittest
from docx import Document

ROOT = Path(__file__).resolve().parents[1]
tree = ast.parse((ROOT / 'app/api/mau18.py').read_text(encoding='utf-8'))
function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'fill_template')
function.returns = None
for arg in function.args.args:
    arg.annotation = None
module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
namespace = {'BytesIO': BytesIO, 'Document': Document}
exec(compile(module, 'mau18.py', 'exec'), namespace)


class TestMau18(unittest.TestCase):
    def test_filled_word_has_both_people_and_no_markers(self):
        def person(name):
            return SimpleNamespace(name=name, address='Đà Nẵng', identity='123456789012', tax='', share='', note='')
        data = SimpleNamespace(people=[person('NGUYỄN VĂN AN'), person('TRẦN THỊ BÌNH')],
            receiving_agency='Văn phòng đăng ký đất đai', phone='', email='', change_content='Nhận chuyển nhượng',
            tax_exemption='', attached_documents='Giấy chứng nhận', place='Đà Nẵng', date=date(2026,10,29))
        result = namespace['fill_template']((ROOT / 'database/mau18-two-person.docx').read_bytes(), data)
        doc = Document(BytesIO(result))
        content = '\n'.join([p.text for p in doc.paragraphs] + [cell.text for table in doc.tables for row in table.rows for cell in row.cells])
        self.assertIn('NGUYỄN VĂN AN', content)
        self.assertIn('TRẦN THỊ BÌNH', content)
        self.assertIn('ngày 29 tháng 10 năm 2026', content)
        self.assertNotIn('{{', content)
        self.assertIn('Bảng 18a', content)


if __name__ == '__main__':
    unittest.main()
