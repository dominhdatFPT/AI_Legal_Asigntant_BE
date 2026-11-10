"""Admin configured Word forms, public fill/export, and saved custom submissions."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from html import escape
from io import BytesIO
import logging
from pathlib import Path
import re
import unicodedata
import urllib.parse
import uuid

logger = logging.getLogger(__name__)


def _content_disposition(filename: str, disposition: str = 'inline') -> str:
    """Format RFC 5987 Content-Disposition header safely for ASCII-only HTTP headers."""
    base_name = Path(filename or 'tai-lieu').name
    clean_base = base_name.replace('đ', 'd').replace('Đ', 'D')
    ascii_name = unicodedata.normalize('NFKD', clean_base).encode('ascii', 'ignore').decode('ascii')
    ascii_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', ascii_name).strip('_') or 'document'
    encoded_name = urllib.parse.quote(base_name)
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}"
import zipfile
from typing import Any

from common_auth.core import CurrentUser
from common_auth.fastapi_dep import get_current_user, require_role
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table as WordTable
from docx.text.paragraph import Paragraph as WordParagraph
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle
from sqlalchemy import text

from contextlib import contextmanager
from hashlib import sha256
import time
from app.database import database_url
from app.repositories.procedure_repository import db_connection
from app.builtin_form_templates import seed_builtin_form_templates

_BUILTIN_SEEDED = False
_PUBLIC_LIST_CACHE: dict[tuple[str | None, str | None], tuple[float, dict]] = {}
_PUBLIC_LIST_TTL = 60.0
_PDF_CACHE: dict[str, bytes] = {}


def _invalidate_template_caches() -> None:
    _PUBLIC_LIST_CACHE.clear()
    _PDF_CACHE.clear()


def ensure_builtin_seeded(conn) -> None:
    global _BUILTIN_SEEDED
    if _BUILTIN_SEEDED:
        return
    try:
        seed_builtin_form_templates(conn)
        _BUILTIN_SEEDED = True
    except Exception:
        pass


@contextmanager
def _seeded_read_connection():
    with db_connection(write=not _BUILTIN_SEEDED) as conn:
        if not _BUILTIN_SEEDED:
            ensure_builtin_seeded(conn)
        yield conn

router = APIRouter()
MAX_WORD = 10 * 1024 * 1024
MAX_PDF = 15 * 1024 * 1024
DOMAINS = {'dat_dai', 'ho_kinh_doanh', 'doanh_nghiep'}
WORD_KEY = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
TOKEN = re.compile(r'\{\{\s*([^{}]+?)\s*\}\}')


def _token_name(value: str) -> str:
    value = value.strip()
    marker = re.fullmatch(r'([#/])\s*([a-z][a-z0-9_]*)', value)
    return marker.group(1) + marker.group(2) if marker else value


class TemplateBody(BaseModel):
    model_config = ConfigDict(extra='allow')
    schema_version: int = 1
    name: str = Field(min_length=1, max_length=255)
    code: str = Field(default='', max_length=50)
    domain: str
    procedure_id: str | None = None
    sections: list[dict] = Field(min_length=1, max_length=80)
    repeat_groups: list[dict] = Field(default_factory=list, max_length=30)


class FillBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    values: dict[str, str] = Field(default_factory=dict, max_length=1000)
    groups: dict[str, list[dict[str, str]]] = Field(default_factory=dict, max_length=30)


class SaveBody(FillBody):
    status: str = 'created'


class WordRunEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: int = Field(ge=0)
    text: str = Field(max_length=20000)


class WordEditBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: str = Field(pattern=r'^[a-f0-9]{64}$')
    edits: list[WordRunEdit] = Field(max_length=2000)


def _validate_content(content: dict, *, for_publish: bool = True) -> list[str]:
    errors = []
    if content.get('domain') not in DOMAINS:
        errors.append('Chọn lĩnh vực hợp lệ cho mẫu.')
    sections = content.get('sections') or []
    if not sections:
        errors.append('Mẫu cần có ít nhất một mục.')
    if not for_publish:
        return errors
    word_keys = []
    known_fields = set()
    for si, section in enumerate(sections, 1):
        if not str(section.get('title', '')).strip():
            errors.append(f'Mục {si} chưa có tên.')
        if not section.get('blocks'):
            errors.append(f"{section.get('title') or f'Mục {si}'} chưa có trường.")
        for block in section.get('blocks', []):
            if not block.get('instance_id') or not block.get('fields'):
                errors.append(f'Một trường trong mục {si} chưa hoàn chỉnh.')
                continue
            for field in block['fields']:
                known_fields.add(f"{block['instance_id']}.{field.get('key')}")
                key = field.get('word_key')
                if not isinstance(key, str) or not WORD_KEY.fullmatch(key):
                    errors.append(f"Trường “{field.get('label') or 'chưa đặt tên'}” thiếu mã điền hợp lệ.")
                else:
                    word_keys.append(key)
                if not str(field.get('label', '')).strip():
                    errors.append('Có trường chưa có tên hiển thị.')
                if field.get('type') not in {'text','textarea','number','date','past_date','dob','issue_date','cccd','phone','email','select','checkbox'}:
                    errors.append(f"Kiểu trường “{field.get('label') or key}” chưa được hỗ trợ.")
                if field.get('type') == 'select' and not field.get('options'):
                    errors.append(f"Trường “{field.get('label') or key}” cần có ít nhất một lựa chọn.")
                if field.get('condition'):
                    cond = field['condition']
                    if not isinstance(cond, dict) or not cond.get('field') or 'equals' not in cond:
                        errors.append(f"Điều kiện hiển thị của “{field.get('label') or key}” chưa hợp lệ.")
                    elif cond.get('field') not in known_fields and not any(f"{b.get('instance_id')}.{x.get('key')}" == cond.get('field') for s in sections for b in s.get('blocks', []) for x in b.get('fields', [])):
                        errors.append(f"Điều kiện của “{field.get('label') or key}” trỏ tới trường không tồn tại.")
    if len(word_keys) != len(set(word_keys)):
        errors.append('Có hai trường đang dùng cùng mã Word; hãy đổi một mã để không nhầm dữ liệu.')
    group_keys = set()
    all_group_fields = set()
    for group in content.get('repeat_groups', []):
        key = group.get('key', '')
        if not isinstance(key, str) or not WORD_KEY.fullmatch(key) or key in group_keys:
            errors.append('Mã nhóm lặp không hợp lệ hoặc bị trùng.')
            continue
        group_keys.add(key)
        fields = group.get('fields', [])
        if not fields:
            errors.append(f"Nhóm “{group.get('title') or key}” cần ít nhất một trường.")
        for field in fields:
            if not WORD_KEY.fullmatch(str(field.get('key', ''))) or not WORD_KEY.fullmatch(str(field.get('word_key', ''))):
                errors.append(f"Một trường trong nhóm “{group.get('title') or key}” chưa có mã hợp lệ.")
            all_group_fields.add(f'{key}.{field.get("key")}')
            if field.get('type') not in {'text','textarea','number','date','email','phone','select','checkbox'}:
                errors.append(f"Kiểu trường nhóm “{group.get('title') or key}” chưa được hỗ trợ.")
            if field.get('type') == 'select' and not field.get('options'):
                errors.append(f"Trường “{field.get('label') or ''}” cần có lựa chọn.")
    for group in content.get('repeat_groups', []):
        condition = group.get('condition')
        if condition:
            if not isinstance(condition, dict) or not condition.get('field') or 'equals' not in condition:
                errors.append(f"Điều kiện hiển thị của nhóm “{group.get('title') or 'lặp'}” chưa hợp lệ.")
            elif condition.get('field') not in known_fields:
                errors.append(f"Điều kiện của nhóm “{group.get('title') or 'lặp'}” trỏ tới trường không tồn tại.")
    return errors


def _document_text(doc: Document) -> str:
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        parts.extend(cell.text for row in table.rows for cell in row.cells)
    for section in doc.sections:
        parts.extend(p.text for p in _paragraphs(section.header))
        parts.extend(p.text for p in _paragraphs(section.footer))
    return '\n'.join(parts)


def analyze_document(content: dict, file_data: bytes) -> dict:
    try:
        doc = Document(BytesIO(file_data))
    except Exception as exc:
        raise HTTPException(422, 'Không đọc được file Word. Hãy tải lên tệp .docx hợp lệ.') from exc
    found = [_token_name(token) for token in TOKEN.findall(_document_text(doc))]
    expected = {field['word_key']: field.get('label', field['word_key'])
                for section in content.get('sections', []) for block in section.get('blocks', []) if block.get('block_key') != 'repeat_group'
                for field in block.get('fields', []) if field.get('word_key')}
    groups = {group['key']: group for group in content.get('repeat_groups', [])}
    repeat_rows = []
    for table in doc.tables:
        for row in table.rows:
            row_text = ' '.join(cell.text for cell in row.cells)
            repeat_rows.append({_token_name(token) for token in TOKEN.findall(row_text)})
    valid_group_markers = {marker for key in groups for marker in (f'#{key}', f'/{key}')}
    group_index_tags = {f'{key}.index' for key in groups}
    unknown = sorted({token for token in found if token not in expected
                      and token not in valid_group_markers
                      and token not in group_index_tags
                      and not any(token in {f'{k}.{f["key"]}' for f in group.get('fields', [])} for k, group in groups.items())})
    unused = sorted(set(expected) - set(found))
    repeating = {}
    for key, group in groups.items():
        opening, closing = found.count('#' + key), found.count('/' + key)
        field_tags = [f'{key}.{field["key"]}' for field in group.get('fields', [])]
        if f'{key}.index' in found:
            field_tags.append(f'{key}.index')
        missing = [tag for tag in field_tags if tag not in found]
        if opening != 1 or closing != 1:
            repeating[key] = {'title': group.get('title', key), 'valid': False,
                              'message': f"Trong một dòng bảng cần đặt {{{{#{key}}}}} ở đầu và {{{{/{key}}}}} ở cuối (mỗi dấu một lần)."}
        elif missing:
            repeating[key] = {'title': group.get('title', key), 'valid': False,
                              'message': 'Chưa tìm thấy dấu: ' + ', '.join('{{' + x + '}}' for x in missing)}
        elif not any(f'#{key}' in row and f'/{key}' in row and all(tag in row for tag in field_tags) for row in repeat_rows):
            repeating[key] = {'title': group.get('title', key), 'valid': False,
                              'message': f"Dấu mở/đóng và mọi trường của nhóm “{group.get('title', key)}” phải nằm cùng một dòng của bảng Word."}
        else:
            repeating[key] = {'title': group.get('title', key), 'valid': True,
                              'message': 'Đã tìm thấy dấu trong file Word.'}
    errors = _validate_content(content)
    raw_text = _document_text(doc)
    if raw_text.count('{{') != raw_text.count('}}'):
        errors.append('File Word có dấu mở/đóng không cân bằng; kiểm tra lại các cặp {{...}}.')
    if unknown:
        errors.append('File Word có dấu chưa liên kết: ' + ', '.join('{{'+x+'}}' for x in unknown))
    if unused:
        errors.append('Các trường chưa có vị trí trong Word: ' + ', '.join(expected[x] for x in unused))
    errors.extend(value['message'] for value in repeating.values() if not value['valid'])
    return {'valid': not errors, 'found': sorted(set(found)), 'missing_fields': unused,
            'unknown_tokens': unknown, 'repeat_groups': repeating, 'errors': errors}


def _replace_paragraph(paragraph, replacements: dict[str, str]):
    runs = paragraph.runs
    if not runs:
        return
    run_texts = [run.text for run in runs]
    combined = ''.join(run_texts)
    matches = list(TOKEN.finditer(combined))
    if not matches:
        return

    def locate(position, *, end=False):
        cursor = 0
        for index, value in enumerate(run_texts):
            limit = cursor + len(value)
            contains = cursor < position <= limit if end else cursor <= position < limit
            if value and contains:
                return index, position - cursor
            cursor = limit
        return None

    for match in reversed(matches):
        replacement = replacements.get('{{'+_token_name(match.group(1))+'}}')
        if replacement is None:
            continue
        start = locate(match.start())
        finish = locate(match.end(), end=True)
        if start is None or finish is None:
            continue
        start_index, start_offset = start
        end_index, end_offset = finish
        if start_index == end_index:
            run_texts[start_index] = run_texts[start_index][:start_offset] + replacement + run_texts[end_index][end_offset:]
        else:
            run_texts[start_index] = run_texts[start_index][:start_offset] + replacement
            for index in range(start_index + 1, end_index):
                run_texts[index] = ''
            run_texts[end_index] = run_texts[end_index][end_offset:]

    for run, value in zip(runs, run_texts):
        run.text = value


def _paragraphs(container):
    for p in container.paragraphs:
        yield p
    for table in container.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from _paragraphs(cell)


def _document_field_value(field: dict, value) -> str:
    value = str(value or '')
    if value and field.get('type') in {'date', 'past_date', 'dob', 'issue_date'}:
        try:
            return date.fromisoformat(value).strftime('%d/%m/%Y')
        except ValueError:
            pass
    return value


def fill_word(file_data: bytes, content: dict, payload: FillBody) -> bytes:
    doc = Document(BytesIO(file_data))
    groups = {group['key']: group for group in content.get('repeat_groups', [])}
    for table in doc.tables:
        table_groups = {
            key: group for key, group in groups.items()
            if any('#' + key in {_token_name(token) for token in TOKEN.findall(' '.join(cell.text for cell in row.cells))}
                   for row in table.rows)
        }
        def has_rows(group):
            condition = group.get('condition')
            visible = not condition or payload.values.get(condition['field']) == condition['equals']
            return visible and bool(payload.groups.get(group['key'], []))

        # A table used only by empty repeat groups must not leave a header-only
        # attachment in the exported document. Other, non-repeat tables stay intact.
        if table_groups and not any(has_rows(group) for group in table_groups.values()):
            previous = table._tbl.getprevious()
            following = table._tbl.getnext()
            caption_removed = False
            if previous is not None and previous.tag == qn('w:p'):
                caption = WordParagraph(previous, doc).text.strip()
                titles = {str(group.get('title', '')).strip().casefold() for group in table_groups.values()}
                if re.match(r'^(?:Bảng\s+[\w.]+\s*:|Table\s+[\w.]+\s*:)', caption, re.IGNORECASE) or (caption and caption.casefold() in titles):
                    previous.getparent().remove(previous)
                    caption_removed = True
            # Only remove an immediately adjacent table footnote after recognizing
            # its caption; unrelated text following a generic table is preserved.
            if caption_removed and following is not None and following.tag == qn('w:p'):
                if WordParagraph(following, doc).text.lstrip().startswith('(*)'):
                    following.getparent().remove(following)
            table._tbl.getparent().remove(table._tbl)
            continue
        for row in list(table.rows):
            row_tokens = {_token_name(token) for token in TOKEN.findall(' '.join(cell.text for cell in row.cells))}
            group = next((item for key, item in groups.items() if '#'+key in row_tokens), None)
            if not group:
                continue
            key = group['key']
            condition = group.get('condition')
            group_visible = not condition or payload.values.get(condition['field']) == condition['equals']
            group_values = payload.groups.get(key, []) if group_visible else []
            template_row = deepcopy(row._tr)
            for row_index, record in enumerate(group_values, 1):
                cloned = deepcopy(template_row)
                row._tr.addprevious(cloned)
                instance = next(table_row for table_row in WordTable(table._tbl, doc).rows
                                if table_row._tr is cloned)
                replacements = {}
                replacements['{{'+key+'.index}}'] = str(row_index)
                for field in group.get('fields', []):
                    value = _document_field_value(field, record.get(field['key'], ''))
                    if field.get('type') == 'checkbox': value = 'Có' if value.lower() in {'true','1','yes','có'} else 'Không'
                    replacements['{{'+key+'.'+field['key']+'}}'] = value
                replacements.update({'{{#'+key+'}}':'', '{{# '+key+' }}':'', '{{/'+key+'}}':'', '{{/ '+key+' }}':''})
                for cell in instance.cells:
                    for p in _paragraphs(cell): _replace_paragraph(p, replacements)
            table._tbl.remove(row._tr)
    replacements = {}
    for section in content.get('sections', []):
        for block in section.get('blocks', []):
            if block.get('block_key') == 'repeat_group':
                continue
            for field in block.get('fields', []):
                value = payload.values.get(f"{block['instance_id']}.{field['key']}", '')
                condition=field.get('condition')
                if condition and payload.values.get(condition['field']) != condition['equals']:
                    value=''
                if field.get('type') == 'checkbox':
                    value = 'Có' if str(value).lower() in {'true','1','yes','có'} else 'Không'
                replacements['{{'+field['word_key']+'}}'] = _document_field_value(field, value)
    for group in groups.values():
        for field in group.get('fields', []):
            replacements['{{'+group['key']+'.'+field['key']+'}}'] = ''
    for container in [doc, *[s.header for s in doc.sections], *[s.footer for s in doc.sections]]:
        for p in _paragraphs(container): _replace_paragraph(p, replacements)
    output = BytesIO()
    doc.save(output)
    return output.getvalue()


def _field_value_error(field: dict, value: str) -> str | None:
    kind = field.get('type')
    label = field.get('label') or 'Trường dữ liệu'
    if kind == 'email' and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        return f'{label}: email không hợp lệ.'
    if kind in {'date','past_date','dob','issue_date'}:
        try:
            parsed = date.fromisoformat(value)
            if kind in {'past_date','dob','issue_date'} and parsed > date.today():
                return f'{label}: ngày không được ở tương lai.'
        except ValueError:
            return f'{label}: ngày không hợp lệ.'
    if kind == 'phone':
        digits = re.sub(r'[\s.()\-]', '', value)
        if digits.startswith('+84'):
            digits = '0' + digits[3:]
        elif digits.startswith('84') and len(digits) == 11:
            digits = '0' + digits[2:]
        if not re.fullmatch(r'0[35789]\d{8}', digits):
            return f'{label}: số điện thoại chưa đúng.'
    if kind == 'cccd' and not re.fullmatch(r'\d{12}', re.sub(r'\s', '', value)):
        return f'{label}: số CCCD phải gồm đúng 12 chữ số.'
    if kind == 'number':
        number = re.sub(r'm²|m2|đồng|vnđ|vnd|đ|\s', '', value, flags=re.IGNORECASE)
        valid = bool(re.fullmatch(r'\d+([.,]\d{1,2})?', number)
                     or re.fullmatch(r'\d{1,3}(\.\d{3})+(,\d{1,2})?', number))
        if not valid:
            return f'{label}: số liệu chưa đúng.'
    if kind == 'select' and value not in (field.get('options') or []):
        return f'{label}: hãy chọn một phương án trong danh sách.'
    return None


def _validate_payload(content: dict, payload: FillBody) -> None:
    errors = []
    known_values = {}
    for section in content.get('sections', []):
        for block in section.get('blocks', []):
            if block.get('block_key') == 'repeat_group':
                continue
            for field in block.get('fields', []):
                path = f"{block.get('instance_id')}.{field.get('key')}"
                raw = payload.values.get(path, '')
                value = str(raw).strip()
                condition = field.get('condition')
                visible = not condition or payload.values.get(condition.get('field')) == condition.get('equals')
                known_values[path] = value
                if visible and field.get('required') and (not value or (field.get('type') == 'checkbox' and value.lower() != 'true')):
                    errors.append(f"{field.get('label') or path}: là thông tin bắt buộc.")
                if not visible or not value or field.get('type') == 'checkbox':
                    continue
                if len(value) > 20000:
                    errors.append(f"{field.get('label') or path}: nội dung quá dài.")
                else:
                    message = _field_value_error(field, value)
                    if message: errors.append(message)
    allowed_groups = {group.get('key'): group for group in content.get('repeat_groups', [])}
    for key, rows in payload.groups.items():
        group = allowed_groups.get(key)
        if not group:
            errors.append(f"Nhóm lặp “{key}” không tồn tại trong mẫu.")
            continue
        if not isinstance(rows, list) or len(rows) > 100:
            errors.append(f"Nhóm “{group.get('title') or key}” có quá nhiều dòng.")
            continue
        visible = not group.get('condition') or payload.values.get(group['condition']['field']) == group['condition']['equals']
        for index, row in enumerate(rows, 1):
            for field in group.get('fields', []):
                value = str(row.get(field['key'], '')).strip()
                if visible and field.get('required') and (not value or (field.get('type') == 'checkbox' and value.lower() != 'true')):
                    errors.append(f"{group.get('title') or key}, dòng {index}, {field.get('label')}: là thông tin bắt buộc.")
                if len(value) > 20000:
                    errors.append(f"{group.get('title') or key}, dòng {index}: nội dung quá dài.")
                if value:
                    message = _field_value_error(field, value)
                    if message: errors.append(f"{group.get('title') or key}, dòng {index}, {message}")
    if errors:
        raise HTTPException(422, '; '.join(errors[:12]))


def word_to_pdf(word_data: bytes) -> bytes:
    doc = Document(BytesIO(word_data))
    font_dir = Path(__file__).resolve().parents[1] / 'fonts'
    family = [('CustomForm','LiberationSerif-Regular.ttf'),('CustomForm-Bold','LiberationSerif-Bold.ttf'),
              ('CustomForm-Italic','LiberationSerif-Italic.ttf'),('CustomForm-BoldItalic','LiberationSerif-BoldItalic.ttf')]
    for name, filename in family:
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(font_dir / filename)))
    pdfmetrics.registerFontFamily('CustomForm', normal='CustomForm', bold='CustomForm-Bold', italic='CustomForm-Italic', boldItalic='CustomForm-BoldItalic')
    story = []
    def as_para(p, size=11):
        text_parts = []
        for run in p.runs:
            item = escape(run.text).replace('\n','<br/>')
            if run.bold: item = f'<b>{item}</b>'
            if run.italic: item = f'<i>{item}</i>'
            if run.font.superscript: item = f'<super>{item}</super>'
            text_parts.append(item)
        alignment = {1:TA_CENTER,2:TA_RIGHT}.get(p.alignment,TA_LEFT)
        return Paragraph(''.join(text_parts) or ' ', ParagraphStyle('custom',fontName='CustomForm',fontSize=size,leading=size*1.25,alignment=alignment,spaceAfter=3,wordWrap='CJK'))
    for element in doc.element.body:
        if element.tag == qn('w:p'):
            p = WordParagraph(element,doc)
            if element.xpath('./w:pPr/w:pageBreakBefore'): story.append(PageBreak())
            if p.text.strip(): story.append(as_para(p))
            elif element.xpath('.//w:br[@w:type="page"]'): story.append(PageBreak())
        elif element.tag == qn('w:tbl'):
            table = WordTable(element,doc)
            rows = [[as_para(cell.paragraphs[0],8) for cell in row.cells] for row in table.rows]
            if rows:
                widths = [A4[0]-100] * len(rows[0])
                pdf_table = Table(rows,colWidths=[w/len(widths) for w in widths],repeatRows=1)
                pdf_table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.4,colors.black),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3)]))
                story.append(pdf_table)
    output=BytesIO()
    SimpleDocTemplate(output,pagesize=A4,leftMargin=50,rightMargin=50,topMargin=48,bottomMargin=48).build(story)
    return output.getvalue()


def _row(conn, template_id, *, public=False, lock=False):
    sql = 'SELECT * FROM public.custom_form_templates WHERE id=:id AND deleted_at IS NULL'
    if public: sql += " AND status='published'"
    if lock: sql += ' FOR UPDATE'
    result = conn.execute(text(sql), {'id':str(template_id)}).mappings().first()
    if not result:
        raise HTTPException(404, 'Không tìm thấy mẫu đơn hoặc mẫu chưa được xuất bản.')
    return dict(result)


def _view(row):
    content = row['content']
    if isinstance(content, str):
        import json
        content = json.loads(content)
    has_word = row['has_word_file'] if 'has_word_file' in row else bool(row.get('word_data'))
    has_pdf = row['has_reference_pdf'] if 'has_reference_pdf' in row else bool(row.get('reference_pdf'))
    return {'id':str(row['id']),'template_group_id':str(row['template_group_id']),'version':row['version'],
            'status':row['status'],'name':row['name'],'domain':row['domain'],'procedure_id':row['procedure_id'],
            'content':content,'has_word_file':bool(has_word),'has_reference_pdf':bool(has_pdf),
            'word_filename':row.get('word_filename'),'reference_pdf_filename':row.get('reference_pdf_filename'),
            'created_at':str(row['created_at']),'updated_at':str(row['updated_at']),'deleted_at':str(row['deleted_at']) if row.get('deleted_at') else None}


def _validate_upload(file: UploadFile, data: bytes, *, pdf=False):
    maximum = MAX_PDF if pdf else MAX_WORD
    if len(data) > maximum: raise HTTPException(413, 'File vượt quá dung lượng cho phép.')
    ext = Path(file.filename or '').suffix.lower()
    if pdf:
        if ext != '.pdf' or not data.startswith(b'%PDF-'): raise HTTPException(422, 'Tệp tham khảo phải là PDF hợp lệ.')
    else:
        if ext != '.docx': raise HTTPException(422, 'File điền đơn phải là Word .docx. Hãy mở file bình thường bằng WPS, chèn dấu điền rồi tải lên.')
        try:
            with zipfile.ZipFile(BytesIO(data)) as package:
                if 'word/document.xml' not in package.namelist(): raise ValueError('missing document')
        except Exception as exc: raise HTTPException(422, 'Không đọc được file Word .docx.') from exc


@router.get('/admin/form-templates')
def list_admin(domain: str | None = None, status: str | None = None, include_deleted: bool = False,
               user: CurrentUser = Depends(require_role('admin'))):
    where=['1=1']
    params={}
    if domain: where.append('domain=:domain'); params['domain']=domain
    if status: where.append('status=:status'); params['status']=status
    if not include_deleted: where.append('deleted_at IS NULL')
    select_cols = '''id, template_group_id, version, status, name, domain, procedure_id, content,
                         (word_data IS NOT NULL) AS has_word_file,
                         (reference_pdf IS NOT NULL) AS has_reference_pdf,
                         word_filename, reference_pdf_filename,
                         created_at, updated_at, deleted_at'''
    with _seeded_read_connection() as conn:
        rows=conn.execute(text(f'SELECT {select_cols} FROM public.custom_form_templates WHERE ' + ' AND '.join(where) + ' ORDER BY updated_at DESC'), params).mappings().all()
    items=[_view(dict(row)) for row in rows]
    return {'data':{'items':items,'total':len(items)},'error':None}


@router.post('/admin/form-templates',status_code=201)
def create_template(body: TemplateBody, user: CurrentUser=Depends(require_role('admin'))):
    content=body.model_dump(mode='json')
    errors=_validate_content(content, for_publish=False)
    if errors: raise HTTPException(422,'; '.join(errors))
    template_id,group_id=str(uuid.uuid4()),str(uuid.uuid4())
    with db_connection(write=True) as conn:
        row=conn.execute(text('''INSERT INTO public.custom_form_templates (id,template_group_id,name,domain,procedure_id,content)
          VALUES (:id,:group_id,:name,:domain,:procedure_id,CAST(:content AS jsonb)) RETURNING *'''),
          {'id':template_id,'group_id':group_id,'name':content['name'].strip(),'domain':content['domain'],'procedure_id':content.get('procedure_id'),
           'content':__import__('json').dumps(content,ensure_ascii=False)}).mappings().one()
    _invalidate_template_caches()
    return {'data':_view(dict(row)),'error':None}

@router.post('/admin/form-templates/{template_id}/clone', status_code=201)
def clone_template(template_id: str, user: CurrentUser = Depends(require_role('admin'))):
    with db_connection(write=True) as conn:
        source = _row(conn, template_id, lock=True)
        new_id = str(uuid.uuid4())
        new_group_id = str(uuid.uuid4())
        new_name = f"{source['name']} (Bản sao)"
        tpl_content = source['content']
        if isinstance(tpl_content, str):
            import json
            tpl_content = json.loads(tpl_content)
        elif isinstance(tpl_content, dict):
            import copy
            tpl_content = copy.deepcopy(tpl_content)
        tpl_content['name'] = new_name

        row = conn.execute(text('''INSERT INTO public.custom_form_templates 
          (id, template_group_id, version, status, name, domain, procedure_id, content, word_data, word_filename, reference_pdf, reference_pdf_filename)
          VALUES (:id, :group, 1, 'draft', :name, :domain, :procedure_id, CAST(:content AS jsonb), :word_data, :word_filename, :pdf, :pdf_filename)
          RETURNING *'''),
          {
              'id': new_id,
              'group': new_group_id,
              'name': new_name,
              'domain': source['domain'],
              'procedure_id': source.get('procedure_id'),
              'content': __import__('json').dumps(tpl_content, ensure_ascii=False),
              'word_data': source.get('word_data'),
              'word_filename': source.get('word_filename'),
              'pdf': source.get('reference_pdf'),
              'pdf_filename': source.get('reference_pdf_filename')
          }).mappings().one()
    _invalidate_template_caches()
    return {'data': _view(dict(row)), 'error': None}


@router.get('/admin/form-templates/{template_id}')
def get_admin(template_id: str,user: CurrentUser=Depends(require_role('admin'))):
    with _seeded_read_connection() as conn:
        row=_row(conn,template_id)
    return {'data':_view(row),'error':None}


@router.put('/admin/form-templates/{template_id}')
def update_template(template_id: str,body: TemplateBody,user: CurrentUser=Depends(require_role('admin'))):
    content=body.model_dump(mode='json'); errors=_validate_content(content, for_publish=False)
    if errors: raise HTTPException(422,'; '.join(errors))
    with db_connection(write=True) as conn:
        current=_row(conn,template_id,lock=True)
        row=conn.execute(text('''UPDATE public.custom_form_templates SET name=:name,domain=:domain,procedure_id=:procedure_id,
          content=CAST(:content AS jsonb),updated_at=now() WHERE id=:id RETURNING *'''),
          {'id':template_id,'name':content['name'].strip(),'domain':content['domain'],'procedure_id':content.get('procedure_id'),
           'content':__import__('json').dumps(content,ensure_ascii=False)}).mappings().one()
    _invalidate_template_caches()
    return {'data':_view(dict(row)),'error':None}


@router.post('/admin/form-templates/{template_id}/new-version',status_code=201)
def new_version(template_id: str,user: CurrentUser=Depends(require_role('admin'))):
    with db_connection(write=True) as conn:
        old=_row(conn,template_id,lock=True)
        existing=conn.execute(text("SELECT * FROM public.custom_form_templates WHERE template_group_id=:group AND status='draft' AND deleted_at IS NULL ORDER BY version DESC LIMIT 1"),{'group':old['template_group_id']}).mappings().first()
        if existing: return {'data':_view(dict(existing)),'error':None}
        version=conn.execute(text('SELECT COALESCE(MAX(version),0)+1 FROM public.custom_form_templates WHERE template_group_id=:group'),{'group':old['template_group_id']}).scalar_one()
        new_id=str(uuid.uuid4())
        row=conn.execute(text('''INSERT INTO public.custom_form_templates (id,template_group_id,version,status,name,domain,procedure_id,content,word_data,word_filename,reference_pdf,reference_pdf_filename)
          VALUES (:id,:group,:version,'draft',:name,:domain,:procedure_id,CAST(:content AS jsonb),:word_data,:word_filename,:pdf,:pdf_filename) RETURNING *'''),
          {'id':new_id,'group':old['template_group_id'],'version':version,'name':old['name'],'domain':old['domain'],'procedure_id':old['procedure_id'],
           'content':__import__('json').dumps(old['content'],ensure_ascii=False) if isinstance(old['content'],dict) else old['content'],'word_data':old['word_data'],'word_filename':old['word_filename'],'pdf':old['reference_pdf'],'pdf_filename':old['reference_pdf_filename']}).mappings().one()
    _invalidate_template_caches()
    return {'data':_view(dict(row)),'error':None}


@router.post('/admin/form-templates/{template_id}/files')
async def upload_files(template_id: str,word: UploadFile | None=File(default=None),reference_pdf: UploadFile | None=File(default=None),user: CurrentUser=Depends(require_role('admin'))):
    if not (word and word.filename) and not (reference_pdf and reference_pdf.filename):
        raise HTTPException(422,'Chọn file Word hoặc PDF tham khảo trước khi tải lên.')
    word_data=None
    if word and word.filename:
        word_data=await word.read(MAX_WORD+1); _validate_upload(word,word_data)
    pdf_data=None
    if reference_pdf and reference_pdf.filename:
        pdf_data=await reference_pdf.read(MAX_PDF+1); _validate_upload(reference_pdf,pdf_data,pdf=True)
    with db_connection(write=True) as conn:
        row=_row(conn,template_id,lock=True)
        result=conn.execute(text('''UPDATE public.custom_form_templates SET word_data=COALESCE(:word,word_data),word_filename=COALESCE(:filename,word_filename),
          reference_pdf=COALESCE(:pdf,reference_pdf),reference_pdf_filename=COALESCE(:pdf_filename,reference_pdf_filename),updated_at=now()
          WHERE id=:id RETURNING *'''),{'id':template_id,'word':word_data,'filename':Path(word.filename).name if word and word.filename else None,'pdf':pdf_data,
            'pdf_filename':Path(reference_pdf.filename).name if pdf_data else None}).mappings().one()
    _invalidate_template_caches()
    return {'data':_view(dict(result)),'error':None}


@router.get('/admin/form-templates/{template_id}/document')
def download_source(template_id: str,user: CurrentUser=Depends(require_role('admin'))):
    with db_connection() as conn: row=_row(conn,template_id)
    if not row.get('word_data'): raise HTTPException(404,'Mẫu chưa có file Word.')
    filename = row.get("word_filename") or "mau.docx"
    return Response(bytes(row['word_data']),media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers={'Content-Disposition':_content_disposition(filename, 'attachment')})


@router.post('/admin/form-templates/{template_id}/check')
def check_document(template_id: str,user: CurrentUser=Depends(require_role('admin'))):
    with db_connection() as conn: row=_row(conn,template_id)
    if not row.get('word_data'): raise HTTPException(404,'Tải lên file Word trước khi kiểm tra.')
    return {'data':analyze_document(row['content'],bytes(row['word_data'])),'error':None}


@router.get('/admin/form-templates/{template_id}/word-editor')
def load_word_editor(template_id: str, user: CurrentUser=Depends(require_role('admin'))):
    from app.word_editor import document_model
    with db_connection() as conn:
        row = _row(conn, template_id)
    if not row.get('word_data'):
        raise HTTPException(404, 'Tải lên file Word trước khi mở trình sửa.')
    _, _, model = document_model(bytes(row['word_data']))
    return {'data': model, 'error': None}


@router.put('/admin/form-templates/{template_id}/word-editor')
def save_word_editor(template_id: str, body: WordEditBody, user: CurrentUser=Depends(require_role('admin'))):
    from hashlib import sha256
    from app.word_editor import edit_document
    with db_connection(write=True) as conn:
        row = _row(conn, template_id, lock=True)
        original = bytes(row.get('word_data') or b'')
        if sha256(original).hexdigest() != body.version:
            raise HTTPException(409, 'File Word đã thay đổi. Hãy mở lại để tránh ghi đè.')
        if not original:
            raise HTTPException(404, 'Mẫu chưa có file Word.')
        if sum(len(edit.text) for edit in body.edits) > 1000000:
            raise HTTPException(422, 'Nội dung chỉnh sửa quá lớn.')
        try:
            updated = edit_document(original, [edit.model_dump() for edit in body.edits])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        conn.execute(text('UPDATE public.custom_form_templates SET word_data=:word,updated_at=now() WHERE id=:id'), {'word': updated, 'id': template_id})
    _invalidate_template_caches()
    return {'data': {'version': sha256(updated).hexdigest()}, 'error': None}


@router.post('/admin/form-templates/{template_id}/publish')
def publish_template(template_id: str,user: CurrentUser=Depends(require_role('admin'))):
    with db_connection(write=True) as conn:
        row=_row(conn,template_id,lock=True)
        if row['status'] not in ('draft','hidden','published'): raise HTTPException(409,'Chỉ xuất bản được bản nháp, mẫu đang ẩn hoặc đang xuất bản.')
        if not row.get('word_data'): raise HTTPException(422,'Tải file Word đã gắn trường lên trước khi xuất bản.')
        report=analyze_document(row['content'],bytes(row['word_data']))
        if not report['valid']: raise HTTPException(422,'Chưa xuất bản được: '+'; '.join(report['errors']))
        conn.execute(text("UPDATE public.custom_form_templates SET status='archived',updated_at=now() WHERE template_group_id=:group AND id<>:id AND status IN ('published','hidden') AND deleted_at IS NULL"),{'group':row['template_group_id'],'id':template_id})
        updated=conn.execute(text("UPDATE public.custom_form_templates SET status='published',updated_at=now() WHERE id=:id RETURNING *"),{'id':template_id}).mappings().one()
    _invalidate_template_caches()
    return {'data':_view(dict(updated)),'error':None}


@router.post('/admin/form-templates/{template_id}/hide')
def hide_template(template_id: str,user: CurrentUser=Depends(require_role('admin'))):
    with db_connection(write=True) as conn:
        row=_row(conn,template_id,lock=True)
        if row['status']!='published': raise HTTPException(409,'Chỉ ẩn được mẫu đang xuất bản.')
        updated=conn.execute(text("UPDATE public.custom_form_templates SET status='hidden',updated_at=now() WHERE id=:id RETURNING *"),{'id':template_id}).mappings().one()
    _invalidate_template_caches()
    return {'data':_view(dict(updated)),'error':None}


@router.delete('/admin/form-templates/{template_id}', status_code=204)
async def delete_template(template_id: str, user: CurrentUser = Depends(require_role('admin'))):
    with db_connection(write=True) as conn:
        _row(conn, template_id, lock=True)
        conn.execute(text('UPDATE public.custom_form_templates SET deleted_at=now(), updated_at=now() WHERE id=:id'), {'id': template_id})
    _invalidate_template_caches()
    try:
        from app.core.rabbitmq_procedure import procedure_mq
        await procedure_mq.publish_form_template_deleted(template_id, str(user.id))
    except Exception as exc:
        logger.warning(f'RabbitMQ publish form_template.deleted failed: {exc}')


@router.post('/admin/form-templates/{template_id}/restore')
def restore_template(template_id: str,user: CurrentUser=Depends(require_role('admin'))):
    with db_connection(write=True) as conn:
        row=conn.execute(text('UPDATE public.custom_form_templates SET deleted_at=NULL,updated_at=now() WHERE id=:id RETURNING *'),{'id':template_id}).mappings().first()
        if not row: raise HTTPException(404,'Không tìm thấy mẫu đơn.')
    _invalidate_template_caches()
    return {'data':_view(dict(row)),'error':None}


@router.get('/form-templates')
def list_public(domain: str | None=None,procedure_id: str | None=None):
    cache_key = (domain, procedure_id)
    now = time.monotonic()
    use_cache = database_url().startswith('postgresql')
    if use_cache and cache_key in _PUBLIC_LIST_CACHE:
        cached_at, cached_resp = _PUBLIC_LIST_CACHE[cache_key]
        if (now - cached_at) < _PUBLIC_LIST_TTL:
            return deepcopy(cached_resp)
    where=["status='published'",'deleted_at IS NULL']; params={}
    if domain: where.append('domain=:domain'); params['domain']=domain
    if procedure_id: where.append('procedure_id=:procedure_id'); params['procedure_id']=procedure_id
    select_cols = '''id, template_group_id, version, status, name, domain, procedure_id, content,
                         (word_data IS NOT NULL) AS has_word_file,
                         (reference_pdf IS NOT NULL) AS has_reference_pdf,
                         word_filename, reference_pdf_filename,
                         created_at, updated_at, deleted_at'''
    with _seeded_read_connection() as conn:
        rows=conn.execute(text(f'SELECT {select_cols} FROM public.custom_form_templates WHERE ' + ' AND '.join(where) + ' ORDER BY name'), params).mappings().all()
    items=[_view(dict(row)) for row in rows]
    resp = {'data':{'items':items,'total':len(items)},'error':None}
    if use_cache:
        _PUBLIC_LIST_CACHE[cache_key] = (now, resp)
    return deepcopy(resp) if use_cache else resp


@router.get('/form-templates/{template_id}')
def get_public(template_id: str):
    with _seeded_read_connection() as conn:
        row=_row(conn,template_id,public=True)
    return {'data':_view(row),'error':None}


@router.get('/form-templates/{template_id}/reference-pdf')
def get_reference_pdf(template_id: str):
    with db_connection() as conn: row=_row(conn,template_id,public=True)
    if row.get('reference_pdf'):
        filename = Path(row.get('reference_pdf_filename') or 'mau-tham-khao.pdf').name
        return Response(bytes(row['reference_pdf']),media_type='application/pdf',headers={
            'Content-Disposition': _content_disposition(filename, 'inline'),
            'Cache-Control': 'public, max-age=300',
            'X-Content-Type-Options': 'nosniff'
        })
    if row.get('word_data'):
        try:
            raw_word = bytes(row['word_data'])
            cache_hash = sha256(raw_word).hexdigest()
            pdf_bytes = _PDF_CACHE.get(cache_hash)
            if pdf_bytes is None:
                pdf_bytes = word_to_pdf(raw_word)
                if len(_PDF_CACHE) >= 64:
                    _PDF_CACHE.clear()
                _PDF_CACHE[cache_hash] = pdf_bytes
            pdf_name = f"{row.get('name') or 'mau-don'}.pdf"
            return Response(pdf_bytes, media_type='application/pdf', headers={
                'Content-Disposition': _content_disposition(pdf_name, 'inline'),
                'Cache-Control': 'public, max-age=300',
                'X-Content-Type-Options': 'nosniff'
            })
        except Exception as exc:
            logger.warning(f"Failed to generate reference PDF for template {template_id}: {exc}", exc_info=True)
    raise HTTPException(404,'Mẫu này không có PDF tham khảo.')


@router.get('/form-templates/{template_id}/document')
def get_public_document(template_id: str):
    with db_connection() as conn: row=_row(conn,template_id,public=True)
    if not row.get('word_data'):
        raise HTTPException(404,'Mẫu chưa có file Word.')
    filename = Path(row.get('word_filename') or 'mau.docx').name
    return Response(bytes(row['word_data']),media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers={
        'Content-Disposition': _content_disposition(filename, 'attachment'),
        'Cache-Control':'public, max-age=300'})


def _export(template_id: str,payload: FillBody,*,pdf: bool,user: CurrentUser,admin: bool=False):
    with _seeded_read_connection() as conn:
        row=_row(conn,template_id,public=not admin)
    if not row.get('word_data'): raise HTTPException(422,'Tải file Word đã gắn dấu lên trước khi thử.')
    report=analyze_document(row['content'],bytes(row['word_data']))
    if not report['valid']: raise HTTPException(503,'Mẫu này chưa vượt qua kiểm tra file Word.')
    if not admin:
        from app.services.profile_binding import bind_custom_profile
        bind_custom_profile(row['content'], payload, str(user.id))
    _validate_payload(row['content'],payload)
    filled=fill_word(bytes(row['word_data']),row['content'],payload)
    if pdf:
        result=word_to_pdf(filled)
        return Response(result,media_type='application/pdf',headers={'Content-Disposition':'inline; filename="ban-xem-truoc.pdf"','Cache-Control':'no-store'})
    return Response(filled,media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers={'Content-Disposition':'attachment; filename="don-da-dien.docx"','Cache-Control':'no-store'})


@router.post('/form-templates/{template_id}/preview-pdf')
def preview_pdf(template_id: str,payload: FillBody,user: CurrentUser=Depends(get_current_user)):
    return _export(template_id,payload,pdf=True,user=user)


@router.post('/form-templates/{template_id}/download-word')
def download_word(template_id: str,payload: FillBody,user: CurrentUser=Depends(get_current_user)):
    return _export(template_id,payload,pdf=False,user=user)


@router.post('/admin/form-templates/{template_id}/preview-pdf')
def admin_preview_pdf(template_id: str,payload: FillBody,user: CurrentUser=Depends(require_role('admin'))):
    return _export(template_id,payload,pdf=True,user=user,admin=True)


@router.post('/admin/form-templates/{template_id}/download-word')
def admin_download_word(template_id: str,payload: FillBody,user: CurrentUser=Depends(require_role('admin'))):
    return _export(template_id,payload,pdf=False,user=user,admin=True)


@router.post('/form-templates/{template_id}/submissions',status_code=201)
def save_submission(template_id: str,payload: SaveBody,user: CurrentUser=Depends(get_current_user)):
    if payload.status not in {'created','submitted'}: raise HTTPException(422,'Trạng thái hồ sơ không hợp lệ.')
    with db_connection(write=True) as conn:
        ensure_builtin_seeded(conn)
        row=_row(conn,template_id,public=True)
        if not row.get('word_data'): raise HTTPException(422,'Mẫu chưa có file Word để tạo hồ sơ.')
        report=analyze_document(row['content'],bytes(row['word_data']))
        if not report['valid']: raise HTTPException(503,'Mẫu này chưa sẵn sàng để tạo hồ sơ.')
        from app.services.profile_binding import bind_custom_profile
        bind_custom_profile(row['content'], payload, str(user.id))
        _validate_payload(row['content'],payload)
        submission_id=str(uuid.uuid4())
        saved=conn.execute(text('''INSERT INTO public.custom_form_submissions (id,template_id,user_id,status,form_values)
           VALUES (:id,:template_id,:user_id,:status,CAST(:values AS jsonb)) RETURNING id,template_id,status,created_at'''),
           {'id':submission_id,'template_id':template_id,'user_id':str(user.id),'status':payload.status,
            'values':__import__('json').dumps({'values':payload.values,'groups':payload.groups},ensure_ascii=False)}).mappings().one()
    return {'data':{**dict(saved),'id':str(saved['id']),'template_id':str(saved['template_id']),'created_at':str(saved['created_at'])},'error':None}
