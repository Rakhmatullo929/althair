"""Tests for the Team Lead CRUD API (route-calendar daily grouping)."""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from intake.models import TeamLead
from django.contrib.auth import get_user_model
from organizations.models import Organization, OrganizationMembership


class TeamLeadApiTests(APITestCase):

    def setUp(self):
        self.list_url = reverse('jobs:team-lead-list')
        self.organization = Organization.objects.create(name='Jobs Test', slug='jobs-test')
        user = get_user_model().objects.create_user(username='owner', password='pw12345!')
        OrganizationMembership.objects.create(
            organization=self.organization, user=user, role='owner', status='active',
        )
        self.client.force_authenticate(user=user)
        self.client.credentials(HTTP_X_ORGANIZATION_ID=str(self.organization.id))
        # Migration 0017 seeds 3 defaults, but the test DB starts empty of data
        # beyond migrations; ensure a known baseline.
        TeamLead.objects.all().delete()
        for i, name in enumerate(['Team Lead 1', 'Team Lead 2']):
            TeamLead.objects.create(organization=self.organization, name=name, position=i)

    def test_list(self):
        res = self.client.get(self.list_url, secure=True)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual([l['name'] for l in res.json()], ['Team Lead 1', 'Team Lead 2'])

    def test_create_appends_position(self):
        res = self.client.post(self.list_url, {'name': 'Mike Crew'}, format='json', secure=True)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.json()['name'], 'Mike Crew')
        self.assertEqual(res.json()['position'], 2)

    def test_create_rejects_blank_name(self):
        res = self.client.post(self.list_url, {'name': '   '}, format='json', secure=True)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rename(self):
        lead = TeamLead.objects.first()
        url = reverse('jobs:team-lead-detail', args=[lead.id])
        res = self.client.patch(url, {'name': 'North Crew A'}, format='json', secure=True)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        lead.refresh_from_db()
        self.assertEqual(lead.name, 'North Crew A')

    def test_delete(self):
        lead = TeamLead.objects.first()
        url = reverse('jobs:team-lead-detail', args=[lead.id])
        res = self.client.delete(url, secure=True)
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(TeamLead.objects.filter(pk=lead.id).exists())
