"""Standalone regression checks for optional repeating Word attachments.

Run in procedure-service, where python-docx is installed, from stdin or a file.
Only the document functions are loaded; no auth/database setup is needed.
"""
import ast
from copy import deepcopy
from datetime import date
from io import BytesIO
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table as WordTable
from docx.text.paragraph import Paragraph as WordParagraph

source = ast.parse(Path('app/api/custom_form_templates.py').read_text(encoding='utf-8'))
names = {'_token_name', '_replace_paragraph', '_paragraphs', '_document_field_value', 'fill_word'}
functions = ast.Module(body=[node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in names], type_ignores=[])
TOKEN = re.compile(r'\{\{\s*([^{}]+?)\s*\}\}')
exec('from __future__ import annotations\n' + ast.unparse(functions), globals())


class OptionalRepeatExport(unittest.TestCase):
    def export(self, rows, condition=None):
        doc = Document()
        doc.add_paragraph('Nội dung chính cần giữ')
        doc.add_paragraph('Bảng 18a: Danh sách người sử dụng đất')
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = 'STT'
        table.cell(0, 1).text = 'Tên người'
        table.cell(1, 0).text = '{{#people}}{{people.index}}'
        table.cell(1, 1).text = '{{people.name}}{{/people}}'
        doc.add_paragraph('(*) Chú thích của bảng')
        doc.add_paragraph('NGƯỜI LÀM ĐƠN')
        doc.add_table(rows=1, cols=1).cell(0, 0).text = 'Bảng khác cần giữ'
        data = BytesIO()
        doc.save(data)
        content = {'sections': [], 'repeat_groups': [{'key': 'people', 'title': 'Danh sách người sử dụng đất', 'condition': condition, 'fields': [{'key': 'name', 'type': 'text'}]}]}
        filled = fill_word(data.getvalue(), content, SimpleNamespace(values={}, groups={'people': rows}))
        return Document(BytesIO(filled))

    def test_empty_attachment_removed(self):
        doc = self.export([])
        self.assertEqual(len(doc.tables), 1)
        text = '\n'.join(p.text for p in doc.paragraphs)
        self.assertNotIn('Bảng 18a', text)
        self.assertNotIn('(*)', text)
        self.assertIn('Nội dung chính cần giữ', text)
        self.assertIn('NGƯỜI LÀM ĐƠN', text)
        self.assertEqual(doc.tables[0].cell(0, 0).text, 'Bảng khác cần giữ')

    def test_two_people_keep_header_and_numbering(self):
        doc = self.export([{'name': 'An'}, {'name': 'Bình'}])
        self.assertEqual(len(doc.tables), 2)
        self.assertEqual(len(doc.tables[0].rows), 3)
        self.assertEqual(doc.tables[0].cell(1, 0).text, '1')
        self.assertEqual(doc.tables[0].cell(2, 0).text, '2')
        self.assertEqual(doc.tables[0].cell(2, 1).text, 'Bình')
        self.assertTrue(any('Bảng 18a' in p.text for p in doc.paragraphs))

    def test_hidden_group_removed_even_with_rows(self):
        doc = self.export([{'name': 'An'}], {'field': 'include_people', 'equals': 'yes'})
        self.assertEqual(len(doc.tables), 1)


if __name__ == '__main__':
    unittest.main()
