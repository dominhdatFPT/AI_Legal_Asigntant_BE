from io import BytesIO

from docx import Document
from fastapi import HTTPException

from app.api.custom_form_templates import (
    FillBody,
    _build_procedure_values,
    _validate_content,
    _validate_payload,
    analyze_document,
    fill_word,
    word_to_pdf,
)


def sample_content():
    return {
        'schema_version': 1,
        'name': 'Đơn thử cấu hình',
        'domain': 'dat_dai',
        'sections': [{
            'id': 'sec_1', 'title': 'Người làm đơn', 'order': 1,
            'blocks': [{
                'block_key': 'free_field', 'instance_id': 'applicant',
                'fields': [{
                    'key': 'name', 'word_key': 'applicant_name', 'label': 'Họ tên',
                    'type': 'text', 'required': True,
                }],
            }, {
                'block_key': 'repeat_group', 'instance_id': 'owners',
                'title': 'Người cùng sử dụng',
                'fields': [{
                    'key': 'name', 'word_key': 'name', 'label': 'Họ tên',
                    'type': 'text', 'required': True,
                }],
            }],
        }],
        'repeat_groups': [{
            'key': 'owners', 'title': 'Người cùng sử dụng',
            'fields': [{
                'key': 'name', 'word_key': 'name', 'label': 'Họ tên',
                'type': 'text', 'required': True,
            }],
        }],
    }


def sample_word():
    document = Document()
    document.add_paragraph('Người làm đơn: {{ applicant_name }}')
    table = document.add_table(rows=1, cols=3)
    table.rows[0].cells[0].text = '{{ # owners }}'
    table.rows[0].cells[1].text = '{{ owners.name }}'
    table.rows[0].cells[2].text = '{{ /owners }}'
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def test_builder_validates_and_accepts_spaced_word_markers():
    content = sample_content()
    assert _validate_content(content) == []
    report = analyze_document(content, sample_word())
    assert report['valid'] is True
    assert report['found'] == ['#owners', '/owners', 'applicant_name', 'owners.name']


def test_repeating_group_fills_arbitrary_number_of_people_and_exports_pdf():
    content = sample_content()
    payload = FillBody(
        values={'applicant.name': 'Nguyễn Thị An'},
        groups={'owners': [{'name': 'Lê Văn Bình'}, {'name': 'Trần Thị Chi'}]},
    )
    _validate_payload(content, payload)
    filled = fill_word(sample_word(), content, payload)
    result = Document(BytesIO(filled))
    text = '\n'.join(p.text for p in result.paragraphs)
    rows = '\n'.join(cell.text for row in result.tables[0].rows for cell in row.cells)
    assert 'Nguyễn Thị An' in text
    assert 'Lê Văn Bình' in rows and 'Trần Thị Chi' in rows
    assert '{{' not in text + rows
    assert word_to_pdf(filled).startswith(b'%PDF-')


def test_required_fields_and_unknown_word_markers_are_reported():
    content = sample_content()
    missing_required = FillBody(values={}, groups={})
    try:
        _validate_payload(content, missing_required)
    except HTTPException as error:
        assert error.status_code == 422
    else:
        raise AssertionError('Required field should have been rejected')

    document = Document()
    document.add_paragraph('{{ applicant_name }} {{ not_configured }}')
    output = BytesIO()
    document.save(output)
    report = analyze_document(content, output.getvalue())
    assert report['valid'] is False
    assert report['unknown_tokens'] == ['not_configured']
    assert report['repeat_groups']['owners']['valid'] is False


def test_repeating_group_rejects_markers_split_across_table_rows():
    document = Document()
    document.add_paragraph('{{applicant_name}}')
    table = document.add_table(rows=3, cols=1)
    table.rows[0].cells[0].text = '{{#owners}}'
    table.rows[1].cells[0].text = '{{owners.name}}'
    table.rows[2].cells[0].text = '{{/owners}}'
    output = BytesIO()
    document.save(output)

    report = analyze_document(sample_content(), output.getvalue())
    assert report['valid'] is False
    assert 'cùng một dòng' in report['repeat_groups']['owners']['message']


