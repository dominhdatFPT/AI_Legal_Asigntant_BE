"""AI hours remain a fallback and never become independently verified facts."""
import asyncio
import json
from pathlib import Path
import sys
import unittest

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/location-service'))
from app.services.web_contacts import GoogleContacts, ai_summary_hours


OFFICE = {'place_id': 'test-office', 'name': 'UBND phường Hải Châu',
          'address': '15 Lê Hồng Phong, Hải Châu, Đà Nẵng'}
SUMMARY = {'name': OFFICE['name'], 'address': OFFICE['address'],
           'text': 'Thứ Hai–Thứ Sáu · Sáng: 07:30–11:30 · Chiều: 13:30–17:00'}


class Pages:
    async def read(self, url, **kwargs):
        return None

    async def close(self):
        pass


class AIHoursTests(unittest.TestCase):
    def test_exact_office_is_required(self):
        self.assertEqual(ai_summary_hours(OFFICE, SUMMARY)['verification_status'], 'unverified')
        for fact in [None, {}, {**SUMMARY, 'name': 'UBND phường khác'},
                     {**SUMMARY, 'address': '916 Tôn Đản, Đà Nẵng'},
                     {**SUMMARY, 'text': 'Không tìm thấy giờ'}, {**SUMMARY, 'text': '07:30' * 101}]:
            with self.subTest(fact=fact):
                self.assertIsNone(ai_summary_hours(OFFICE, fact))

    def test_search_summary_fills_missing_hours_without_source_or_verified_status(self):
        async def run():
            def respond(request):
                prompt = json.loads(request.content)['contents'][0]['parts'][0]['text']
                self.assertIn('Do not guess common administrative hours', prompt)
                return httpx.Response(200, json={'candidates': [{
                    'content': {'parts': [{'text': json.dumps({'items': [{
                        'place_id': OFFICE['place_id'], 'phone': None, 'hours': None,
                        'ai_summary_hours': SUMMARY}]})}]},
                    'groundingMetadata': {'webSearchQueries': ['office hours']}}]})
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                provider = GoogleContacts('fake', 'test', client=client, pages=Pages())
                result = (await provider.lookup([OFFICE]))['items'][0]
                self.assertEqual(result['opening_hours']['text'], SUMMARY['text'])
                self.assertEqual(result['opening_hours']['source_type'], 'ai_summary')
                self.assertEqual(result['web_lookup_status'], 'unverified')
                self.assertNotIn('source_url', result['opening_hours'])
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
