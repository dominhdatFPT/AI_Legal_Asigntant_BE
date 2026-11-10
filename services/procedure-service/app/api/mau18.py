"""Mẫu 18: generate a document and PDF preview without storing user input."""
from copy import deepcopy
from datetime import date as Date
from html import escape
from io import BytesIO
from pathlib import Path
import re
from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph as WordParagraph
from docx.table import Table as WordTable
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, PageBreak
from sqlalchemy import text
from app.core.security import get_current_user
from app.repositories.procedure_repository import db_connection

router = APIRouter()
PILOT_FILE = 'mau18-hai-nguoi.docx'


class CleanTextModel(BaseModel):
    @field_validator('*', mode='after')
    @classmethod
    def validate_xml_text(cls, value):
        if isinstance(value, str) and any(ord(c) < 32 and c not in '\n\r\t' for c in value):
            raise ValueError('Văn bản chứa ký tự không hợp lệ')
        return value


class Person(CleanTextModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    address: str = Field(min_length=1, max_length=240)
    identity: str = Field(min_length=1, max_length=100)
    tax: str = Field(default='', max_length=50)
    share: str = Field(default='', max_length=80)
    note: str = Field(default='', max_length=100)


class Submission(CleanTextModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    receiving_agency: str = Field(min_length=1, max_length=200)
    people: list[Person] = Field(min_length=1, max_length=20)
    phone: str = Field(default='', max_length=40)
    email: str = Field(default='', max_length=150)
    change_content: str = Field(min_length=1, max_length=1200)
    tax_exemption: str = Field(default='', max_length=400)
    attached_documents: str = Field(min_length=1, max_length=800)
    place: str = Field(default='', max_length=100)
    signer_name: str = Field(default='', max_length=120)
    date: Date | None = None

    @field_validator('date', mode='before')
    @classmethod
    def blank_date(cls, value):
        return None if value == '' else value


def fill_template(source: bytes, submission: Submission) -> bytes:
    a = submission.people[0]
    signed = submission.date
    values = dict(receiving_agency=submission.receiving_agency, representative_name=a.name,
        representative_id=a.identity, representative_address=a.address, representative_tax=a.tax,
        phone=submission.phone, email=submission.email, change_content=submission.change_content,
        tax_exemption=submission.tax_exemption, attached_documents=submission.attached_documents,
        place=submission.place, day=str(signed.day) if signed else '',
        month=str(signed.month) if signed else '', year=str(signed.year) if signed else '')
    for n, person in enumerate(submission.people, 1):
        values.update({f'person{n}_name': person.name, f'person{n}_address': person.address,
            f'person{n}_id': person.identity, f'person{n}_tax': person.tax,
            f'person{n}_share': person.share, f'person{n}_note': person.note})
    doc = Document(BytesIO(source))
    if submission.signer_name:
        for paragraph in doc.paragraphs:
            if '(Ký, ghi rõ họ tên và đóng dấu nếu có)' in paragraph.text:
                paragraph.add_run('\n' + submission.signer_name)
                break
    if len(submission.people) == 1:
        body = doc.element.body
        children = list(body)
        annex = next(i for i, el in enumerate(children) if 'Bảng 18a: Danh sách' in ''.join(el.itertext()))
        # The preceding paragraph is the page break before the annex.
        if annex and children[annex - 1].xpath('.//w:br[@w:type="page"]'):
            annex -= 1
        for el in children[annex:]:
            if el.tag != qn('w:sectPr'):
                body.remove(el)
    else:
        table = doc.tables[0]
        # Repeat the headings when a long list continues onto the next page.
        trpr = table.rows[0]._tr.get_or_add_trPr()
        from docx.oxml import OxmlElement
        trpr.append(OxmlElement('w:tblHeader'))
        for n in range(3, len(submission.people) + 1):
            row = deepcopy(table.rows[2]._tr)
            for node in row.iter(qn('w:t')):
                node.text = (node.text or '').replace('person2_', f'person{n}_')
            row.xpath('./w:tc[1]//w:t')[0].text = str(n)
            table._tbl.append(row)
    paragraphs = list(doc.paragraphs)
    for table in doc.tables:
        paragraphs.extend(p for row in table.rows for cell in row.cells for p in cell.paragraphs)
    for paragraph in paragraphs:
        for run in paragraph.runs:
            for key, value in values.items():
                marker = '{{' + key + '}}'
                if marker in run.text:
                    run.text = run.text.replace(marker, value or '……………………')
    # Chú thích trong phần đơn là chỉ số trên; giữ các số ở trang hướng dẫn bình thường.
    for paragraph in doc.paragraphs:
        if paragraph.text.startswith('Hướng dẫn kê khai đơn:'):
            break
        for run in list(paragraph.runs):
            parts = re.split(r'(\([1-7]\))', run.text)
            if len(parts) == 1:
                continue
            anchor = run._r
            for part in parts:
                if not part:
                    continue
                clone = deepcopy(run._r)
                anchor.addprevious(clone)
                from docx.text.run import Run
                fragment = Run(clone, paragraph)
                fragment.text = part
                if re.fullmatch(r'\([1-7]\)', part):
                    fragment.font.superscript = True
            anchor.getparent().remove(anchor)
    out = BytesIO()
    doc.save(out)
    return out.getvalue()


def render_preview(docx: bytes) -> bytes:
    """Render the same filled text in PDF; pagination may differ from Word."""
    fonts = Path(__file__).resolve().parents[1] / 'fonts'
    for name, file in [('M18', 'LiberationSerif-Regular.ttf'), ('M18-Bold', 'LiberationSerif-Bold.ttf'),
                       ('M18-Italic', 'LiberationSerif-Italic.ttf'), ('M18-BoldItalic', 'LiberationSerif-BoldItalic.ttf')]:
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(fonts / file)))
    pdfmetrics.registerFontFamily('M18', normal='M18', bold='M18-Bold', italic='M18-Italic', boldItalic='M18-BoldItalic')
    doc = Document(BytesIO(docx))
    story = []
    def para(p, size=11):
        text_parts = []
        for run in p.runs:
            value = escape(run.text).replace('\n', '<br/>')
            if run.bold:
                value = f'<b>{value}</b>'
            if run.italic:
                value = f'<i>{value}</i>'
            if run.font.superscript:
                value = f'<super>{value}</super>'
            text_parts.append(value)
        alignment = {1: TA_CENTER, 2: TA_RIGHT}.get(p.alignment, TA_LEFT)
        style = ParagraphStyle('m18', fontName='M18', fontSize=size, leading=size * 1.25,
                               alignment=alignment, spaceAfter=3, wordWrap='CJK')
        return Paragraph(''.join(text_parts) or ' ', style)
    for child in doc.element.body:
        if child.tag == qn('w:p'):
            p = WordParagraph(child, doc)
            if child.xpath('.//w:br[@w:type="page"]'):
                story.append(PageBreak())
            elif p.text.strip():
                story.append(para(p))
        elif child.tag == qn('w:tbl'):
            table = WordTable(child, doc)
            rows = [[para(cell.paragraphs[0], 7) for cell in row.cells] for row in table.rows]
            widths = [25, 90, 82, 52, 95, 80, 57]
            factor = (A4[0] - 100) / sum(widths)
            pdf_table = Table(rows, colWidths=[x * factor for x in widths], repeatRows=1)
            pdf_table.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), .4, colors.black),
                                         ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                                         ('LEFTPADDING', (0, 0), (-1, -1), 2),
                                         ('RIGHTPADDING', (0, 0), (-1, -1), 2)]))
            story.append(pdf_table)
    out = BytesIO()
    SimpleDocTemplate(out, pagesize=A4, leftMargin=50, rightMargin=50, topMargin=48, bottomMargin=48).build(story)
    return out.getvalue()


