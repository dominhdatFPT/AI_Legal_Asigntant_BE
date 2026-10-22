"""Public blank library must stay separate from private contract storage/auth."""
import hashlib
import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from app.main import app
from app.api.reference_catalog import ROOT, snapshot


class ReferenceCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_complete_read_only_inventory_and_domains(self):
        response = self.client.get('/api/v1/contracts/reference-catalog')
        self.assertEqual(response.status_code, 200)
        data = response.json()['data']
        self.assertEqual(data['counts'], {'templates': 30, 'pages': 114})
        self.assertEqual(len(data['items']), 30)
        self.assertTrue(all(i['view_only'] and not i['can_fill'] for i in data['items']))
        for domain, count in [('land', 18), ('business', 10), ('cooperative', 1), ('social_insurance', 1)]:
            result = self.client.get('/api/v1/contracts/reference-catalog', params={'domain': domain}).json()['data']
            self.assertEqual(result['pagination']['total'], count)

    def test_search_multiple_procedures_and_status(self):
        def find(**params):
            return self.client.get('/api/v1/contracts/reference-catalog', params=params).json()['data']
        self.assertEqual(find(q='thue mat bang')['pagination']['total'], 1)
        self.assertEqual(find(procedure='DD-008')['pagination']['total'], 4)
        self.assertEqual(find(procedure='DD-006')['pagination']['total'], 4)
        self.assertEqual(find(status='needs_review')['pagination']['total'], 2)
        self.assertEqual(find(status='specific_case')['pagination']['total'], 2)
        self.assertEqual(find(domain='land', procedure='DN-006')['pagination']['total'], 0)

    def test_pagination_does_not_lose_documents(self):
        ids = []
        for page in range(1, 4):
            result = self.client.get('/api/v1/contracts/reference-catalog', params={'page': page, 'page_size': 12}).json()['data']
            ids += [i['id'] for i in result['items']]
        self.assertEqual(len(ids), 30)
        self.assertEqual(len(set(ids)), 30)
        self.assertEqual(self.client.get('/api/v1/contracts/reference-catalog?page_size=101').status_code, 422)

    def test_correct_names_and_no_false_company_procedure(self):
        by_source = {i['source_id']: i for i in snapshot()['items']}
        insurance = by_source['1DaLZxSM3DKJGfOgzSlaNrakDOpuum6LB']
        self.assertEqual(insurance['domain'], 'social_insurance')
        self.assertIsNone(insurance['procedure_code'])
        self.assertNotIn('DN-001', [p['code'] for p in insurance['procedures']])
        coop = by_source['1rMA2pUDJOscOMJrfh-SRoWs9MvwZDLS1']
        self.assertEqual(coop['domain'], 'cooperative')
        self.assertNotIn('DN-010', [p['code'] for p in coop['procedures']])
        self.assertIn('một thành viên', by_source['1Y1LzRK0D3JLMTAj1rFlUJOscM6oGX3tF']['name'])
        self.assertIn('chuyển hình thức', by_source['1G5hN83wwi4yrdjG9D3-zEEAdnlPZNFd0']['name'])
        self.assertIn('hộ kinh doanh', by_source['173feKCGvyipnO48anmGUhr8CaeFkjQpQ']['name'])

    def test_all_asset_hashes_and_document_pages_are_preserved(self):
        data = snapshot()
        self.assertEqual(len(data['asset_hashes']), 144)
        for name, expected in data['asset_hashes'].items():
            content = (ROOT / name).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), expected)
            self.assertTrue(content.startswith(b'%PDF-') if name.endswith('.pdf') else content.startswith(b'\x89PNG'))
        self.assertEqual(sum(len(i['pages']) for i in data['items']), 114)
        self.assertTrue(all(len(i['page_dimensions']) == len(i['pages']) for i in data['items']))

    def test_asset_whitelist_and_private_endpoints(self):
        name = next(n for n in snapshot()['asset_hashes'] if n.endswith('.png'))
        response = self.client.get('/api/v1/contracts/reference-assets/' + name)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
        for name in ['catalog.json', 'jwt_private.pem', 'unknown.png', '..%2F..%2Fkeys%2Fjwt_private.pem']:
            self.assertEqual(self.client.get('/api/v1/contracts/reference-assets/' + name).status_code, 404)
        self.assertEqual(self.client.get('/api/v1/contracts/drafts').status_code, 401)
        self.assertEqual(self.client.post('/api/v1/contracts/template-catalog', json={'name': 'x'}).status_code, 401)
        template = snapshot()['items'][0]
        detail = self.client.get('/api/v1/contracts/reference-catalog/' + template['id'])
        self.assertEqual(detail.json()['data']['id'], template['id'])
        self.assertEqual(self.client.get('/api/v1/contracts/reference-catalog/00000000-0000-0000-0000-000000000000').status_code, 404)


if __name__ == '__main__':
    unittest.main()
