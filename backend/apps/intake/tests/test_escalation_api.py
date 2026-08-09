"""Tests for the Voice Escalation API.

    POST /api/v1/intake/escalate/

A voice escalation must land in the SAME Escalation queue as SMS escalations,
carrying the caller name (if given), transcript and summary.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from intake.models import ChannelChoice, Interaction, InteractionCategory
from channels.models import ChannelConnection, ChannelStatus, ChannelType
from organizations.models import Organization, OrganizationMembership

_TOKEN = 'test-static-token'
_TOKEN_HASH = __import__('hashlib').sha256(_TOKEN.encode()).hexdigest()


@override_settings(EXPECTED_API_TOKEN_SHA256=_TOKEN_HASH)
class EscalationApiTests(APITestCase):

    def setUp(self):
        cache.clear()  # reset throttle history between tests
        self.url = reverse('intake:escalate-create')
        self.organization = Organization.objects.create(name='Escalation Test', slug='escalation-test')
        self.destination = '+17245768867'
        self.connection = ChannelConnection.objects.create(
            organization=self.organization,
            type=ChannelType.VOICE,
            provider='twilio',
            display_name='Voice Test',
            external_identifier=self.destination,
            status=ChannelStatus.ACTIVE,
        )

    def _post(self, payload):
        # secure=True avoids the http→https redirect (SECURE_SSL_REDIRECT).
        payload = {**payload, 'destination': self.destination}
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {_TOKEN}')
        return self.client.post(self.url, payload, format='json', secure=True)

    def _authenticate_staff(self, username):
        User = get_user_model()
        staff = User.objects.create_user(
            username=username, email=f'{username}@test.com', password='pw12345!',
        )
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=staff,
            role='agent',
            status='active',
        )
        self.client.credentials(HTTP_X_ORGANIZATION_ID=str(self.organization.id))
        self.client.force_authenticate(user=staff)

    # ── create ───────────────────────────────────────────────────────────────
    def test_create_voice_escalation_full_payload(self):
        res = self._post({
            'phone': '+13059995694',
            'name': 'John Smith',
            'reason': 'callback_requested',
            'transcript': 'assistant: How can I help?\nuser: Call me back right now!',
            'summary': 'Customer asked for a manager callback.',
            'call_sid': 'CA-test-123',
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        body = res.json()
        self.assertEqual(body['status'], 'escalated')
        self.assertTrue(body['interaction_id'])

        inter = Interaction.objects.get(pk=body['interaction_id'])
        self.assertEqual(inter.category, InteractionCategory.ESCALATION)
        self.assertEqual(inter.channel, ChannelChoice.VOICE)
        self.assertEqual(inter.escalation_reason, 'callback_requested')
        self.assertEqual(inter.summary, 'Customer asked for a manager callback.')
        self.assertIn('Transcript:', inter.raw_content)            # transcript carried
        self.assertIn('Call me back right now!', inter.raw_content)
        self.assertFalse(inter.is_read)
        self.assertEqual(inter.contact.name, 'John Smith')          # name captured
        self.assertEqual(inter.provider_payload.get('call_sid'), 'CA-test-123')
        # provider_id stays blank so it never collides with the voice webhook dedup
        self.assertEqual(inter.provider_id, '')

    def test_create_escalation_minimal(self):
        res = self._post({'reason': 'customer-requested-human'})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        inter = Interaction.objects.get(pk=res.json()['interaction_id'])
        self.assertEqual(inter.category, InteractionCategory.ESCALATION)
        self.assertEqual(inter.escalation_reason, 'customer-requested-human')

    def test_reason_defaults_when_blank(self):
        res = self._post({'phone': '+15551112222'})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        inter = Interaction.objects.get(pk=res.json()['interaction_id'])
        self.assertEqual(inter.escalation_reason, 'unspecified')

    # ── appears in the existing Escalation tab/queue ─────────────────────────
    def test_appears_in_escalation_queue(self):
        self._post({
            'phone': '+13059995694', 'name': 'Jane Roe',
            'reason': 'human_handoff',
            'transcript': 'user: I want to speak to a person',
            'summary': 'Wants a human.',
        })

        # Staff opens the Escalation tab (the existing authenticated list endpoint).
        self._authenticate_staff('staff')

        res = self.client.get(reverse('intake:escalation-list'), secure=True)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.json()['results']
        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertEqual(item['channel'], 'voice')
        self.assertEqual(item['contact_name'], 'Jane Roe')
        self.assertEqual(item['escalation_reason'], 'human_handoff')
        self.assertEqual(item['summary'], 'Wants a human.')
        self.assertIn('Transcript:', item['raw_content'])
        self.assertFalse(item['is_read'])

    def test_unread_count_includes_voice_escalation(self):
        self._post({'phone': '+13059995694', 'reason': 'human_handoff'})
        self._authenticate_staff('staff2')
        res = self.client.get(reverse('intake:escalation-unread-count'), secure=True)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.json()['unread'], 1)
