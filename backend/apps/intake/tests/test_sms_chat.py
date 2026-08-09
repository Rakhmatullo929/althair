"""Tests for the SMS chat agent.

OpenAI is mocked through ``intake.services.sms_chat.utils.get_openai_client``;
Twilio is mocked at ``core.utils.send_message.send_message``.  The tests exercise
the agent end-to-end at the same boundary the webhook view uses
(``handle_inbound_sms``) so the integration between persistence, prompts,
tools, and the loop is covered.
"""

import json
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase

from intake.models import (
    ChannelChoice,
    Contact,
    ConversationMessage,
    ConversationThread,
    Interaction,
    JobEvent,
    JobRecord,
    JobStatus,
    MessageRole,
    ThreadState,
)
from intake.services.sms_chat import handle_inbound_sms
from channels.models import ChannelConnection, ChannelStatus, ChannelType
from organizations.models import Organization


def make_tool_call(name, arguments, call_id='call_test'):
    return SimpleNamespace(
        id=call_id,
        type='function',
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(arguments),
        ),
    )


def make_response(*, tool_calls=None, content=None):
    """Build a SimpleNamespace shaped like an OpenAI ChatCompletion response."""
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                content=content,
                tool_calls=tool_calls or [],
            ),
        )],
    )


class FakeOpenAI:
    """Stub OpenAI client whose chat.completions.create() returns canned responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                if not outer._responses:
                    raise AssertionError(
                        'FakeOpenAI ran out of canned responses; '
                        f'extra call had messages={kwargs.get("messages", [])[-2:]}'
                    )
                return outer._responses.pop(0)

        self.chat = SimpleNamespace(completions=_Completions())


class _AgentTestBase(TestCase):
    def setUp(self):
        super().setUp()
        self.organization = Organization.objects.create(name='SMS Test', slug='sms-test')
        self.connection = ChannelConnection.objects.create(
            organization=self.organization,
            type=ChannelType.SMS,
            provider='twilio',
            display_name='SMS Test',
            external_identifier='+17245768867',
            status=ChannelStatus.ACTIVE,
        )
        # Fire transaction.on_commit callbacks synchronously inside the
        # TestCase's wrapping transaction (which never actually commits).
        self.on_commit_patcher = mock.patch(
            'intake.services.sms_chat.tools.transaction.on_commit',
            side_effect=lambda fn: fn(),
        )
        self.on_commit_patcher.start()
        self.addCleanup(self.on_commit_patcher.stop)

        self.send_patcher = mock.patch(
            'intake.services.sms_chat.tools.send_message',
            return_value='SM_test',
        )
        self.send_message = self.send_patcher.start()
        self.addCleanup(self.send_patcher.stop)

        self.notify_patcher = mock.patch(
            'intake.services.sms_chat.jobs.fire_notifications',
            return_value=None,
        )
        self.notify_patcher.start()
        self.addCleanup(self.notify_patcher.stop)

        self.backfill_patcher = mock.patch(
            'intake.services.sms_chat.jobs.backfill_timeline',
            return_value=None,
        )
        self.backfill_patcher.start()
        self.addCleanup(self.backfill_patcher.stop)

    def _install_openai(self, responses):
        client = FakeOpenAI(responses)
        # The util uses lru_cache so we patch the function directly.
        patcher = mock.patch(
            'intake.services.sms_chat.agent.get_openai_client',
            return_value=client,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return client

    def _payload(self, body, *, from_phone='+15551234567', sid='SMtest1'):
        return {
            'MessageSid': sid,
            'From': from_phone,
            'To': '+17245768867',
            'Body': body,
        }

    def _handle_inbound_sms(self, payload):
        return handle_inbound_sms(
            payload,
            organization=self.organization,
            channel_connection=self.connection,
        )


class HandleInboundSmsTests(_AgentTestBase):
    def test_first_inbound_triggers_send_sms(self):
        self._install_openai([
            make_response(tool_calls=[
                make_tool_call('send_sms', {'text': 'Hi! What can I help with?'}),
            ]),
        ])

        interaction = self._handle_inbound_sms(self._payload('Hello'))

        self.assertIsInstance(interaction, Interaction)
        self.assertEqual(interaction.channel, ChannelChoice.SMS)

        self.send_message.assert_called_once()
        sent_text, sent_to = self.send_message.call_args.args[:2]
        self.assertEqual(sent_text, 'Hi! What can I help with?')
        self.assertEqual(sent_to, '+15551234567')

        thread = ConversationThread.objects.get()
        self.assertEqual(thread.state, ThreadState.OPEN)
        self.assertTrue(thread.is_active)

        roles = list(thread.messages.order_by('sequence').values_list('role', flat=True))
        self.assertEqual(roles, [MessageRole.USER, MessageRole.ASSISTANT, MessageRole.TOOL])

    def test_happy_path_creates_job_record(self):
        # 1st webhook: bot asks for service type
        # 2nd webhook: bot asks for site
        # 3rd webhook: bot sends readback
        # 4th webhook (user confirms): bot calls finish_and_submit, then send_sms ack
        self._install_openai([
            make_response(tool_calls=[
                make_tool_call('send_sms', {'text': 'What service do you need?'}),
            ]),
            make_response(tool_calls=[
                make_tool_call('send_sms', {'text': 'Which site?'}),
            ]),
            make_response(tool_calls=[
                make_tool_call('send_sms', {
                    'text': 'Got it: street sweeping at Maple Ridge. Confirm?',
                }),
            ]),
            # User confirms — agent first calls finish_and_submit
            make_response(tool_calls=[
                make_tool_call('finish_and_submit', {
                    'service_type': 'street sweeping',
                    'site': 'Maple Ridge',
                    'scope': 'Sweep curb line on Cedar Dr',
                    'community': 'Maple Ridge',
                    'builder': 'ABC Builders',
                    'scheduled_date': '2026-05-01',
                    'summary': 'Street Sweeping',
                }, call_id='call_finish'),
            ]),
            # Then a follow-up send_sms ack
            make_response(tool_calls=[
                make_tool_call('send_sms', {'text': 'Thanks, your request is in.'},
                               call_id='call_ack'),
            ]),
        ])

        self._handle_inbound_sms(self._payload('Hi', sid='SM1'))
        self._handle_inbound_sms(self._payload('street sweeping', sid='SM2'))
        self._handle_inbound_sms(self._payload('Maple Ridge', sid='SM3'))
        self._handle_inbound_sms(self._payload('yes thats right', sid='SM4'))

        self.assertEqual(JobRecord.objects.count(), 1)
        job = JobRecord.objects.get()
        self.assertEqual(job.service_type, 'street sweeping')
        self.assertEqual(job.site, 'Maple Ridge')
        self.assertEqual(job.community, 'Maple Ridge')
        self.assertEqual(job.builder, 'ABC Builders')
        self.assertEqual(job.status, JobStatus.NEW)
        self.assertEqual(job.scheduled_date.isoformat(), '2026-05-01')

        thread = ConversationThread.objects.get()
        self.assertEqual(thread.active_job_id, job.id)

        # Two outbound SMS in the last webhook (finish+ack), four total
        self.assertEqual(self.send_message.call_count, 4)

    def test_close_chat_closes_thread(self):
        self._install_openai([
            make_response(tool_calls=[
                make_tool_call('close_chat', {'reason': 'customer_done'},
                               call_id='call_close'),
            ]),
            make_response(tool_calls=[
                make_tool_call('send_sms', {'text': 'Thanks, talk soon!'},
                               call_id='call_bye'),
            ]),
        ])

        self._handle_inbound_sms(self._payload('nevermind, cancel'))

        thread = ConversationThread.objects.get()
        self.assertEqual(thread.state, ThreadState.CLOSED)
        self.assertFalse(thread.is_active)

        # close_chat creates an extra Interaction with classification_reason
        closed_interactions = Interaction.objects.filter(
            classification_reason__startswith='closed:',
        )
        self.assertEqual(closed_interactions.count(), 1)
        self.assertEqual(self.send_message.call_count, 0)

    def test_new_inbound_after_close_creates_fresh_thread(self):
        # First conversation: closed immediately
        self._install_openai([
            make_response(tool_calls=[
                make_tool_call('close_chat', {'reason': 'done'}, call_id='c1'),
            ]),
            make_response(tool_calls=[
                make_tool_call('send_sms', {'text': 'Bye'}, call_id='c2'),
            ]),
            # Second conversation: a new question
            make_response(tool_calls=[
                make_tool_call('send_sms', {'text': 'Hi again, how can I help?'},
                               call_id='c3'),
            ]),
        ])

        self._handle_inbound_sms(self._payload('cancel', sid='SMa'))
        self._handle_inbound_sms(self._payload('Hi, new question', sid='SMb'))

        threads = list(ConversationThread.objects.order_by('created_at'))
        self.assertEqual(len(threads), 2)
        self.assertEqual(threads[0].state, ThreadState.CLOSED)
        self.assertEqual(threads[1].state, ThreadState.OPEN)
        # The new thread starts fresh: only the user msg + assistant + tool result
        self.assertEqual(threads[1].messages.count(), 3)

    def test_past_jobs_appear_in_system_context(self):
        contact = Contact.objects.create(organization=self.organization, phone='+15551234567', source='sms')
        JobRecord.objects.create(organization=self.organization,
            source_channel=ChannelChoice.SMS, contact=contact,
            service_type='stone delivery', site='Maple Ridge',
            scope='20 tons stone', summary='Stone Delivery',
            status=JobStatus.COMPLETED,
        )

        client = self._install_openai([
            make_response(tool_calls=[
                make_tool_call('send_sms', {'text': 'Same site as last time?'}),
            ]),
        ])

        self._handle_inbound_sms(self._payload('Need more stone'))

        # Inspect the system messages handed to OpenAI
        first_call = client.calls[0]
        system_blocks = [m for m in first_call['messages'] if m['role'] == 'system']
        joined = '\n'.join(m['content'] or '' for m in system_blocks)
        self.assertIn('stone delivery', joined.lower())
        self.assertIn('Maple Ridge', joined)

    def test_max_tool_iterations_safety(self):
        # Agent infinitely loops calling finish_and_submit (non-terminal) — safety stop.
        loop_responses = [
            make_response(tool_calls=[
                make_tool_call(
                    'finish_and_submit',
                    {
                        'service_type': 'x', 'site': 'y', 'scope': 'z',
                    },
                    call_id=f'call_{i}',
                ),
            ])
            for i in range(10)
        ]
        self._install_openai(loop_responses)

        self._handle_inbound_sms(self._payload('weird'))

        # MAX_TOOL_ITERATIONS = 6 → at most 6 jobs created before we bail
        self.assertLessEqual(JobRecord.objects.count(), 6)
        # Thread should still exist and remain OPEN (we hit the cap, not close_chat)
        thread = ConversationThread.objects.get()
        self.assertEqual(thread.state, ThreadState.OPEN)

    def test_required_tool_choice_is_passed_to_openai(self):
        client = self._install_openai([
            make_response(tool_calls=[
                make_tool_call('send_sms', {'text': 'hey'}),
            ]),
        ])

        self._handle_inbound_sms(self._payload('hi'))

        kwargs = client.calls[0]
        self.assertEqual(kwargs['tool_choice'], 'required')
        self.assertEqual(kwargs['parallel_tool_calls'], False)
        tool_names = sorted(t['function']['name'] for t in kwargs['tools'])
        self.assertEqual(
            tool_names,
            ['close_chat', 'enrich_job', 'escalate_chat', 'finish_and_submit',
             'mmc_checker', 'send_sms'],
        )

    def test_escalate_chat_creates_escalation_interaction(self):
        self._install_openai([
            make_response(tool_calls=[
                make_tool_call(
                    'escalate_chat',
                    {
                        'ack_text': 'Got it, our team will reach out.',
                        'reason': 'customer requested manager',
                        'contact_name': 'Alex',
                    },
                    call_id='call_esc',
                ),
            ]),
        ])

        self._handle_inbound_sms(self._payload('I want to speak to a manager'))

        # Thread closed
        thread = ConversationThread.objects.get()
        self.assertEqual(thread.state, ThreadState.CLOSED)
        self.assertFalse(thread.is_active)

        # ESCALATION Interaction surfaces in the staff queue.
        escalation = Interaction.objects.filter(category='escalation').get()
        self.assertEqual(escalation.escalation_reason, 'customer requested manager')
        self.assertFalse(escalation.is_read)
        self.assertEqual(escalation.status, 'review_required')

        # The contact_name argument backfilled the Contact row.
        contact = Contact.objects.get(phone='+15551234567')
        self.assertEqual(contact.name, 'Alex')

        # Ack SMS was sent (via the on_commit shim in setUp).
        self.send_message.assert_called_once()
        self.assertIn('reach out', self.send_message.call_args.args[0])

    def test_escalate_chat_with_active_job_writes_jobevent(self):
        contact = Contact.objects.create(organization=self.organization,
            phone='+15551234567', name='Alex', source='sms',
        )
        job = JobRecord.objects.create(organization=self.organization,
            source_channel=ChannelChoice.SMS, contact=contact,
            service_type='street sweeping', site='Maple Ridge', scope='x',
            status=JobStatus.NEW,
        )
        ConversationThread.objects.create(organization=self.organization, channel_connection=self.connection,
            phone='+15551234567', contact=contact, active_job=job,
            state=ThreadState.OPEN, is_active=True,
        )

        self._install_openai([
            make_response(tool_calls=[
                make_tool_call(
                    'escalate_chat',
                    {
                        'ack_text': 'On it.',
                        'reason': 'fence_damage_complaint',
                        'contact_name': 'Alex',
                    },
                    call_id='call_esc2',
                ),
            ]),
        ])

        self._handle_inbound_sms(self._payload('your crew damaged my fence'))

        events = JobEvent.objects.filter(job=job, event_type='note_added')
        self.assertEqual(events.count(), 1)
        self.assertIn('ESCALATION', events.first().note)
        self.assertIn('fence_damage_complaint', events.first().note)

    def test_close_chat_with_farewell_text(self):
        self._install_openai([
            make_response(tool_calls=[
                make_tool_call(
                    'close_chat',
                    {
                        'farewell_text': 'No problem, take care!',
                        'reason': 'customer_cancelled',
                    },
                    call_id='call_close',
                ),
            ]),
        ])

        self._handle_inbound_sms(self._payload('actually nevermind'))

        thread = ConversationThread.objects.get()
        self.assertEqual(thread.state, ThreadState.CLOSED)
        self.send_message.assert_called_once()
        self.assertEqual(self.send_message.call_args.args[0], 'No problem, take care!')

    def test_enrich_job_appends_to_active_job(self):
        contact = Contact.objects.create(organization=self.organization, phone='+15551234567', source='sms')
        job = JobRecord.objects.create(organization=self.organization,
            source_channel=ChannelChoice.SMS, contact=contact,
            service_type='street sweeping', site='Maple Ridge',
            scope='Original scope: 400 ft of curb', quantity='400 ft',
            status=JobStatus.NEW,
        )
        ConversationThread.objects.create(organization=self.organization, channel_connection=self.connection,
            phone='+15551234567', contact=contact, active_job=job,
            state=ThreadState.OPEN, is_active=True,
        )

        self._install_openai([
            make_response(tool_calls=[
                make_tool_call(
                    'enrich_job',
                    {
                        'note': 'Customer added 5 tons of stone',
                        'scope_addition': 'Plus 5 tons of #57 stone for driveway',
                        'scheduled_date': '2026-05-08',
                        'urgency_notes': 'rush',
                    },
                    call_id='call_enrich',
                ),
            ]),
            make_response(tool_calls=[
                make_tool_call('send_sms', {'text': 'Got it, added.'}, call_id='call_ack'),
            ]),
        ])

        self._handle_inbound_sms(self._payload(
            'also add 5 tons of stone, rush please, by Friday'
        ))

        job.refresh_from_db()
        self.assertIn('Original scope', job.scope)
        self.assertIn('5 tons of #57 stone', job.scope)
        self.assertEqual(job.scheduled_date.isoformat(), '2026-05-08')
        self.assertEqual(job.urgency_notes, 'rush')

        events = JobEvent.objects.filter(job=job, event_type='note_added')
        self.assertEqual(events.count(), 1)
        self.assertIn('SMS update', events.first().note)

    def test_enrich_job_without_active_job_returns_error(self):
        # No active job on the thread → the tool should return an error
        # status (and the agent loop continues to send_sms ack).
        self._install_openai([
            make_response(tool_calls=[
                make_tool_call(
                    'enrich_job',
                    {'note': 'misuse — no active job exists'},
                    call_id='call_bad',
                ),
            ]),
            make_response(tool_calls=[
                make_tool_call(
                    'send_sms', {'text': 'Sorry, no active job to update.'},
                ),
            ]),
        ])

        self._handle_inbound_sms(self._payload('add to my job'))

        # The model received the error status and presumably handled it via
        # send_sms.  No JobRecord should have been created.
        self.assertEqual(JobRecord.objects.count(), 0)
        # Two assistant turns + two tool results.
        self.assertEqual(
            ConversationMessage.objects.filter(role=MessageRole.TOOL).count(), 2,
        )

    def test_duplicate_message_sid_returns_existing_interaction(self):
        self._install_openai([
            make_response(tool_calls=[
                make_tool_call('send_sms', {'text': 'hello'}),
            ]),
        ])

        first = self._handle_inbound_sms(self._payload('hi', sid='SMdup'))
        # Same MessageSid arrives again — the agent should NOT call OpenAI
        # a second time.
        second = self._handle_inbound_sms(self._payload('hi', sid='SMdup'))

        self.assertEqual(first.id, second.id)
        # Only one Interaction with this provider_id, only one outbound SMS.
        self.assertEqual(
            Interaction.objects.filter(provider_id='SMdup').count(), 1,
        )
        self.assertEqual(self.send_message.call_count, 1)

    def test_persisted_assistant_carries_tool_calls(self):
        self._install_openai([
            make_response(tool_calls=[
                make_tool_call('send_sms', {'text': 'hi'}, call_id='c_call_xyz'),
            ]),
        ])

        self._handle_inbound_sms(self._payload('hello'))

        assistant_msg = ConversationMessage.objects.get(role=MessageRole.ASSISTANT)
        self.assertEqual(len(assistant_msg.tool_calls), 1)
        tc = assistant_msg.tool_calls[0]
        self.assertEqual(tc['id'], 'c_call_xyz')
        self.assertEqual(tc['function']['name'], 'send_sms')

        tool_msg = ConversationMessage.objects.get(role=MessageRole.TOOL)
        self.assertEqual(tool_msg.tool_call_id, 'c_call_xyz')
        self.assertEqual(json.loads(tool_msg.content)['status'], 'sent')