@router.get('/templates/mau18-two-person/preview')
def preview_mau18():
    with db_connection() as conn:
        row = conn.execute(text('SELECT preview_pdf FROM public.form_templates WHERE domain = :domain AND file_name = :filename AND is_active = true'),
                           {'domain': 'land', 'filename': PILOT_FILE}).first()
    if not row or not row[0]:
        raise HTTPException(status_code=404, detail='Chưa có bản xem trước mẫu đơn.')
    return Response(bytes(row[0]), media_type='application/pdf', headers={'Content-Disposition': 'inline; filename="mau18-tham-khao.pdf"', 'Cache-Control': 'no-store'})


@router.get('/templates/mau18-two-person/preview-page/{page_number}')
def preview_mau18_page(page_number: int):
    if not 1 <= page_number <= 3:
        raise HTTPException(status_code=404, detail='Không tìm thấy trang mẫu đơn.')
    with db_connection() as conn:
        row = conn.execute(text('''SELECT p.image_data FROM public.form_template_preview_pages p
          JOIN public.form_templates t ON t.id = p.template_id
          WHERE t.domain = :domain AND t.file_name = :filename AND t.is_active = true AND p.page_number = :page'''),
          {'domain': 'land', 'filename': PILOT_FILE, 'page': page_number}).first()
    if not row:
        raise HTTPException(status_code=404, detail='Không tìm thấy trang mẫu đơn.')
    return Response(bytes(row[0]), media_type='image/png', headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


def source_mau18():
    with db_connection() as conn:
        row = conn.execute(text('SELECT file_data FROM public.form_templates WHERE domain = :domain AND file_name = :filename AND is_active = true'),
                           {'domain': 'land', 'filename': PILOT_FILE}).first()
    if not row:
        raise HTTPException(status_code=503, detail='Mẫu Word chưa được cấu hình trên máy chủ.')
    return bytes(row[0])


@router.post('/templates/mau18-two-person/docx')
def make_mau18(submission: Submission, user=Depends(get_current_user)):
    return Response(fill_template(source_mau18(), submission),
        media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        headers={'Content-Disposition': 'attachment; filename="don-dang-ky-bien-dong-mau18.docx"', 'Cache-Control': 'no-store'})


@router.post('/templates/mau18-two-person/preview-filled')
def preview_filled_mau18(submission: Submission, user=Depends(get_current_user)):
    return Response(render_preview(fill_template(source_mau18(), submission)), media_type='application/pdf',
                    headers={'Content-Disposition': 'inline; filename="mau18-xem-truoc.pdf"', 'Cache-Control': 'no-store'})
