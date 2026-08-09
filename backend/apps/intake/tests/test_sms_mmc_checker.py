"""Tests for the SMS agent's `mmc_checker` tool.

The tool lets the SMS assistant look up known caller/site context (reusing the
same recall logic as the Voice Context Lookup API) so it stops re-asking
details that are already on file.
"""

from django.test import TestCase

from intake.models import ChannelChoice, Contact, ConversationThread, JobRecord
from intake.services.sms_chat import tools
from organizations.models import Organization


class SmsMmcCheckerTests(TestCase):

    def setUp(self):
        self.organization = Organization.objects.create(name='MMC Test', slug='mmc-test')
        self.contact = Contact.objects.create(
            organization=self.organization,
            name='John Smith', phone='+15551234567',
            metadata={'city_state': 'Pittsburgh, PA'},
        )
        self.thread = ConversationThread.objects.create(
            organization=self.organization,
            phone='+15551234567', contact=self.contact,
        )
        JobRecord.objects.create(
            organization=self.organization,
            source_channel=ChannelChoice.SMS, contact=self.contact,
            service_type='Install', site='189 East Avenue, Pittsburgh, PA 15201',
            community='Maple Ridge', builder='DR Horton', project='Lot 42',
        )

    def test_tool_registered(self):
        names = [t['function']['name'] for t in tools.ALL_TOOLS]
        self.assertIn('mmc_checker', names)

    def test_lookup_by_phone(self):
        res = tools.execute_mmc_checker(self.thread, self.contact, {'phone': '+15551234567'})
        self.assertEqual(res['status'], 'ok')
        self.assertTrue(res['found'])
        self.assertEqual(res['match_type'], 'phone')
        self.assertEqual(res['most_recent']['site'], '189 East Avenue, Pittsburgh, PA 15201')
        self.assertEqual(res['most_recent']['community'], 'Maple Ridge')
        self.assertEqual(res['most_recent']['builder'], 'DR Horton')
        self.assertEqual(res['most_recent']['lot_phase'], 'Lot 42')

    def test_lookup_by_community(self):
        res = tools.execute_mmc_checker(self.thread, self.contact, {'community': 'Maple Ridge'})
        self.assertEqual(res['status'], 'ok')
        self.assertTrue(res['found'])
        self.assertEqual(res['match_type'], 'community')

    def test_lookup_by_address(self):
        res = tools.execute_mmc_checker(self.thread, self.contact, {'address': '189 East Avenue'})
        self.assertTrue(res['found'])
        self.assertEqual(res['match_type'], 'address')

    def test_defaults_to_caller_phone_when_no_args(self):
        res = tools.execute_mmc_checker(self.thread, self.contact, {})
        self.assertTrue(res['found'])
        self.assertEqual(res['match_type'], 'phone')

    def test_no_match_returns_ok_not_found(self):
        # A phone with no contact on file → not found, empty locations.
        res = tools.execute_mmc_checker(self.thread, self.contact, {'phone': '+19990001111'})
        self.assertEqual(res['status'], 'ok')
        self.assertFalse(res['found'])
        self.assertEqual(res['known_locations'], [])
