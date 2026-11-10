"""Run inside procedure-service: python - < this file."""
from io import BytesIO
from hashlib import sha256
import unittest

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from app.word_editor import document_model, edit_document


class WordEditorTests(unittest.TestCase):
    def fixture(self):
        doc = Document()
        doc.sections[0].header.paragraphs[0].text = 'Đầu trang giữ nguyên'
        doc.add_paragraph('Văn bản gốc').runs[0].bold = True
        table = doc.add_table(rows=2, cols=3)
        table.cell(0, 0).merge(table.cell(0, 1)).text = 'Ô gộp'
        table.cell(1, 0).text = '{{#people}}{{people.index}}'
        table.cell(1, 1).text = '{{people.name}}'
        table.cell(1, 2).text = '{{/people}}'
        doc.add_paragraph('')
        output = BytesIO()
        doc.save(output)
        return output.getvalue()

    def test_text_and_token_changes_preserve_structure(self):
        original = self.fixture()
        _, _, model = document_model(original)
        self.assertEqual(model['version'], sha256(original).hexdigest())
        self.assertEqual(model['blocks'][1]['rows'][0][0]['span'], 2)
        edit_id = model['blocks'][1]['rows'][1][1]['blocks'][0]['runs'][0]['id']
        output = edit_document(original, [{'id': 0, 'text': 'Văn bản đã sửa'}, {'id': edit_id, 'text': '{{people.ho_ten}}'}])
        doc = Document(BytesIO(output))
        self.assertEqual(doc.paragraphs[0].text, 'Văn bản đã sửa')
        self.assertTrue(doc.paragraphs[0].runs[0].bold)
        self.assertEqual(doc.tables[0].cell(1, 1).text, '{{people.ho_ten}}')
        self.assertEqual(doc.tables[0].cell(0, 0)._tc.grid_span, 2)
        self.assertEqual(doc.sections[0].header.paragraphs[0].text, 'Đầu trang giữ nguyên')

    def test_empty_paragraph_can_receive_token(self):
        original = self.fixture()
        _, _, model = document_model(original)
        index = model['blocks'][-1]['runs'][0]['id']
        output = edit_document(original, [{'id': index, 'text': '{{kinh_gui}}'}])
        self.assertEqual(Document(BytesIO(output)).paragraphs[-1].text, '{{kinh_gui}}')

    def test_drawing_in_edited_run_is_not_removed(self):
        doc = Document()
        run = doc.add_paragraph().add_run('Chữ')
        run._r.append(OxmlElement('w:drawing'))
        data = BytesIO()
        doc.save(data)
        output = edit_document(data.getvalue(), [{'id': 0, 'text': 'Chữ đã sửa'}])
        changed = Document(BytesIO(output))
        self.assertIsNotNone(changed.paragraphs[0].runs[0]._r.find(qn('w:drawing')))

    def test_reject_invalid_or_duplicate_positions(self):
        for edits in [[{'id': 99999, 'text': 'x'}], [{'id': 0, 'text': 'a'}, {'id': 0, 'text': 'b'}]]:
            with self.assertRaises(ValueError):
                edit_document(self.fixture(), edits)


if __name__ == '__main__':
    unittest.main()
