from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from early_access.models import EarlyAccessLead


@override_settings(EARLY_ACCESS_WEBHOOK_SECRET='test-only-shared-value', EARLY_ACCESS_RATE_LIMIT=2)
class EarlyAccessApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse('early_access:early-access')
        self.headers = {'HTTP_X_LEAD_WEBHOOK_SECRET': 'test-only-shared-value'}
        self.payload = {
            'fullName': 'Test User',
            'companyName': 'Test Company',
            'contact': 'lead@example.test',
            'industry': 'Services',
            'preferredChannel': 'Telegram',
            'note': '',
            'consent': True,
            'locale': 'ru',
            'receivedAt': '2026-08-09T10:00:00Z',
        }

    def post(self, payload=None, **headers):
        return self.client.post(
            self.url,
            self.payload if payload is None else payload,
            format='json',
            **(headers or self.headers),
        )

    def test_exact_landing_payload_is_stored(self):
        response = self.post()
        self.assertEqual((response.status_code, response.json()['code']), (201, 'STORED'))
        lead = EarlyAccessLead.objects.get()
        self.assertEqual(lead.contact, 'lead@example.test')
        self.assertTrue(lead.consent)

    def test_validation_and_consent(self):
        response = self.post({**self.payload, 'contact': 'invalid', 'consent': False})
        self.assertEqual((response.status_code, response.json()['code']), (400, 'INVALID'))
        self.assertEqual(EarlyAccessLead.objects.count(), 0)

    def test_honeypot_is_rejected_without_storage(self):
        response = self.post({**self.payload, 'website': 'bot.example'})
        self.assertEqual((response.status_code, response.json()['code']), (400, 'HONEYPOT_REJECTED'))
        self.assertEqual(EarlyAccessLead.objects.count(), 0)

    def test_invalid_secret_is_rejected(self):
        response = self.post(**{'HTTP_X_LEAD_WEBHOOK_SECRET': 'wrong-test-value'})
        self.assertEqual((response.status_code, response.json()['code']), (403, 'INVALID_SECRET'))

    def test_replayed_payload_is_idempotent(self):
        first = self.post()
        second = self.post()
        self.assertEqual(first.status_code, 201)
        self.assertEqual((second.status_code, second.json()['code']), (200, 'DUPLICATE'))
        self.assertEqual(EarlyAccessLead.objects.count(), 1)

    def test_cache_rate_limit(self):
        self.post()
        self.post({**self.payload, 'contact': 'second@example.test'})
        third = self.post({**self.payload, 'contact': 'third@example.test'})
        self.assertEqual((third.status_code, third.json()['code']), (429, 'RATE_LIMITED'))
