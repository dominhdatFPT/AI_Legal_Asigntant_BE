"""Offline PDF inspection and HTTP + PostgreSQL tests on an explicitly disposable DB.

Run with unittest in the Contract image. Integration tests accept only the dedicated
Docker hostname/database below; they never use CONTRACT_DATABASE_URL from .env.
"""
import hashlib
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
import pymupdf
from fastapi.testclient import TestClient
from common_auth import CurrentUser
from common_auth.fastapi_dep import get_current_user

from app.main import app
from app.api import reference_catalog
from app.core.errors import ContractError
from app.services.reference_pdf import MAX_PDF_BYTES, prepare_reference_pdf

ADMIN = '11111111-1111-4111-8111-111111111111'
CLASSIFICATION = {'key': 'DD-001', 'code': 'DD-001', 'name': 'Thủ tục đất đai', 'domain': 'land'}


def pdf(pages=2, password=None):
    with pymupdf.open() as document:
        for index in range(pages):
            page = document.new_page()
            page.insert_text((72, 72), f'PDF TEST TEMPLATE - Page {index + 1}')
        if password:
            return document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                                    owner_pw=password, user_pw=password)
        return document.tobytes()


class PdfInspectionTests(unittest.TestCase):
    def test_original_bytes_and_every_page_are_preserved(self):
        data = pdf()
        item, images = prepare_reference_pdf(data, '../test.pdf', 'Mẫu kiểm thử', 'land', CLASSIFICATION)
        self.assertEqual(item['source_sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(item['source_filename'], 'test.pdf')
        self.assertEqual(item['page_count'], 2)
        self.assertEqual(len(images), 2)
        self.assertTrue(all(image.startswith(b'\x89PNG') for image in images))
        self.assertTrue(all(size['width'] <= 1600 and size['height'] <= 1600 for size in item['page_dimensions']))
        self.assertTrue(item['view_only'])
        self.assertFalse(item['can_fill'])

    def test_invalid_empty_encrypted_and_excessive_pdfs_are_rejected(self):
        for data, filename in [(b'', 'x.pdf'), (b'not a pdf', 'x.pdf'),
                               (b'%PDF-broken', 'x.pdf'), (pdf(), 'x.docx'),
                               (pdf(password='test'), 'x.pdf'), (pdf(101), 'x.pdf'),
                               (b'%PDF-' + b'0' * MAX_PDF_BYTES, 'x.pdf')]:
            with self.subTest(filename=filename, size=len(data)):
                with self.assertRaises(ContractError) as caught:
                    prepare_reference_pdf(data, filename, 'Mẫu kiểm thử', 'land', CLASSIFICATION)
                self.assertEqual(caught.exception.status, 422)


@unittest.skipUnless(os.getenv('TEST_REFERENCE_DATABASE_URL') ==
    'postgresql://postgres@127.0.0.1/reference_upload_tests', 'requires disposable PostgreSQL')
class ReferenceUploadHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = os.environ['TEST_REFERENCE_DATABASE_URL']
        with psycopg.connect(cls.url, autocommit=True) as conn:
            conn.execute('DROP TABLE IF EXISTS contract_reference_uploads, contract_reference_catalog, reference_classifications')
            conn.execute('''CREATE TABLE reference_classifications
                (key text, code text, name text, domain text, kind text, procedure_id uuid)''')
            conn.execute("INSERT INTO reference_classifications VALUES ('DD-001','DD-001','Thủ tục đất đai','land','procedure',NULL), ('HKD-001','HKD-001','Đăng ký hộ kinh doanh','business','procedure',NULL)")
            conn.execute('''CREATE TABLE contract_reference_catalog (
                id uuid, number integer, name text, description text, domain text, procedure_key text,
                procedure_code text, procedure_name text, procedures jsonb, page_count integer, format text,
                pdf_url text, pages jsonb, page_dimensions jsonb, text_content text, content_status text,
                review_notes jsonb, source_id text, source_filename text, source_url text, source_sha256 text,
                pdf_sha256 text, view_only boolean, can_fill boolean)''')
            conn.execute((Path(__file__).parents[1] / 'database/003_reference_uploads.sql').read_text())
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.url) as conn:
            conn.execute('DROP TABLE contract_reference_uploads, contract_reference_catalog, reference_classifications')

    def setUp(self):
        self.env = patch.dict(os.environ, {'CONTRACT_DATABASE_URL': self.url})
        self.env.start()
        self.gate = patch('app.core.security.get_admin_gate')
        self.gate.start()
        with psycopg.connect(self.url) as conn:
            conn.execute('TRUNCATE contract_reference_uploads')
        reference_catalog._CACHE.update(data=None, timestamp=0.0)
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(ADMIN, 'admin')

    def tearDown(self):
        app.dependency_overrides.clear()
        self.gate.stop()
        self.env.stop()
        reference_catalog._CACHE.update(data=None, timestamp=0.0)

    def upload(self, data=None, **values):
        fields = dict(name='Mẫu PDF kiểm thử', domain='land', procedure='DD-001')
        fields.update(values)
        return self.client.post('/api/v1/contracts/reference-catalog', data=fields,
            files={'file': ('mau.pdf', pdf() if data is None else data, 'application/pdf')},
            headers={'Origin': 'http://localhost:3000', 'Authorization': 'Bearer test-admin'})

    def test_upload_list_reload_detail_and_all_assets(self):
        self.assertEqual(self.client.get('/api/v1/contracts/reference-catalog').json()['data']['items'], [])
        data = pdf()
        response = self.upload(data)
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()['data']
        reference_catalog._CACHE.update(data=None, timestamp=0.0)
        listed = self.client.get('/api/v1/contracts/reference-catalog?domain=land').json()['data']['items']
        self.assertEqual([entry['id'] for entry in listed], [item['id']])
        self.assertEqual(self.client.get('/api/v1/contracts/reference-catalog/' + item['id']).status_code, 200)
        for page in item['pages']:
            asset = self.client.get(page.replace('/api/reference-assets/', '/api/v1/contracts/reference-assets/'))
            self.assertEqual(asset.status_code, 200)
            self.assertTrue(asset.content.startswith(b'\x89PNG'))
        original = self.client.get('/api/v1/contracts/reference-assets/' + item['id'] + '.pdf')
        self.assertEqual(original.content, data)
        self.assertEqual(original.headers['x-content-type-options'], 'nosniff')
        self.assertEqual(self.client.get('/api/v1/contracts/reference-assets/' + item['id'] + '-page-3.png').status_code, 404)
        self.assertEqual(self.client.get('/api/v1/contracts/reference-assets/' + str(uuid4()) + '.pdf').status_code, 404)

    def test_business_upload_uses_its_own_classification(self):
        response = self.upload(domain='business', procedure='HKD-001')
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()['data']['procedure_code'], 'HKD-001')
        self.assertEqual(self.client.get('/api/v1/contracts/reference-catalog?domain=land').json()['data']['items'], [])

    def test_duplicate_and_invalid_metadata_do_not_write(self):
        data = pdf()
        self.assertEqual(self.upload(data).status_code, 201)
        self.assertEqual(self.upload(data).status_code, 409)
        for values in [dict(name=' ' * 5), dict(name='x' * 256), dict(procedure='HKD-001'),
                       dict(procedure='unknown'), dict(domain='invalid')]:
            self.assertEqual(self.upload(**values).status_code, 422)
        self.assertEqual(self.upload(b'%PDF-broken').status_code, 422)
        self.assertEqual(self.upload(b'%PDF-' + b'0' * MAX_PDF_BYTES).status_code, 422)
        with psycopg.connect(self.url) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM contract_reference_uploads').fetchone()[0], 1)

    def test_authentication_role_and_origin_are_enforced(self):
        app.dependency_overrides.clear()
        response = self.client.post('/api/v1/contracts/reference-catalog',
            data=dict(name='Mẫu thử', domain='land', procedure='DD-001'),
            files={'file': ('x.pdf', pdf(), 'application/pdf')})
        self.assertEqual(response.status_code, 401)
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(ADMIN, 'citizen')
        self.assertEqual(self.upload().status_code, 403)
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(ADMIN, 'admin')
        response = self.client.post('/api/v1/contracts/reference-catalog',
            data=dict(name='Mẫu thử', domain='land', procedure='DD-001'),
            files={'file': ('x.pdf', pdf(), 'application/pdf')}, headers={'Origin': 'https://untrusted.invalid'})
        self.assertEqual(response.status_code, 403)


if __name__ == '__main__':
    unittest.main()
