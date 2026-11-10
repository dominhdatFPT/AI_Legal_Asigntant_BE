from datetime import date
from io import BytesIO
from pathlib import Path

from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.mau18 import Submission, fill_template
from app.core.security import get_current_user
from app.main import app

ROOT = Path(__file__).resolve().parents[1]


def payload():
    return {'receiving_agency': 'Văn phòng đăng ký đất đai',
            'people': [{'name': 'NGUYỄN VĂN AN', 'identity': '012345678901', 'address': 'Đà Nẵng'},
                       {'name': 'TRẦN THỊ BÌNH', 'identity': '012345678902', 'address': 'Đà Nẵng'}],
            'change_content': 'Nhận chuyển nhượng', 'attached_documents': 'Giấy chứng nhận',
            'place': 'Đà Nẵng', 'date': '2026-10-29'}


def test_mau18_api_creates_docx_from_database(db_engine):
    data = (ROOT / 'database/mau18-two-person.docx').read_bytes()
    with db_engine.begin() as conn:
        conn.execute(text('CREATE TABLE public.form_templates (id TEXT, domain TEXT, file_name TEXT, is_active BOOLEAN, file_data BLOB, preview_pdf BLOB)'))
        conn.execute(text('CREATE TABLE public.form_template_preview_pages (template_id TEXT, page_number INTEGER, image_data BLOB)'))
        conn.execute(text('INSERT INTO public.form_templates VALUES (:id,:domain,:filename,:active,:data,:preview)'),
                     {'id':'pilot','domain':'land','filename':'mau18-hai-nguoi.docx','active':True,'data':data,'preview':b'%PDF-1.4 preview'})
        conn.execute(text('INSERT INTO public.form_template_preview_pages VALUES (:id,1,:image)'), {'id':'pilot', 'image':b'fake-png'})
    app.dependency_overrides[get_current_user] = lambda: object()
    try:
        client = TestClient(app)
        preview = client.get('/api/v1/procedures/templates/mau18-two-person/preview')
        assert preview.status_code == 200 and preview.content.startswith(b'%PDF-')
        image = client.get('/api/v1/procedures/templates/mau18-two-person/preview-page/1')
        assert image.status_code == 200 and image.content == b'fake-png'
        assert client.get('/api/v1/procedures/templates/mau18-two-person/preview-page/4').status_code == 404
        response = client.post('/api/v1/procedures/templates/mau18-two-person/docx', json=payload())
        assert response.status_code == 200, response.text
        assert response.headers['content-type'].startswith('application/vnd.openxmlformats')
        doc = Document(BytesIO(response.content))
        content = '\n'.join([p.text for p in doc.paragraphs] + [c.text for t in doc.tables for r in t.rows for c in r.cells])
        assert 'NGUYỄN VĂN AN' in content and 'TRẦN THỊ BÌNH' in content
        assert '{{' not in content
        assert 'ngày 29 tháng 10 năm 2026' in content
        refs = [r for p in doc.paragraphs for r in p.runs if r.text in ('(1)', '(2)', '(3)', '(4)', '(5)', '(6)', '(7)')]
        assert len(refs) == 7 and all(r.font.superscript for r in refs)
        one = client.post('/api/v1/procedures/templates/mau18-two-person/docx', json={**payload(), 'people': payload()['people'][:1]})
        assert one.status_code == 200, one.text
        assert not Document(BytesIO(one.content)).tables
        many_payload = {**payload(), 'people': payload()['people'] + [dict(payload()['people'][0], name='LÊ VĂN BA')]}
        many = client.post('/api/v1/procedures/templates/mau18-two-person/docx', json=many_payload)
        assert many.status_code == 200, many.text
        assert 'LÊ VĂN BA' in '\n'.join(c.text for r in Document(BytesIO(many.content)).tables[0].rows for c in r.cells)
        pdf = client.post('/api/v1/procedures/templates/mau18-two-person/preview-filled', json=many_payload)
        assert pdf.status_code == 200, pdf.text
        assert pdf.content.startswith(b'%PDF-')
        one_pdf = client.post('/api/v1/procedures/templates/mau18-two-person/preview-filled', json={**payload(), 'people': payload()['people'][:1]})
        assert one_pdf.status_code == 200 and one_pdf.content != pdf.content
        assert client.post('/api/v1/procedures/templates/mau18-two-person/docx', json={**payload(), 'people': []}).status_code == 422
        assert client.post('/api/v1/procedures/templates/mau18-two-person/docx', json={**payload(), 'people': payload()['people'] * 11}).status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_empty_date_is_accepted():
    assert Submission(**{**payload(), 'date': ''}).date is None
