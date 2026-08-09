"""Tests for the Voice-platform Context Lookup API.

    GET /api/v1/intake/context-lookup/?phone=|site=|community=|address=
"""

import datetime
import hashlib

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from intake.models import ChannelChoice, Contact, JobRecord
from channels.models import ChannelConnection, ChannelStatus, ChannelType
from organizations.models import Organization

_TOKEN = 'test-static-token'
_TOKEN_HASH = hashlib.sha256(_TOKEN.encode()).hexdigest()


@override_settings(EXPECTED_API_TOKEN_SHA256=_TOKEN_HASH)
class ContextLookupTests(APITestCase):

    def setUp(self):
        cache.clear()  # reset throttle history between tests
        self.url = reverse('intake:context-lookup')
        self.organization = Organization.objects.create(name='Voice Test', slug='voice-test')
        self.destination = '+17245768867'
        ChannelConnection.objects.create(
            organization=self.organization,
            type=ChannelType.VOICE,
            provider='twilio',
            display_name='Voice Test',
            external_identifier=self.destination,
            status=ChannelStatus.ACTIVE,
        )

        # Contact A — two jobs at different sites/communities.
        self.contact_a = Contact.objects.create(
            organization=self.organization,
            name='John Smith', phone='+15551234567',
            metadata={'city_state': 'Pittsburgh, PA'},
        )
        self.job_old = JobRecord.objects.create(
            organization=self.organization,
            source_channel=ChannelChoice.VOICE, contact=self.contact_a,
            service_type='Street sweeping', site='200 West Street, Pittsburgh, PA',
            community='Oak Hill', builder='Lennar', project='Lot 7',
        )
        self.job_new = JobRecord.objects.create(
            organization=self.organization,
            source_channel=ChannelChoice.VOICE, contact=self.contact_a,
            service_type='Install', site='189 East Avenue, Pittsburgh, PA 15201',
            community='Maple Ridge', builder='DR Horton', project='Lot 42',
        )
        # Force deterministic recency: job_new is the most recent.
        now = timezone.now()
        JobRecord.objects.filter(pk=self.job_old.pk).update(created_at=now - datetime.timedelta(days=3))
        JobRecord.objects.filter(pk=self.job_new.pk).update(created_at=now - datetime.timedelta(hours=1))

        # A different contact / site — to ensure filters do not leak across contacts.
        self.contact_b = Contact.objects.create(
            organization=self.organization, name='Jane Doe', phone='+15559998888',
        )
        JobRecord.objects.create(
            organization=self.organization,
            source_channel=ChannelChoice.VOICE, contact=self.contact_b,
            service_type='Cleanup', site='55 Main St, Akron, OH 44301',
            community='River Run', builder='Pulte', project='Lot 9',
        )

    # ── helpers ──────────────────────────────────────────────────────────────
    def _auth(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {_TOKEN}')

    def _get(self, **params):
        # secure=True avoids the http→https redirect (SECURE_SSL_REDIRECT).
        params.setdefault('destination', self.destination)
        return self.client.get(self.url, params, secure=True)

    # ── lookup by phone ──────────────────────────────────────────────────────
    def test_lookup_by_phone(self):
        self._auth()
        res = self._get(phone='+15551234567')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.json()
        self.assertTrue(data['found'])
        self.assertEqual(data['match_type'], 'phone')
        self.assertEqual(data['contact']['name'], 'John Smith')
        self.assertEqual(data['contact']['phone'], '+15551234567')

        mr = data['most_recent']  # newest job = 189 East Avenue / Maple Ridge
        self.assertEqual(mr['site'], '189 East Avenue, Pittsburgh, PA 15201')
        self.assertEqual(mr['address'], '189 East Avenue')
        self.assertEqual(mr['city'], 'Pittsburgh')
        self.assertEqual(mr['state'], 'PA')
        self.assertEqual(mr['township'], '')
        self.assertEqual(mr['community'], 'Maple Ridge')
        self.assertEqual(mr['builder'], 'DR Horton')
        self.assertEqual(mr['lot_phase'], 'Lot 42')

        sites = [loc['site'] for loc in data['known_locations']]
        self.assertEqual(len(sites), 2)              # both distinct locations
        self.assertEqual(sites[0], '189 East Avenue, Pittsburgh, PA 15201')  # newest first
        self.assertIn('200 West Street, Pittsburgh, PA', sites)

    def test_lookup_by_phone_handles_formatting(self):
        # Matches on the significant last-10 digits despite formatting.
        self._auth()
        res = self._get(phone='(555) 123-4567')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.json()['found'])

    # ── lookup by site ───────────────────────────────────────────────────────
    def test_lookup_by_site(self):
        self._auth()
        res = self._get(site='189 East Avenue')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.json()
        self.assertTrue(data['found'])
        self.assertEqual(data['match_type'], 'site')
        self.assertEqual(data['most_recent']['community'], 'Maple Ridge')

    # ── lookup by community ──────────────────────────────────────────────────
    def test_lookup_by_community(self):
        self._auth()
        res = self._get(community='Maple Ridge')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.json()
        self.assertTrue(data['found'])
        self.assertEqual(data['match_type'], 'community')
        self.assertEqual(data['known_locations'][0]['community'], 'Maple Ridge')

    # ── lookup by address ────────────────────────────────────────────────────
    def test_lookup_by_address(self):
        self._auth()
        res = self._get(address='200 West Street')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.json()
        self.assertTrue(data['found'])
        self.assertEqual(data['match_type'], 'address')
        self.assertEqual(data['most_recent']['site'], '200 West Street, Pittsburgh, PA')

    # ── no results ───────────────────────────────────────────────────────────
    def test_lookup_no_results(self):
        self._auth()
        res = self._get(phone='+19998887777')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.json()
        self.assertFalse(data['found'])
        self.assertEqual(data['known_locations'], [])
        self.assertIsNone(data['most_recent'])

    def test_no_params_returns_400(self):
        self._auth()
        res = self._get()
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(res.json()['found'])

    # ── auth ─────────────────────────────────────────────────────────────────
    def test_no_token_is_rejected(self):
        res = self._get(phone='+15551234567')
        self.assertIn(res.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_unauthorized_bad_token(self):
        # DRF returns 403 (not 401) for AuthenticationFailed when the auth class
        # defines no WWW-Authenticate header — either way the request is rejected.
        self.client.credentials(HTTP_AUTHORIZATION='Bearer wrong-token')
        res = self._get(phone='+15551234567')
        self.assertIn(res.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
