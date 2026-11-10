"""Isolated auth, snapshot, callback and concurrency checks for ONLYOFFICE."""
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import tempfile
from types import SimpleNamespace
from urllib.parse import urlparse
import unittest
from unittest.mock import patch

from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt

from app.api import office_editor as office
from common_auth.core import CurrentUser


class OfficeTests(unittest.TestCase):
    def setUp(self):
        stream = BytesIO()
        doc = Document()
        doc.add_paragraph('Original {{name}}')
        doc.save(stream)
        self.original = stream.getvalue()
        self.row = dict(status='draft', word_data=self.original, word_filename='test.docx')
        self.updates = []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for mocked in [patch.dict('os.environ', {'ONLYOFFICE_JWT_SECRET': 'test-only-' + 'x' * 40, 'ONLYOFFICE_BACKUP_DIR': self.tmp.name}),
                       patch.object(office, '_row', side_effect=lambda *args, **kwargs: dict(self.row)),
                       patch.object(office, 'db_connection', self.connection)]:
            mocked.start()
            self.addCleanup(mocked.stop)
        office.sessions.clear()
        app = FastAPI()
        app.include_router(office.router)
        self.client = TestClient(app)
        self.user = CurrentUser(id='admin-test', role='admin')

    @contextmanager
    def connection(self, **kwargs):
        def execute(statement, params):
            self.updates.append(params)
            self.row['word_data'] = params['data']
        yield SimpleNamespace(execute=execute)

    def config(self):
        with patch.object(office.requests, 'get', return_value=SimpleNamespace(ok=True, text='true')):
            return office.config('template', self.user)['data']

    def test_admin_required(self):
        response = self.client.post('/admin/form-templates/template/office/config')
        self.assertEqual(response.status_code, 401)

    def test_signed_snapshot_preserves_original(self):
        config = self.config()
        source = urlparse(config['config']['document']['url'])
        response = self.client.get(source.path.replace('/api/v1/procedures', '') + '?' + source.query)
        self.assertEqual(response.content, self.original)
        self.assertEqual(office.backup_path(sha256(self.original).hexdigest()).read_bytes(), self.original)
        self.assertEqual(self.client.get('/office/template/source?ticket=invalid').status_code, 401)

    def test_callback_rejects_bad_token_and_external_download(self):
        config = self.config()
        callback = urlparse(config['config']['editorConfig']['callbackUrl'])
        path = callback.path.replace('/api/v1/procedures', '') + '?' + callback.query
        body = {'key': config['config']['document']['key'], 'status': 6, 'url': 'http://malicious.invalid/cache/file.docx'}
        self.assertEqual(self.client.post(path, json=body).status_code, 401)
        body['token'] = jwt.encode(body, office.secret(), algorithm='HS256')
        self.assertEqual(self.client.post(path, json=body).status_code, 400)
        self.assertFalse(self.updates)

    def test_callback_saves_draft_and_keeps_snapshot(self):
        config = self.config()
        state = office.sessions[config['session']]
        callback = urlparse(config['config']['editorConfig']['callbackUrl'])
        path = callback.path.replace('/api/v1/procedures', '') + '?' + callback.query
        stream = BytesIO()
        doc = Document()
        doc.add_paragraph('Changed {{name}}')
        doc.save(stream)
        updated = stream.getvalue()
        body = {'key': state['key'], 'status': 6, 'url': 'http://onlyoffice/cache/files/test.docx'}
        body['token'] = jwt.encode(body, office.secret(), algorithm='HS256')
        download = SimpleNamespace(status_code=200, iter_content=lambda size: [updated])
        class Response:
            def __enter__(self): return download
            def __exit__(self, *args): pass
        with patch.object(office.requests, 'get', return_value=Response()):
            response = self.client.post(path, json=body)
        self.assertEqual(response.json(), {'error': 0})
        self.assertEqual(self.row['word_data'], updated)
        self.assertEqual(state['revision'], 1)
        self.assertEqual(office.backup_path(state['original']).read_bytes(), self.original)

    def test_force_save_rejects_concurrent_change(self):
        config = self.config()
        self.row['word_data'] = b'Changed by another session'
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as raised:
            office.force_save('template', office.SaveCommand(session=config['session']), self.user)
        self.assertEqual(raised.exception.status_code, 409)


if __name__ == '__main__':
    unittest.main()
