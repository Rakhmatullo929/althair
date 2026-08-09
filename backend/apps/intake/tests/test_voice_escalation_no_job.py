"""A voice call flagged as an escalation must NOT create a Job.

PM bug: escalation calls were producing empty Job cards.  The job is created by
the post-call voice webhook (process_voice_webhook); these tests verify it is
skipped for escalations and still works for normal job requests.
"""

from django.test import TestCase

from intake.models import (
    ChannelChoice,
    Interaction,
    InteractionCategory,
    JobRecord,
)
from intake.services.escalation import create_escalation
from intake.services.voice import process_voice_webhook
from channels.models import ChannelConnection, ChannelStatus, ChannelType
from organizations.models import Organization


class VoiceEscalationNoJobTests(TestCase):

    def setUp(self):
        self.organization = Organization.objects.create(name='Voice Test', slug='voice-test')
        self.connection = ChannelConnection.objects.create(
            organization=self.organization,
            type=ChannelType.VOICE,
            provider='twilio',
            display_name='Voice Test',
            external_identifier='+17245768867',
            status=ChannelStatus.ACTIVE,
        )

    def _process(self, payload):
        return process_voice_webhook(
            payload,
            organization=self.organization,
            channel_connection=self.connection,
        )

    def _payload(self, **extra):
        base = {
            'CallSid': 'CA-esc-1', 'From': '+13059995694',
            'To': '+17245768867', 'CallStatus': 'completed',
        }
        base.update(extra)
        return base

    def test_is_escalation_flag_creates_no_job(self):
        # Structured fields present, but is_escalation=True → no job.
        interaction = self._process(self._payload(
            is_escalation=True, service_type='Install', site='123 Test Rd',
            scope='I want to talk to a manager',
        ))
        self.assertEqual(interaction.category, InteractionCategory.ESCALATION)
        self.assertEqual(JobRecord.objects.count(), 0)

    def test_category_escalation_creates_no_job(self):
        interaction = self._process(self._payload(
            CallSid='CA-esc-2', category='escalation',
            service_type='Install', site='123 Test Rd',
        ))
        self.assertEqual(interaction.category, InteractionCategory.ESCALATION)
        self.assertEqual(JobRecord.objects.count(), 0)

    def test_existing_escalation_for_callsid_skips_job(self):
        # The agent already escalated this call via the escalation endpoint.
        create_escalation(
            organization=self.organization,
            channel_connection=self.connection,
            channel=ChannelChoice.VOICE, phone='+13059995694',
            reason='human_handoff', call_sid='CA-auto-1',
        )
        self._process(self._payload(
            CallSid='CA-auto-1', service_type='Install', site='123 Test Rd', scope='x',
        ))
        self.assertEqual(JobRecord.objects.count(), 0)

    def test_normal_job_request_still_creates_job(self):
        # Regression: a normal job webhook (no escalation signal) still creates a job.
        interaction = self._process(self._payload(
            CallSid='CA-job-1', service_type='Street sweeping', builder='Lennar',
            site='200 West St', scope='sweep the lot',
        ))
        self.assertEqual(interaction.category, InteractionCategory.JOB_REQUEST_INTAKE)
        self.assertEqual(JobRecord.objects.count(), 1)
