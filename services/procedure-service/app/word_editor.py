"""Edit text runs without rebuilding the Word document or its tables."""
from hashlib import sha256
from io import BytesIO

from docx import Document
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.table import Table
from docx.text.paragraph import Paragraph


def document_model(data: bytes):
    doc = Document(BytesIO(data))
    runs = []

    def paragraph(element, parent):
        p = Paragraph(element, parent)
        items = []
        for run in p.runs:
            index = len(runs)
            runs.append(run)
            items.append({'id': index, 'text': run.text, 'bold': run.bold,
                          'italic': run.italic, 'underline': bool(run.underline),
                          'size': run.font.size.pt if run.font.size else None})
        # Empty paragraphs can also receive text and placeholders.
        if not items:
            index = len(runs)
            runs.append(p)
            items.append({'id': index, 'text': ''})
        return {'kind': 'paragraph', 'runs': items, 'align': {1: 'center', 2: 'right', 3: 'justify'}.get(p.alignment, 'left')}

    def blocks(element, parent):
        result = []
        for child in element:
            if child.tag == qn('w:p'):
                result.append(paragraph(child, parent))
            elif child.tag == qn('w:tbl'):
                table = Table(child, parent)
                # Read each XML cell once; merged-cell aliases must not duplicate edits.
                result.append({'kind': 'table', 'rows': [
                    [{'span': cell.grid_span,
                      'blocks': blocks(cell, table)} for cell in row.tc_lst]
                    for row in table._tbl.tr_lst]})
        return result

    model = blocks(doc.element.body, doc)
    return doc, runs, {'version': sha256(data).hexdigest(), 'blocks': model}


def edit_document(data: bytes, edits: list[dict]) -> bytes:
    doc, runs, _ = document_model(data)
    seen = set()
    for edit in edits:
        index = edit['id']
        if index in seen or index < 0 or index >= len(runs):
            raise ValueError('Vị trí sửa trong Word không hợp lệ. Hãy mở lại file.')
        seen.add(index)
        target = runs[index]
        if isinstance(target, Paragraph):
            target.add_run(edit['text'])
        else:
            # Run.text replaces every child, including embedded images. Replace
            # only textual nodes and keep drawings, bookmarks and run properties.
            text_tags = {qn('w:t'), qn('w:tab'), qn('w:br'), qn('w:cr')}
            element = target._r
            old = [child for child in element if child.tag in text_tags]
            position = element.index(old[0]) if old else len(element)
            for child in old:
                element.remove(child)
            replacement = OxmlElement('w:r')
            replacement.text = edit['text']
            for offset, child in enumerate(list(replacement)):
                element.insert(position + offset, child)
    output = BytesIO()
    doc.save(output)
    return output.getvalue()
