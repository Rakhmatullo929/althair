"""Regression tests for membership-based customer authorization."""

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from organizations.models import Organization, OrganizationMembership

User = get_user_model()


class RoleApiTests(APITestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Role Test', slug='role-test')
        self.owner = User.objects.create_user(
            username='owner', email='owner@example.test', password='pw12345!',
            role='operations', organization='wrong-legacy-value',
        )
        self.agent = User.objects.create_user(
            username='agent', email='agent@example.test', password='pw12345!',
            role='admin', organization='role-test',
        )
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.owner,
            role='owner',
            status='active',
        )
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.agent,
            role='agent',
            status='active',
        )
        self.header = {'HTTP_X_ORGANIZATION_ID': str(self.organization.id)}

    def test_auth_me_does_not_expose_deprecated_authorization_strings(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(reverse('users:auth-me'), secure=True)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn('role', response.json())
        self.assertNotIn('organization', response.json())
        self.assertNotIn('is_admin', response.json())

    def test_legacy_role_string_cannot_grant_team_management(self):
        self.client.force_authenticate(user=self.agent)
        response = self.client.get(reverse('users:user-list'), secure=True, **self.header)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_legacy_strings_cannot_replace_membership(self):
        outsider = User.objects.create_user(
            username='outsider', role='admin', organization='role-test', password='pw12345!',
        )
        self.client.force_authenticate(user=outsider)
        response = self.client.get(reverse('users:user-list'), secure=True, **self.header)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_list_only_organization_members(self):
        User.objects.create_user(username='unrelated', password='pw12345!')
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(reverse('users:user-list'), secure=True, **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.json()), 2)
        self.assertEqual({row['membership_role'] for row in response.json()}, {'owner', 'agent'})

    def test_owner_can_create_user_with_membership_role(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            reverse('users:user-list'),
            {
                'email': 'new@example.test',
                'first_name': 'New',
                'password': 'pw12345!',
                'membership_role': 'manager',
            },
            format='json',
            secure=True,
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = User.objects.get(email='new@example.test')
        self.assertTrue(OrganizationMembership.objects.filter(
            organization=self.organization, user=created, role='manager', status='active',
        ).exists())

    def test_owner_can_change_membership_role(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.patch(
            reverse('users:user-detail', args=[self.agent.id]),
            {'membership_role': 'manager'},
            format='json',
            secure=True,
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        membership = OrganizationMembership.objects.get(
            organization=self.organization, user=self.agent,
        )
        self.assertEqual(membership.role, 'manager')

    def test_last_owner_cannot_be_suspended(self):
        other_owner = User.objects.create_user(username='other-owner', password='pw12345!')
        membership = OrganizationMembership.objects.create(
            organization=self.organization, user=other_owner, role='owner', status='active',
        )
        self.client.force_authenticate(user=self.owner)
        first = self.client.delete(
            reverse('users:user-detail', args=[other_owner.id]), secure=True, **self.header,
        )
        self.assertEqual(first.status_code, status.HTTP_204_NO_CONTENT)
        membership.refresh_from_db()
        self.assertEqual(membership.status, 'suspended')

        response = self.client.patch(
            reverse('users:user-detail', args=[self.owner.id]),
            {'membership_status': 'suspended'},
            format='json',
            secure=True,
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
