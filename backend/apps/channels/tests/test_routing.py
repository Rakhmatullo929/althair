import uuid
from types import SimpleNamespace
from unittest import mock

from django.urls import reverse
from rest_framework.test import APITestCase

from channels.models import ChannelConnection, ChannelStatus, ChannelType
from organizations.models import Organization


class DestinationRoutingTests(APITestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Routing', slug='routing')
        self.sms = self.connection(ChannelType.SMS, 'twilio', '+15550100001')
        self.voice = self.connection(ChannelType.VOICE, 'twilio', '+15550100002')
        self.email = self.connection(ChannelType.GMAIL, 'outlook', 'inbox@example.test')

    def connection(self, channel_type, provider, destination):
        return ChannelConnection.objects.create(
            organization=self.organization,
            type=channel_type,
            provider=provider,
            display_name=f'{channel_type} test',
            external_identifier=destination,
            status=ChannelStatus.ACTIVE,
        )

    @mock.patch('intake.views.sms.handle_inbound_sms')
    def test_sms_organization_comes_from_to_destination(self, handler):
        handler.return_value = SimpleNamespace(id=uuid.uuid4())
        response = self.client.post(
            reverse('intake:twilio-sms-webhook'),
            {'MessageSid': 'SM-routing', 'From': '+15550999999', 'To': self.sms.external_identifier, 'Body': 'hello'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(handler.call_args.kwargs['organization'], self.organization)
        self.assertEqual(handler.call_args.kwargs['channel_connection'], self.sms)

    @mock.patch('intake.views.voice.process_voice_webhook')
    def test_voice_organization_comes_from_to_destination(self, handler):
        handler.return_value = SimpleNamespace(id=uuid.uuid4())
        response = self.client.post(
            reverse('intake:twilio-voice-webhook'),
            {'CallSid': 'CA-routing', 'From': '+15550999999', 'To': self.voice.external_identifier},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(handler.call_args.kwargs['organization'], self.organization)
        self.assertEqual(handler.call_args.kwargs['channel_connection'], self.voice)

    @mock.patch('intake.views.email.process_email_webhook')
    def test_email_organization_comes_from_verified_destination(self, handler):
        handler.return_value = SimpleNamespace(id=uuid.uuid4())
        response = self.client.post(
            reverse('intake:outlook-email-webhook'),
            {
                'to_email': self.email.external_identifier,
                'from_email': 'customer@example.test',
                'subject': 'Request',
                'body': 'Please help',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(handler.call_args.kwargs['organization'], self.organization)
        self.assertEqual(handler.call_args.kwargs['channel_connection'], self.email)

    def test_unknown_destinations_fail_closed_without_handlers(self):
        with mock.patch('intake.views.sms.handle_inbound_sms') as sms_handler:
            response = self.client.post(
                reverse('intake:twilio-sms-webhook'),
                {'MessageSid': 'SM-no-route', 'From': '+15550999999', 'To': '+15550000000', 'Body': 'hello'},
            )
        self.assertEqual((response.status_code, response.json()['code']), (404, 'unknown_destination'))
        sms_handler.assert_not_called()
