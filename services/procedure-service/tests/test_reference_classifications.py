import unittest
from app.services.reference_classifications import document_classifications


class ReferenceClassificationTests(unittest.TestCase):
    def test_keys_domains_and_real_codes(self):
        data = document_classifications()
        self.assertEqual(len(data['items']), 25)
        self.assertEqual(len({i['key'] for i in data['items']}), 25)
        domains = {i['code'] for i in data['domains']}
        self.assertEqual(domains, {'land', 'business', 'cooperative', 'social_insurance'})
        for item in data['items']:
            self.assertIn(item['domain'], domains)
            self.assertTrue(item['name'])
            if item['kind'] == 'document_classification':
                self.assertIsNone(item['code'])
                self.assertIsNone(item['procedure_id'])
            else:
                self.assertEqual(item['code'], item['key'])
                self.assertTrue(item['procedure_id'])

    def test_mismatched_sources_have_distinct_classifications(self):
        by_key = {i['key']: i for i in document_classifications()['items']}
        self.assertEqual(by_key['social-insurance-authorization']['domain'], 'social_insurance')
        self.assertEqual(by_key['coop-branch-registration']['domain'], 'cooperative')
        self.assertIn('hộ kinh doanh', by_key['household-certificate-reissue']['name'])
        self.assertNotIn('DN-001', by_key)
        self.assertNotIn('DN-010', by_key)


if __name__ == '__main__':
    unittest.main()