def test_standard_form_data_maps_to_filled_word_template_values():
    content = sample_content()
    values = _build_procedure_values(
        content,
        {'applicant_name': 'Nguyễn Thị An'},
        [{'name': 'applicant_name', 'label': 'Họ tên người làm đơn'}],
    )

    assert values['applicant.name'] == 'Nguyễn Thị An'


def test_filling_split_word_tag_preserves_surrounding_run_formatting():
    document = Document()
    paragraph = document.add_paragraph()
    prefix = paragraph.add_run('Họ tên: ')
    prefix.bold = True
    paragraph.add_run('{{applicant_').italic = True
    paragraph.add_run('name}}').bold = True
    suffix = paragraph.add_run(' — Ký tên')
    suffix.italic = True
    table = document.add_table(rows=1, cols=3)
    table.rows[0].cells[0].text = '{{#owners}}'
    table.rows[0].cells[1].text = '{{owners.name}}'
    table.rows[0].cells[2].text = '{{/owners}}'
    output = BytesIO()
    document.save(output)

    filled = Document(BytesIO(fill_word(
        output.getvalue(), sample_content(),
        FillBody(values={'applicant.name': 'Nguyễn Thị An'}, groups={'owners': [{'name': 'Nguyễn Thị An'}]}),
    )))
    paragraph = filled.paragraphs[0]
    assert ''.join(run.text for run in paragraph.runs) == 'Họ tên: Nguyễn Thị An — Ký tên'
    assert paragraph.runs[0].bold is True
    assert paragraph.runs[-1].italic is True


def test_server_validates_phone_and_number_fields_in_repeat_groups():
    content = sample_content()
    content['repeat_groups'][0]['fields'][0]['type'] = 'phone'
    invalid_phone = FillBody(groups={'owners': [{'name': '123'}]})
    try:
        _validate_payload(content, invalid_phone)
    except HTTPException as error:
        assert error.status_code == 422
        assert 'số điện thoại' in str(error.detail)
    else:
        raise AssertionError('Invalid phone should have been rejected')

    content['repeat_groups'][0]['fields'][0]['type'] = 'number'
    invalid_number = FillBody(groups={'owners': [{'name': '1,000'}]})
    try:
        _validate_payload(content, invalid_number)
    except HTTPException as error:
        assert error.status_code == 422
        assert 'số liệu' in str(error.detail)
    else:
        raise AssertionError('Ambiguous number should have been rejected')


def test_admin_template_api_rejects_citizen(auth):
    from fastapi.testclient import TestClient
    from app.main import app

    response = TestClient(app).get(
        '/api/procedures/admin/form-templates', headers=auth(role='citizen')
    )
    assert response.status_code == 403


def test_condition_can_hide_repeat_group_before_word_export():
    content = sample_content()
    content['sections'][0]['blocks'][0]['fields'].append({
        'key': 'has_owners', 'word_key': 'has_owners', 'label': 'Có người cùng sử dụng',
        'type': 'select', 'required': False, 'options': ['Có', 'Không'],
    })
    content['repeat_groups'][0]['condition'] = {
        'field': 'applicant.has_owners', 'equals': 'Có',
    }
    payload = FillBody(
        values={'applicant.name': 'Nguyễn Thị An', 'applicant.has_owners': 'Không'},
        groups={'owners': [{'name': 'Dữ liệu giả bị ẩn'}]},
    )

    _validate_payload(content, payload)
    result = Document(BytesIO(fill_word(sample_word(), content, payload)))
    rendered = '\n'.join(p.text for p in result.paragraphs) + '\n' + '\n'.join(
        cell.text for table in result.tables for row in table.rows for cell in row.cells
    )
    assert 'Dữ liệu giả bị ẩn' not in rendered
    assert '{{' not in rendered
