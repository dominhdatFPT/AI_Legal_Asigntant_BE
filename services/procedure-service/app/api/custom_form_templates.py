"""Admin configured Word forms, public fill/export, and saved custom submissions."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from html import escape
from io import BytesIO
import json
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
from typing import Literal

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
from app.core.catalog_cache import catalog_cache
from app.services.submission_recipient import is_submission_recipient, optional_template_recipients

_BUILTIN_SEEDED = False
_PUBLIC_LIST_CACHE: dict[tuple[str | None, str | None], tuple[float, dict]] = {}
_PUBLIC_LIST_TTL = 60.0
_PDF_CACHE: dict[str, bytes] = {}
_PDF_HEALTH_CACHE: dict[str, tuple[str, str | None]] = {}


def _invalidate_template_caches() -> None:
    catalog_cache.invalidate('custom_templates')
    _PUBLIC_LIST_CACHE.clear()
    _PDF_CACHE.clear()
    _PDF_HEALTH_CACHE.clear()


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
def _seeded_read_connection(fresh: bool = False):
    with db_connection(write=False, fresh=fresh) as conn:
        yield conn

router = APIRouter()
MAX_WORD = 10 * 1024 * 1024
MAX_PDF = 15 * 1024 * 1024
DOMAINS = {'dat_dai', 'ho_kinh_doanh', 'doanh_nghiep'}
WORD_KEY = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
TOKEN = re.compile(r'\{\{\s*([^{}]+?)\s*\}\}')
VIETNAMESE_PATTERN = re.compile(
    r'[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]',
    re.IGNORECASE,
)
VN_NON_WINANSI = re.compile(
    r'[ạảầấậẩẫăằắặẳẵẹẻẽềếệểễịỉĩọỏồốộổỗơờớợởỡụủũưừứựửữỳỵỷỹđ]',
    re.IGNORECASE,
)
IN_WORD_DOT = re.compile(r'[a-zA-Z0-9\u00C0-\u024F]·|·[a-zA-Z0-9\u00C0-\u024F]|\uFFFD')


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


def _condition_matcher(content: dict, values: dict[str, str], *, citizen: bool):
    fields = {}
    recipient_paths = set()
    if citizen:
        for section in content.get('sections', []):
            for block in section.get('blocks', []):
                if block.get('block_key') == 'repeat_group':
                    continue
                for field in block.get('fields', []):
                    path = f"{block['instance_id']}.{field['key']}"
                    fields[path] = field
                    if is_submission_recipient(field, block.get('block_key')):
                        recipient_paths.add(path)

    def uses_hidden_recipient(condition):
        return bool(condition and condition['field'] in recipient_paths
                    and not values.get(condition['field'], '').strip())

    def matches(condition):
        seen = set()
        while condition:
            path = condition['field']
            value = values.get(path)
            if not citizen:
                return value == condition['equals']
            # Current citizen forms omit recipient controls. Legacy clients can
            # still supply an actual recipient and use the original comparison.
            if uses_hidden_recipient(condition):
                return True
            if path in seen or path not in fields or value is None or value != condition['equals']:
                return False
            seen.add(path)
            condition = fields[path].get('condition')
        return True

    return matches, uses_hidden_recipient


def fill_word(file_data: bytes, content: dict, payload: FillBody, *, citizen: bool = False) -> bytes:
    matches_condition, _ = _condition_matcher(content, payload.values, citizen=citizen)
    doc = Document(BytesIO(file_data))
    groups = {group['key']: group for group in content.get('repeat_groups', [])}
    for table in doc.tables:
        table_groups = {
            key: group for key, group in groups.items()
            if any('#' + key in {_token_name(token) for token in TOKEN.findall(' '.join(cell.text for cell in row.cells))}
                   for row in table.rows)
        }
        def has_rows(group):
            return matches_condition(group.get('condition')) and bool(payload.groups.get(group['key'], []))

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
            group_values = payload.groups.get(key, []) if matches_condition(group.get('condition')) else []
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
                visible = matches_condition(field.get('condition'))
                if not visible:
                    value = ''
                if field.get('type') == 'checkbox' and (visible or not citizen):
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


def _validate_payload(content: dict, payload: FillBody, *, citizen: bool = False) -> None:
    matches_condition, uses_hidden_recipient = _condition_matcher(content, payload.values, citizen=citizen)
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
                visible = matches_condition(condition)
                known_values[path] = value
                missing = not value or (field.get('type') == 'checkbox' and value.lower() != 'true')
                if (visible and field.get('required') and missing and not uses_hidden_recipient(condition)
                        and not is_submission_recipient(field, block.get('block_key'))):
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
        visible = matches_condition(group.get('condition'))
        if citizen and not visible:
            continue
        for index, row in enumerate(rows, 1):
            for field in group.get('fields', []):
                value = str(row.get(field['key'], '')).strip()
                missing = not value or (field.get('type') == 'checkbox' and value.lower() != 'true')
                if visible and field.get('required') and missing and not is_submission_recipient(field):
                    errors.append(f"{group.get('title') or key}, dòng {index}, {field.get('label')}: là thông tin bắt buộc.")
                if len(value) > 20000:
                    errors.append(f"{group.get('title') or key}, dòng {index}: nội dung quá dài.")
                if value:
                    message = _field_value_error(field, value)
                    if message: errors.append(f"{group.get('title') or key}, dòng {index}, {message}")
    if errors:
        raise HTTPException(422, '; '.join(errors[:12]))


def _build_procedure_values(content: dict, values: dict, fields: list[dict] | None = None) -> dict:
    mapped = {}
    for sec in content.get('sections', []):
        for block in sec.get('blocks', []):
            inst = block.get('instance_id') or ''
            for f in block.get('fields', []):
                key = f.get('key')
                wkey = f.get('word_key')
                field_name = f.get('name') or wkey or key
                for candidate in (wkey, field_name, key, f"{inst}.{key}"):
                    if candidate and candidate in values:
                        full_key = f"{inst}.{key}" if inst else key
                        mapped[full_key] = str(values[candidate])
                        break
    return mapped


def check_pdf_health(
    data: bytes,
    word_bytes: bytes | None = None,
    max_pages: int = 50,
) -> tuple[Literal['CLEAN', 'SCANNED', 'DEFECTIVE_FONT', 'CORRUPTED'], str | None]:
    """Kiểm tra độ toàn vẹn và chất lượng của file PDF tham khảo.

    Phân loại các trạng thái:
    - 'CORRUPTED': Tệp không hợp lệ, hỏng cấu trúc header, xref hoặc không parse được.
    - 'SCANNED': Tài liệu scan / ảnh (không có text layer hoặc ít hơn 50 ký tự).
    - 'DEFECTIVE_FONT': Lỗi mã hóa bảng mã (Type 1 / WinAnsi / MuPDF) làm rớt ký tự tiếng Việt Unicode
      thành dấu · hoặc \ufffd (được xác thực độc lập hoặc qua đối chiếu chéo với Word).
    - 'CLEAN': PDF hợp lệ, text layer toàn vẹn.
    """
    if not data or not data.startswith(b'%PDF-'):
        return 'CORRUPTED', 'Tệp không phải là định dạng PDF hợp lệ (thiếu header %PDF-)'

    pdf_key = sha256(data[:4096] + data[-4096:] + len(data).to_bytes(8, 'big')).hexdigest()
    if word_bytes is None and pdf_key in _PDF_HEALTH_CACHE:
        return _PDF_HEALTH_CACHE[pdf_key]

    try:
        import pypdf
        reader = pypdf.PdfReader(BytesIO(data))
        if len(reader.pages) == 0:
            res = ('CORRUPTED', 'Tệp PDF không chứa trang nào.')
            if word_bytes is None:
                _PDF_HEALTH_CACHE[pdf_key] = res
            return res
        text = ''
        for page in reader.pages[:max_pages]:
            extracted = page.extract_text()
            if extracted:
                text += extracted + '\n'
    except Exception as exc:
        res = ('CORRUPTED', f'Không thể đọc cấu trúc tệp PDF: {exc}')
        if word_bytes is None:
            _PDF_HEALTH_CACHE[pdf_key] = res
        return res

    stripped = text.strip()
    if len(stripped) < 50:
        res = ('SCANNED', None)
        if word_bytes is None:
            if len(_PDF_HEALTH_CACHE) >= 256:
                _PDF_HEALTH_CACHE.clear()
            _PDF_HEALTH_CACHE[pdf_key] = res
        return res

    dot_count = text.count('·') + text.count('\ufffd')
    in_word_dots = len(IN_WORD_DOT.findall(text))
    pdf_non_winansi = len(VN_NON_WINANSI.findall(text))
    total_len = len(text)

    # 1. Đối chiếu chéo với file Word (nếu có):
    if word_bytes:
        try:
            import docx
            doc = docx.Document(BytesIO(word_bytes))
            word_paras = [p.text for p in doc.paragraphs if p.text]
            word_cells = [c.text for t in doc.tables for r in t.rows for c in r.cells if c.text]
            word_text = '\n'.join(word_paras + word_cells)
            word_non_winansi = len(VN_NON_WINANSI.findall(word_text))

            # Nếu Word chứa tiếng Việt phong phú (>= 10 ký tự Non-WinAnsi),
            # nhưng PDF lại rớt gần như toàn bộ (<= 2) và xuất hiện nhiều dấu thay thế:
            if word_non_winansi >= 10 and pdf_non_winansi <= 2 and (dot_count >= 8 or in_word_dots >= 4):
                return 'DEFECTIVE_FONT', (
                    f'Đối chiếu Word: File Word gốc chứa {word_non_winansi} ký tự tiếng Việt có dấu chuẩn '
                    f'nhưng PDF chỉ trích xuất được {pdf_non_winansi} ký tự và chứa {dot_count} ký tự thay thế '
                    f'({in_word_dots} dấu nằm ngay trong từ).'
                )
        except Exception as w_exc:
            logger.debug(f"Không thể đọc word_bytes để đối chiếu: {w_exc}")

    # 2. Phân tích văn bản PDF độc lập (khi không có Word):
    # Dấu · nằm ngay trong từ (ví dụ M·u, VI·T, C·NG) là dấu hiệu không thể chối cãi của việc rớt glyph Unicode.
    if in_word_dots >= 4 and pdf_non_winansi <= 2:
        res = (
            'DEFECTIVE_FONT',
            f'Phát hiện {in_word_dots} ký tự lỗi font nằm bên trong từ và thiếu ký tự tiếng Việt Unicode chuẩn.'
        )
    elif dot_count >= 10 and (dot_count / total_len) > 0.02 and pdf_non_winansi <= 2:
        res = (
            'DEFECTIVE_FONT',
            f'Tài liệu chứa {dot_count} ký tự thay thế (tỷ lệ {dot_count / total_len:.1%}) và thiếu ký tự tiếng Việt Unicode chuẩn.'
        )
    else:
        res = ('CLEAN', None)

    if word_bytes is None:
        if len(_PDF_HEALTH_CACHE) >= 256:
            _PDF_HEALTH_CACHE.clear()
        _PDF_HEALTH_CACHE[pdf_key] = res
    return res


def is_defective_pdf(data: bytes, word_data: bytes | None = None) -> bool:
    """Kiểm tra tương thích ngược xem PDF có lỗi font hay không."""
    status, _ = check_pdf_health(data, word_bytes=word_data)
    return status == 'DEFECTIVE_FONT'


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
        style_name = getattr(getattr(p, 'style', None), 'name', '') or ''
        calc_size = 15 if 'Heading 1' in style_name else (13 if 'Heading 2' in style_name else size)
        alignment = {1:TA_CENTER,2:TA_RIGHT}.get(p.alignment,TA_LEFT)
        return Paragraph(''.join(text_parts) or ' ', ParagraphStyle('custom',fontName='CustomForm',fontSize=calc_size,leading=calc_size*1.25,alignment=alignment,spaceAfter=3,wordWrap='CJK'))

    def cell_flowables(cell, size=8):
        paras = []
        for p in cell.paragraphs:
            if p.text.strip():
                paras.append(as_para(p, size=size))
        return paras if paras else [Paragraph(' ', ParagraphStyle('cell_blank', fontName='CustomForm', fontSize=size))]

    for element in doc.element.body:
        if element.tag == qn('w:p'):
            p = WordParagraph(element,doc)
            if element.xpath('./w:pPr/w:pageBreakBefore'): story.append(PageBreak())
            if p.text.strip(): story.append(as_para(p))
            elif element.xpath('.//w:br[@w:type="page"]'): story.append(PageBreak())
        elif element.tag == qn('w:tbl'):
            table = WordTable(element,doc)
            rows = [[cell_flowables(cell, 8) for cell in row.cells] for row in table.rows]
            if rows:
                widths = [A4[0]-100] * len(rows[0])
                pdf_table = Table(rows,colWidths=[w/len(widths) for w in widths],repeatRows=1)
                pdf_table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.4,colors.black),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3)]))
                story.append(pdf_table)
    output=BytesIO()
    SimpleDocTemplate(output,pagesize=A4,leftMargin=50,rightMargin=50,topMargin=48,bottomMargin=48).build(story)
    return output.getvalue()


_META_COLS = (
    'id, template_group_id, version, status, name, domain, procedure_id, content, '
    '(word_data IS NOT NULL) AS has_word_file, '
    '(reference_pdf IS NOT NULL) AS has_reference_pdf, '
    'word_filename, reference_pdf_filename, created_at, updated_at, deleted_at'
)
_BYTEA_CHUNK_SIZE = 49152


def _row(conn, template_id, *, public=False, lock=False):
    sql = f'SELECT {_META_COLS} FROM public.custom_form_templates WHERE id=:id AND deleted_at IS NULL'
    if public:
        sql += " AND status='published'"
    if lock:
        sql += ' FOR UPDATE'
    result = conn.execute(text(sql), {'id': str(template_id)}).mappings().first()
    if not result:
        raise HTTPException(404, 'Không tìm thấy mẫu đơn hoặc mẫu chưa được xuất bản.')
    row = dict(result)
    if isinstance(row['content'], str):
        row['content'] = json.loads(row['content'])
    return row


def _read_template_bytea(
    template_id: str,
    column: Literal['word_data', 'reference_pdf'],
    *,
    public: bool = False,
    conn=None,
) -> bytes | None:
    """Đọc một cột nhị phân với điều kiện công khai khi được yêu cầu."""
    if column not in ('word_data', 'reference_pdf'):
        raise ValueError(f'Unsupported binary column: {column}')
    where_sql = 'id=:id AND deleted_at IS NULL'
    if public:
        where_sql += " AND status='published'"
    tid = str(template_id)

    def read(connection):
        value = connection.execute(
            text(f'SELECT {column} FROM public.custom_form_templates WHERE {where_sql}'),
            {'id': tid},
        ).scalar_one_or_none()
        if value is None:
            return None
        data = bytes(value)
        if len(data) > (MAX_WORD if column == 'word_data' else MAX_PDF):
            raise HTTPException(413, 'File exceeds the permitted size.')
        return data

    if conn is not None:
        return read(conn)
    with db_connection(write=False) as connection:
        return read(connection)


def _template_word_sha256(conn, template_id: str, *, has_word_file: bool) -> str:
    """Tính SHA-256 hex của word_data ngay trên PostgreSQL mà không kéo blob nhị phân."""
    if not has_word_file:
        return sha256(b'').hexdigest()
    if getattr(getattr(conn, 'dialect', None), 'name', '') == 'postgresql':
        digest = conn.execute(
            text("SELECT encode(sha256(word_data), 'hex') FROM public.custom_form_templates WHERE id=:id AND deleted_at IS NULL"),
            {'id': str(template_id)},
        ).scalar_one_or_none()
        return digest or sha256(b'').hexdigest()
    data = _read_template_bytea(template_id, 'word_data', conn=conn) or b''
    return sha256(data).hexdigest()


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


def _validate_upload(
    file: UploadFile,
    data: bytes,
    *,
    pdf: bool = False,
    word_data: bytes | None = None,
):
    maximum = MAX_PDF if pdf else MAX_WORD
    if len(data) > maximum:
        raise HTTPException(413, 'File vượt quá dung lượng cho phép.')
    ext = Path(file.filename or '').suffix.lower()
    if pdf:
        if ext != '.pdf' or not data.startswith(b'%PDF-'):
            raise HTTPException(422, 'Tệp tham khảo phải là file PDF hợp lệ.')
        status, detail = check_pdf_health(data, word_bytes=word_data)
        if status == 'CORRUPTED':
            raise HTTPException(
                422,
                f'Tệp PDF bị hỏng hoặc không thể đọc cấu trúc: {detail or "Định dạng không hợp lệ"}.'
            )
        if status == 'DEFECTIVE_FONT':
            raise HTTPException(
                422,
                'Tệp PDF tham khảo bị lỗi mã hóa font chữ tiếng Việt (ký tự bị biến thành dấu ·). '
                'Hãy xuất lại tệp PDF có nhúng font chữ Unicode (Embed fonts) từ Word/LibreOffice trước khi tải lên. '
                f'Chi tiết: {detail}'
            )
    else:
        if ext != '.docx':
            raise HTTPException(422, 'File điền đơn phải là Word .docx. Hãy mở file bình thường bằng WPS, chèn dấu điền rồi tải lên.')
        try:
            with zipfile.ZipFile(BytesIO(data)) as package:
                if 'word/document.xml' not in package.namelist():
                    raise ValueError('missing document')
        except Exception as exc:
            raise HTTPException(422, 'Không đọc được file Word .docx.') from exc


def _project_list_content(
    content: object,
    *,
    include_public_content: bool,
    include_sections: bool = True,
) -> dict:
    if isinstance(content, str):
        import json
        content = json.loads(content)
    data = content if isinstance(content, dict) else {}
    projected: dict[str, object] = {'code': data.get('code') or ''}
    if include_public_content:
        projected['description'] = data.get('description') or ''
        if include_sections:
            sections = data.get('sections')
            projected['sections'] = [
                {'title': sec.get('title') or ''}
                for sec in (sections if isinstance(sections, list) else [])
                if isinstance(sec, dict)
            ]
    return projected


def _list_template_rows(
    where: list[str],
    params: dict,
    order_by: str,
    *,
    include_public_content: bool = False,
    include_sections: bool = True,
    page: int = 1,
    page_size: int = 200,
) -> tuple[list[dict], int]:
    """Lấy danh sách mẫu đơn theo lô nhỏ với content rút gọn để tránh vượt ngưỡng TCP."""
    if include_public_content and include_sections:
        pg_content_expr = """jsonb_build_object(
            'code', COALESCE(content->>'code', ''),
            'description', COALESCE(content->>'description', ''),
            'sections', COALESCE(
                (
                    SELECT jsonb_agg(jsonb_build_object('title', COALESCE(sec->>'title', '')))
                    FROM jsonb_array_elements(
                        CASE WHEN jsonb_typeof(content->'sections') = 'array'
                             THEN content->'sections'
                             ELSE '[]'::jsonb END
                    ) AS sec
                ),
                '[]'::jsonb
            )
        ) AS content"""
    elif include_public_content:
        pg_content_expr = """jsonb_build_object(
            'code', COALESCE(content->>'code', ''),
            'description', COALESCE(content->>'description', '')
        ) AS content"""
    else:
        pg_content_expr = "jsonb_build_object('code', COALESCE(content->>'code', '')) AS content"
    items: list[dict] = []
    where_sql = ' AND '.join(where)
    with _seeded_read_connection(fresh=True) as conn:
        is_pg = getattr(getattr(conn, 'dialect', None), 'name', '') == 'postgresql'
        content_col = pg_content_expr if is_pg else 'content'
        select_cols = f'''id, template_group_id, version, status, name, domain, procedure_id, {content_col},
                             (word_data IS NOT NULL) AS has_word_file,
                             (reference_pdf IS NOT NULL) AS has_reference_pdf,
                             word_filename, reference_pdf_filename,
                             created_at, updated_at, deleted_at'''
        sql = (
            f'SELECT page.*, totals.total AS _total FROM '
            f'(SELECT count(*) AS total FROM public.custom_form_templates WHERE {where_sql}) totals '
            f'LEFT JOIN (SELECT {select_cols} FROM public.custom_form_templates'
            f' WHERE {where_sql} ORDER BY {order_by}, id LIMIT :_limit OFFSET :_offset) page ON 1=1 '
            f'ORDER BY page.{order_by}, page.id'
        )
        batch = conn.execute(text(sql), {**params, '_limit': page_size, '_offset': (page - 1) * page_size}).mappings().all()
    total = batch[0]['_total'] if batch else 0
    for row in batch:
        item_row = dict(row)
        item_row.pop('_total', None)
        if item_row.get('id') is None:
            continue
        if not is_pg:
            item_row['content'] = _project_list_content(
                item_row.get('content'),
                include_public_content=include_public_content,
                include_sections=include_sections,
            )
        items.append(_view(item_row))
    return items, total


@router.get('/admin/form-templates')
def list_admin(domain: str | None = None, status: str | None = None, include_deleted: bool = False,
               page: int = Query(1, ge=1, le=1000), page_size: int = Query(200, ge=1, le=200),
               user: CurrentUser = Depends(require_role('admin'))):
    where=['1=1']
    params={}
    if domain: where.append('domain=:domain'); params['domain']=domain
    if status: where.append('status=:status'); params['status']=status
    if not include_deleted: where.append('deleted_at IS NULL')
    items, total = catalog_cache.get_or_load('custom_templates', ('admin', domain, status, include_deleted, page, page_size), 30,
        lambda: _list_template_rows(where, params, 'updated_at DESC', include_public_content=False, page=page, page_size=page_size))
    return {'data':{'items':items,'total':total},'error':None}


@router.post('/admin/form-templates', status_code=201)
def create_template(body: TemplateBody, user: CurrentUser=Depends(require_role('admin'))):
    content=body.model_dump(mode='json')
    errors=_validate_content(content, for_publish=False)
    if errors: raise HTTPException(422,'; '.join(errors))
    template_id,group_id=str(uuid.uuid4()),str(uuid.uuid4())
    with db_connection(write=True) as conn:
        row=conn.execute(text(f'''INSERT INTO public.custom_form_templates (id,template_group_id,name,domain,procedure_id,content)
          VALUES (:id,:group_id,:name,:domain,:procedure_id,CAST(:content AS jsonb)) RETURNING {_META_COLS}'''),
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

        row = conn.execute(
            text(
                f'''INSERT INTO public.custom_form_templates
                (id, template_group_id, version, status, name, domain, procedure_id, content, word_data, word_filename, reference_pdf, reference_pdf_filename)
                SELECT :id, :group, 1, 'draft', :name, src.domain, src.procedure_id, CAST(:content AS jsonb),
                       src.word_data, src.word_filename, src.reference_pdf, src.reference_pdf_filename
                FROM public.custom_form_templates AS src
                WHERE src.id = :src_id AND src.deleted_at IS NULL
                RETURNING {_META_COLS}'''
            ),
            {
                'id': new_id,
                'group': new_group_id,
                'name': new_name,
                'content': __import__('json').dumps(tpl_content, ensure_ascii=False),
                'src_id': str(template_id),
            },
        ).mappings().one()
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
        _row(conn,template_id,lock=True)
        row=conn.execute(text(f'''UPDATE public.custom_form_templates SET name=:name,domain=:domain,procedure_id=:procedure_id,
          content=CAST(:content AS jsonb),updated_at=now() WHERE id=:id RETURNING {_META_COLS}'''),
          {'id':template_id,'name':content['name'].strip(),'domain':content['domain'],'procedure_id':content.get('procedure_id'),
           'content':__import__('json').dumps(content,ensure_ascii=False)}).mappings().one()
    _invalidate_template_caches()
    return {'data':_view(dict(row)),'error':None}


@router.post('/admin/form-templates/{template_id}/new-version',status_code=201)
def new_version(template_id: str,user: CurrentUser=Depends(require_role('admin'))):
    with db_connection(write=True) as conn:
        old=_row(conn,template_id,lock=True)
        existing=conn.execute(text(f"SELECT {_META_COLS} FROM public.custom_form_templates WHERE template_group_id=:group AND status='draft' AND deleted_at IS NULL ORDER BY version DESC LIMIT 1"),{'group':old['template_group_id']}).mappings().first()
        if existing: return {'data':_view(dict(existing)),'error':None}
        version=conn.execute(text('SELECT COALESCE(MAX(version),0)+1 FROM public.custom_form_templates WHERE template_group_id=:group'),{'group':old['template_group_id']}).scalar_one()
        new_id=str(uuid.uuid4())
        row=conn.execute(text(f'''INSERT INTO public.custom_form_templates (id,template_group_id,version,status,name,domain,procedure_id,content,word_data,word_filename,reference_pdf,reference_pdf_filename)
          SELECT :id,:group,:version,'draft',src.name,src.domain,src.procedure_id,src.content,src.word_data,src.word_filename,src.reference_pdf,src.reference_pdf_filename
          FROM public.custom_form_templates AS src WHERE src.id=:src_id AND src.deleted_at IS NULL RETURNING {_META_COLS}'''),
          {'id':new_id,'group':old['template_group_id'],'version':version,'src_id':str(template_id)}).mappings().one()
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
        pdf_data=await reference_pdf.read(MAX_PDF+1)
        compare_word = word_data
        if not compare_word:
            compare_word = _read_template_bytea(template_id, 'word_data')
        _validate_upload(reference_pdf, pdf_data, pdf=True, word_data=compare_word)
    with db_connection(write=True) as conn:
        _row(conn,template_id,lock=True)
        result=conn.execute(text(f'''UPDATE public.custom_form_templates SET word_data=COALESCE(:word,word_data),word_filename=COALESCE(:filename,word_filename),
          reference_pdf=COALESCE(:pdf,reference_pdf),reference_pdf_filename=COALESCE(:pdf_filename,reference_pdf_filename),updated_at=now()
          WHERE id=:id RETURNING {_META_COLS}'''),{'id':template_id,'word':word_data,'filename':Path(word.filename).name if word and word.filename else None,'pdf':pdf_data,
            'pdf_filename':Path(reference_pdf.filename).name if pdf_data else None}).mappings().one()
    _invalidate_template_caches()
    return {'data':_view(dict(result)),'error':None}


@router.get('/admin/form-templates/{template_id}/document')
def download_source(template_id: str,user: CurrentUser=Depends(require_role('admin'))):
    with db_connection() as conn:
        row=_row(conn,template_id)
    if not row.get('has_word_file'):
        raise HTTPException(404,'Mẫu chưa có file Word.')
    word_bytes=_read_template_bytea(template_id,'word_data')
    if not word_bytes:
        raise HTTPException(404,'Mẫu chưa có file Word.')
    filename = row.get("word_filename") or "mau.docx"
    return Response(word_bytes,media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers={'Content-Disposition':_content_disposition(filename, 'attachment')})


@router.post('/admin/form-templates/{template_id}/check')
def check_document(template_id: str,user: CurrentUser=Depends(require_role('admin'))):
    with db_connection() as conn:
        row=_row(conn,template_id)
    if not row.get('has_word_file'):
        raise HTTPException(404,'Tải lên file Word trước khi kiểm tra.')
    word_bytes=_read_template_bytea(template_id,'word_data')
    if not word_bytes:
        raise HTTPException(404,'Tải lên file Word trước khi kiểm tra.')
    return {'data':analyze_document(row['content'],word_bytes),'error':None}


@router.get('/admin/form-templates/{template_id}/word-editor')
def load_word_editor(template_id: str, user: CurrentUser=Depends(require_role('admin'))):
    from app.word_editor import document_model
    with db_connection() as conn:
        row = _row(conn, template_id)
    if not row.get('has_word_file'):
        raise HTTPException(404, 'Tải lên file Word trước khi mở trình sửa.')
    word_bytes = _read_template_bytea(template_id, 'word_data')
    if not word_bytes:
        raise HTTPException(404, 'Tải lên file Word trước khi mở trình sửa.')
    _, _, model = document_model(word_bytes)
    return {'data': model, 'error': None}


@router.put('/admin/form-templates/{template_id}/word-editor')
def save_word_editor(template_id: str, body: WordEditBody, user: CurrentUser=Depends(require_role('admin'))):
    from app.word_editor import edit_document
    with db_connection() as conn:
        row = _row(conn, template_id)
    if not row.get('has_word_file'):
        if sha256(b'').hexdigest() != body.version:
            raise HTTPException(409, 'File Word đã thay đổi. Hãy mở lại để tránh ghi đè.')
        raise HTTPException(404, 'Mẫu chưa có file Word.')
    original = _read_template_bytea(template_id, 'word_data') or b''
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
    with db_connection(write=True) as conn:
        locked = _row(conn, template_id, lock=True)
        if _template_word_sha256(conn, template_id, has_word_file=bool(locked.get('has_word_file'))) != body.version:
            raise HTTPException(409, 'File Word đã thay đổi. Hãy mở lại để tránh ghi đè.')
        conn.execute(text('UPDATE public.custom_form_templates SET word_data=:word,updated_at=now() WHERE id=:id'), {'word': updated, 'id': template_id})
    _invalidate_template_caches()
    return {'data': {'version': sha256(updated).hexdigest()}, 'error': None}


@router.post('/admin/form-templates/{template_id}/publish')
def publish_template(template_id: str,user: CurrentUser=Depends(require_role('admin'))):
    with db_connection() as conn:
        row=_row(conn,template_id)
    if row['status'] not in ('draft','hidden','published'): raise HTTPException(409,'Chỉ xuất bản được bản nháp, mẫu đang ẩn hoặc đang xuất bản.')
    if not row.get('has_word_file'): raise HTTPException(422,'Tải file Word đã gắn trường lên trước khi xuất bản.')
    word_bytes=_read_template_bytea(template_id,'word_data')
    if not word_bytes: raise HTTPException(422,'Tải file Word đã gắn trường lên trước khi xuất bản.')
    report=analyze_document(row['content'],word_bytes)
    if not report['valid']: raise HTTPException(422,'Chưa xuất bản được: '+'; '.join(report['errors']))
    expected_hash=sha256(word_bytes).hexdigest()
    with db_connection(write=True) as conn:
        locked=_row(conn,template_id,lock=True)
        if locked['status'] not in ('draft','hidden','published'): raise HTTPException(409,'Chỉ xuất bản được bản nháp, mẫu đang ẩn hoặc đang xuất bản.')
        if not locked.get('has_word_file'): raise HTTPException(422,'Tải file Word đã gắn trường lên trước khi xuất bản.')
        if _template_word_sha256(conn,template_id,has_word_file=True) != expected_hash:
            raise HTTPException(409,'File Word vừa thay đổi, vui lòng thử xuất bản lại.')
        if locked['content'] != row['content']:
            report=analyze_document(locked['content'],word_bytes)
            if not report['valid']: raise HTTPException(422,'Chưa xuất bản được: '+'; '.join(report['errors']))
        conn.execute(text("UPDATE public.custom_form_templates SET status='archived',updated_at=now() WHERE template_group_id=:group AND id<>:id AND status IN ('published','hidden') AND deleted_at IS NULL"),{'group':locked['template_group_id'],'id':template_id})
        updated=conn.execute(text(f"UPDATE public.custom_form_templates SET status='published',updated_at=now() WHERE id=:id RETURNING {_META_COLS}"),{'id':template_id}).mappings().one()
    _invalidate_template_caches()
    return {'data':_view(dict(updated)),'error':None}


@router.post('/admin/form-templates/{template_id}/hide')
def hide_template(template_id: str,user: CurrentUser=Depends(require_role('admin'))):
    with db_connection(write=True) as conn:
        row=_row(conn,template_id,lock=True)
        if row['status']!='published': raise HTTPException(409,'Chỉ ẩn được mẫu đang xuất bản.')
        updated=conn.execute(text(f"UPDATE public.custom_form_templates SET status='hidden',updated_at=now() WHERE id=:id RETURNING {_META_COLS}"),{'id':template_id}).mappings().one()
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
        row=conn.execute(text(f'UPDATE public.custom_form_templates SET deleted_at=NULL,updated_at=now() WHERE id=:id RETURNING {_META_COLS}'),{'id':template_id}).mappings().first()
        if not row: raise HTTPException(404,'Không tìm thấy mẫu đơn.')
    _invalidate_template_caches()
    return {'data':_view(dict(row)),'error':None}


def warm_public_templates_cache() -> None:
    """Làm ấm bộ đệm danh sách mẫu đơn công khai (compact) ngay sau khi service khởi động."""
    where = ["status='published'", 'deleted_at IS NULL']
    catalog_cache.get_or_load(
        'custom_templates',
        ('public', None, None, 1, 200, True),
        60,
        lambda: _list_template_rows(where, {}, 'name', include_public_content=True, include_sections=False, page=1, page_size=200),
    )


@router.get('/form-templates')
def list_public(response: Response, domain: str | None=None, procedure_id: str | None=None,
                compact: bool = Query(default=False),
                page: int = Query(1, ge=1, le=1000), page_size: int = Query(200, ge=1, le=200)):
    where=["status='published'",'deleted_at IS NULL']; params={}
    if domain: where.append('domain=:domain'); params['domain']=domain
    if procedure_id: where.append('procedure_id=:procedure_id'); params['procedure_id']=procedure_id
    items, total = catalog_cache.get_or_load('custom_templates', ('public', domain, procedure_id, page, page_size, compact), 60,
        lambda: _list_template_rows(where, params, 'name', include_public_content=True, include_sections=not compact, page=page, page_size=page_size))
    response.headers['Cache-Control'] = 'public, max-age=60, stale-while-revalidate=300'
    return {'data':{'items':items,'total':total},'error':None}


@router.get('/form-templates/{template_id}')
def get_public(template_id: str):
    with _seeded_read_connection() as conn:
        row=_row(conn,template_id,public=True)
    view = _view(row)
    view['content'] = optional_template_recipients(view['content'])
    return {'data':view,'error':None}


@router.get('/form-templates/{template_id}/reference-pdf')
def get_reference_pdf(template_id: str):
    with db_connection() as conn:
        row=_row(conn,template_id,public=True)
        word_hash = _template_word_sha256(conn, template_id, has_word_file=True) if (not row.get('has_reference_pdf') and row.get('has_word_file')) else None
    if row.get('has_reference_pdf'):
        pdf_bytes = _read_template_bytea(template_id, 'reference_pdf', public=True)
        if pdf_bytes:
            filename = Path(row.get('reference_pdf_filename') or 'mau-tham-khao.pdf').name
            status, detail = check_pdf_health(pdf_bytes)
            headers = {
                'Content-Disposition': _content_disposition(filename, 'inline'),
                'Cache-Control': 'public, max-age=300',
                'X-Content-Type-Options': 'nosniff'
            }
            if status == 'DEFECTIVE_FONT':
                logger.warning(
                    f"Template {template_id} reference PDF có nghi vấn lỗi font: {detail}. "
                    f"Phục vụ nguyên bản file PDF đã lưu (không tự ý render lại bằng ReportLab để tránh làm sai lệch bố cục biểu mẫu)."
                )
                headers['X-PDF-Quality-Warning'] = 'defective-font-encoding'
            elif status == 'CORRUPTED':
                logger.warning(f"Template {template_id} reference PDF không đọc được cấu trúc: {detail}")
            return Response(pdf_bytes, media_type='application/pdf', headers=headers)
    if row.get('has_word_file'):
        try:
            cache_hash = word_hash or ''
            pdf_bytes = _PDF_CACHE.get(cache_hash) if cache_hash else None
            if pdf_bytes is None:
                raw_word = _read_template_bytea(template_id, 'word_data', public=True)
                if raw_word:
                    cache_hash = sha256(raw_word).hexdigest()
                    pdf_bytes = _PDF_CACHE.get(cache_hash)
                    if pdf_bytes is None:
                        pdf_bytes = word_to_pdf(raw_word)
                        if len(_PDF_CACHE) >= 64:
                            _PDF_CACHE.clear()
                        _PDF_CACHE[cache_hash] = pdf_bytes
            if pdf_bytes is not None:
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
    with db_connection() as conn:
        row=_row(conn,template_id,public=True)
    if not row.get('has_word_file'):
        raise HTTPException(404,'Mẫu chưa có file Word.')
    word_bytes=_read_template_bytea(template_id,'word_data',public=True)
    if not word_bytes:
        raise HTTPException(404,'Mẫu chưa có file Word.')
    filename = Path(row.get('word_filename') or 'mau.docx').name
    return Response(word_bytes,media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers={
        'Content-Disposition': _content_disposition(filename, 'attachment'),
        'Cache-Control':'public, max-age=300'})


def _export(template_id: str,payload: FillBody,*,pdf: bool,user: CurrentUser,admin: bool=False):
    with _seeded_read_connection() as conn:
        row=_row(conn,template_id,public=not admin)
    if not row.get('has_word_file'): raise HTTPException(422,'Tải file Word đã gắn dấu lên trước khi thử.')
    word_bytes=_read_template_bytea(template_id,'word_data',public=not admin)
    if not word_bytes: raise HTTPException(422,'Tải file Word đã gắn dấu lên trước khi thử.')
    report=analyze_document(row['content'],word_bytes)
    if not report['valid']: raise HTTPException(503,'Mẫu này chưa vượt qua kiểm tra file Word.')
    if not admin:
        from app.services.profile_binding import bind_custom_profile
        bind_custom_profile(row['content'], payload, str(user.id))
    _validate_payload(row['content'], payload, citizen=not admin)
    filled=fill_word(word_bytes, row['content'], payload, citizen=not admin)
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
    with _seeded_read_connection() as conn:
        row=_row(conn,template_id,public=True)
    if not row.get('has_word_file'): raise HTTPException(422,'Mẫu chưa có file Word để tạo hồ sơ.')
    word_bytes=_read_template_bytea(template_id,'word_data',public=True)
    if not word_bytes: raise HTTPException(422,'Mẫu chưa có file Word để tạo hồ sơ.')
    report=analyze_document(row['content'],word_bytes)
    if not report['valid']: raise HTTPException(503,'Mẫu này chưa sẵn sàng để tạo hồ sơ.')
    from app.services.profile_binding import bind_custom_profile
    bind_custom_profile(row['content'], payload, str(user.id))
    _validate_payload(row['content'], payload, citizen=True)
    submission_id=str(uuid.uuid4())
    with db_connection(write=True) as conn:
        _row(conn,template_id,public=True)
        values_sql = 'CAST(:values AS jsonb)' if conn.dialect.name == 'postgresql' else ':values'
        saved=conn.execute(text(f'''INSERT INTO public.custom_form_submissions (id,template_id,user_id,status,form_values)
           VALUES (:id,:template_id,:user_id,:status,{values_sql}) RETURNING id,template_id,status,created_at'''),
           {'id':submission_id,'template_id':template_id,'user_id':str(user.id),'status':payload.status,
            'values':json.dumps({'values':payload.values,'groups':payload.groups},ensure_ascii=False)}).mappings().one()
    return {'data':{**dict(saved),'id':str(saved['id']),'template_id':str(saved['template_id']),'created_at':str(saved['created_at'])},'error':None}
