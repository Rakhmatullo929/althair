import threading
from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from channels.models import ChannelConnection, ChannelStatus, ChannelType
from channels.serializers import ChannelConnectionSerializer
from channels.services import ChannelResolutionError, resolve_active_connection
from intake.models import (
    ChannelChoice,
    Contact,
    ConversationMessage,
    ConversationThread,
    JobRecord,
    KnowledgeBaseEntry,
    MessageRole,
    SystemPrompt,
    allocate_job_number,
)
from intake.services.sms_chat.prompts import build_system_messages
from organizations.models import (
    Branch,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
)
from organizations.policies import role_allows
from organizations.services import (
    accept_invitation,
    create_invitation,
    create_organization,
    hash_invitation_token,
    update_membership,
)

User = get_user_model()


class JobNumberConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Concurrent', slug='concurrent')

    def test_job_number_allocation_serializes_concurrent_postgresql_writers(self):
        if not connection.features.has_select_for_update:
            self.assertFalse(connection.features.has_select_for_update)
            return

        barrier = threading.Barrier(2)

        def allocate():
            close_old_connections()
            try:
                organization = Organization.objects.get(pk=self.organization.pk)
                barrier.wait(timeout=5)
                return allocate_job_number(organization)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            numbers = list(executor.map(lambda _index: allocate(), range(2)))

        self.assertCountEqual(numbers, ['A-001', 'A-002'])


class MultiTenantFoundationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='multi-user', password='pw12345!')
        self.org_a = create_organization(creator=self.user, name='Alpha', slug='alpha')
        self.org_b = Organization.objects.create(name='Beta', slug='beta')
        self.member_b = OrganizationMembership.objects.create(
            organization=self.org_b,
            user=self.user,
            role='viewer',
            status='active',
        )
        self.owner_a = OrganizationMembership.objects.get(
            organization=self.org_a, user=self.user,
        )
        self.client.force_authenticate(user=self.user)

    def header(self, organization=None):
        return {'HTTP_X_ORGANIZATION_ID': str((organization or self.org_a).id)}

    def test_organization_creation_is_atomic_owner_and_profile(self):
        creator = User.objects.create_user(username='creator', password='pw12345!')
        organization = create_organization(creator=creator, name='Created', slug='created')
        self.assertTrue(organization.profile.pk)
        self.assertTrue(OrganizationMembership.objects.filter(
            organization=organization, user=creator, role='owner', status='active',
        ).exists())

    def test_user_can_have_different_roles_in_multiple_organizations(self):
        self.assertEqual(self.owner_a.role, 'owner')
        self.assertEqual(self.member_b.role, 'viewer')

    def test_missing_and_invalid_organization_headers_have_stable_codes(self):
        url = reverse('channels:channel-connection-list')
        missing = self.client.get(url)
        invalid = self.client.get(url, HTTP_X_ORGANIZATION_ID='not-a-uuid')
        self.assertEqual((missing.status_code, missing.json()['code']), (400, 'missing_organization_header'))
        self.assertEqual((invalid.status_code, invalid.json()['code']), (400, 'invalid_organization_header'))

    def test_suspended_membership_cannot_access(self):
        self.owner_a.status = 'suspended'
        self.owner_a.save(update_fields=['status'])
        response = self.client.get(reverse('jobs:job-list'), **self.header())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_central_role_matrix(self):
        self.assertTrue(role_allows('viewer', 'read'))
        self.assertFalse(role_allows('viewer', 'operate'))
        self.assertTrue(role_allows('agent', 'operate'))
        self.assertFalse(role_allows('agent', 'manage_settings'))
        self.assertTrue(role_allows('manager', 'manage_settings'))
        self.assertTrue(role_allows('admin', 'manage_channels'))
        self.assertTrue(role_allows('owner', 'manage_ownership'))

    def test_viewer_can_read_but_cannot_mutate(self):
        list_url = reverse('organizations:branch-list', args=[self.org_b.id])
        read = self.client.get(list_url, **self.header(self.org_b))
        write = self.client.post(list_url, {'name': 'Denied'}, format='json', **self.header(self.org_b))
        self.assertEqual(read.status_code, status.HTTP_200_OK)
        self.assertEqual(write.status_code, status.HTTP_403_FORBIDDEN)

    def test_cross_tenant_branch_id_is_404_for_get_update_delete(self):
        branch = Branch.objects.create(organization=self.org_b, name='Secret branch')
        url = reverse('organizations:branch-detail', args=[self.org_a.id, branch.id])
        self.assertEqual(self.client.get(url, **self.header()).status_code, 404)
        self.assertEqual(self.client.patch(url, {'name': 'x'}, format='json', **self.header()).status_code, 404)
        self.assertEqual(self.client.delete(url, **self.header()).status_code, 404)

    def test_last_active_owner_cannot_be_demoted(self):
        with self.assertRaisesMessage(ValueError, 'last active owner'):
            update_membership(membership=self.owner_a, role='admin')

    def test_same_contact_identity_is_allowed_between_tenants(self):
        first = Contact.objects.create(
            organization=self.org_a, phone='+15550001111', email='same@example.test',
        )
        second = Contact.objects.create(
            organization=self.org_b, phone='+15550001111', email='same@example.test',
        )
        self.assertNotEqual(first.id, second.id)

    def test_job_number_and_prompt_key_uniqueness_are_tenant_scoped(self):
        JobRecord.objects.create(
            organization=self.org_a, job_number='A-777', source_channel=ChannelChoice.MANUAL,
        )
        JobRecord.objects.create(
            organization=self.org_b, job_number='A-777', source_channel=ChannelChoice.MANUAL,
        )
        SystemPrompt.objects.create(organization=self.org_a, key='shared', text='alpha')
        SystemPrompt.objects.create(organization=self.org_b, key='shared', text='beta')
        with self.assertRaises(IntegrityError), transaction.atomic():
            SystemPrompt.objects.create(organization=self.org_a, key='shared', text='duplicate')

    def test_job_number_allocation_is_independent(self):
        self.assertEqual(allocate_job_number(self.org_a), 'A-001')
        self.assertEqual(allocate_job_number(self.org_b), 'A-001')
        self.assertEqual(allocate_job_number(self.org_a), 'A-002')

    def test_cross_tenant_relationship_is_rejected(self):
        contact_b = Contact.objects.create(organization=self.org_b, phone='+15550002222')
        with self.assertRaises(ValidationError):
            JobRecord.objects.create(
                organization=self.org_a,
                contact=contact_b,
                source_channel=ChannelChoice.MANUAL,
            )

    def test_channel_credentials_are_write_only(self):
        connection = ChannelConnection(
            organization=self.org_a,
            type=ChannelType.SMS,
            provider='twilio',
            display_name='Private channel',
            external_identifier='+15550003333',
            status=ChannelStatus.ACTIVE,
        )
        connection.set_credentials({'token': 'test-only-dynamic-value'})
        connection.set_webhook_secret('test-only-webhook-value')
        connection.save()
        payload = ChannelConnectionSerializer(connection).data
        self.assertNotIn('credentials', payload)
        self.assertNotIn('encrypted_credentials', payload)
        self.assertNotIn('webhook_secret', payload)
        self.assertNotIn('webhook_secret_hash', payload)
        self.assertTrue(payload['has_credentials'])

    def test_destination_resolver_returns_exact_organization_and_fails_closed(self):
        connection = ChannelConnection.objects.create(
            organization=self.org_a,
            type=ChannelType.SMS,
            provider='twilio',
            display_name='Alpha SMS',
            external_identifier='+15550004444',
            status=ChannelStatus.ACTIVE,
        )
        resolved = resolve_active_connection(
            provider='twilio', channel_type='sms', destination='+15550004444',
        )
        self.assertEqual(resolved.connection, connection)
        self.assertEqual(resolved.organization, self.org_a)
        with self.assertRaises(ChannelResolutionError):
            resolve_active_connection(provider='twilio', channel_type='sms', destination='unknown')

    def test_prompt_kb_and_history_are_tenant_scoped(self):
        contact = Contact.objects.create(organization=self.org_a, phone='+15550005555')
        thread = ConversationThread.objects.create(
            organization=self.org_a, phone=contact.phone, contact=contact,
        )
        ConversationMessage.objects.create(
            organization=self.org_a, thread=thread, sequence=0, role=MessageRole.USER, content='alpha',
        )
        SystemPrompt.objects.create(organization=self.org_a, key='sms_runtime_rules', text='ALPHA_RULE')
        SystemPrompt.objects.create(organization=self.org_b, key='sms_runtime_rules', text='BETA_RULE')
        KnowledgeBaseEntry.objects.create(
            organization=self.org_a, topic='Alpha KB', question='q', answer='alpha answer',
        )
        KnowledgeBaseEntry.objects.create(
            organization=self.org_b, topic='Beta KB', question='q', answer='beta answer',
        )
        combined = '\n'.join(item['content'] for item in build_system_messages(thread, contact))
        self.assertIn('ALPHA_RULE', combined)
        self.assertIn('alpha answer', combined)
        self.assertNotIn('BETA_RULE', combined)
        self.assertNotIn('beta answer', combined)

    def test_invitation_persists_hash_only_and_accepts_once(self):
        invited = User.objects.create_user(
            username='invited', email='invited@example.test', password='pw12345!',
        )
        invitation, raw_token = create_invitation(
            organization=self.org_a,
            email=invited.email,
            role='agent',
            invited_by=self.user,
        )
        invitation.refresh_from_db()
        self.assertEqual(invitation.token_hash, hash_invitation_token(raw_token))
        self.assertNotEqual(invitation.token_hash, raw_token)
        membership = accept_invitation(raw_token=raw_token, user=invited)
        self.assertEqual((membership.organization, membership.role, membership.status), (self.org_a, 'agent', 'active'))
        with self.assertRaises(OrganizationInvitation.DoesNotExist):
            accept_invitation(raw_token=raw_token, user=invited)

    def test_suspended_organization_is_read_only(self):
        self.org_a.status = 'suspended'
        self.org_a.save(update_fields=['status'])
        url = reverse('organizations:organization-detail', args=[self.org_a.id])
        self.assertEqual(self.client.get(url, **self.header()).status_code, 200)
        self.assertEqual(
            self.client.patch(url, {'name': 'Denied'}, format='json', **self.header()).status_code,
            403,
        )
