from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from organizations.models import Branch, OrganizationMembership
from organizations.services import create_organization


User = get_user_model()


class ClientPortalOrganizationTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="portal-owner@example.test",
            email="portal-owner@example.test",
            password="test-only-correct-horse-battery-92!",
        )
        self.organization = create_organization(
            creator=self.owner,
            name="Portal Company",
            slug="portal-company",
            default_language="en",
        )
        self.client.force_authenticate(self.owner)
        self.header = {"HTTP_X_ORGANIZATION_ID": str(self.organization.id)}

    def test_me_exposes_only_active_memberships_for_safe_switching(self):
        second = create_organization(
            creator=self.owner,
            name="Second Company",
            slug="second-company",
        )
        third = create_organization(
            creator=self.owner,
            name="Inactive Company",
            slug="inactive-company",
        )
        OrganizationMembership.objects.filter(
            organization=third,
            user=self.owner,
        ).update(status="suspended")

        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {row["organization"] for row in response.json()["memberships"]},
            {str(self.organization.id), str(second.id)},
        )

    def test_branch_hours_are_validated_and_delete_archives(self):
        url = reverse("organizations:branch-list", args=[self.organization.id])
        invalid = self.client.post(
            url,
            {
                "name": "Invalid hours",
                "working_hours": {"mon": [{"open": "18:00", "close": "09:00"}]},
            },
            format="json",
            **self.header,
        )
        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)

        created = self.client.post(
            url,
            {
                "name": "City branch",
                "working_hours": {"mon": [{"open": "09:00", "close": "18:00"}]},
            },
            format="json",
            **self.header,
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.json())
        detail = reverse(
            "organizations:branch-detail",
            args=[self.organization.id, created.json()["id"]],
        )
        self.assertEqual(self.client.delete(detail, **self.header).status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Branch.objects.get(pk=created.json()["id"]).is_active)

    def test_onboarding_saves_and_resumes_then_completes_only_when_valid(self):
        url = reverse("organizations:organization-onboarding", args=[self.organization.id])
        first = self.client.patch(
            url,
            {
                "step": 1,
                "organization": {"name": "Portal Company", "industry": "clinic"},
                "profile": {"public_business_name": "Portal Clinic"},
            },
            format="json",
            **self.header,
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK, first.json())
        resumed = self.client.get(url, **self.header)
        self.assertEqual(resumed.json()["profile"]["onboarding_current_step"], 2)
        self.assertEqual(resumed.json()["profile"]["onboarding_completed_steps"], [1])

        incomplete = self.client.patch(
            url,
            {"complete": True},
            format="json",
            **self.header,
        )
        self.assertEqual(incomplete.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(incomplete.json()["code"], "onboarding_incomplete")

        complete = self.client.patch(
            url,
            {
                "step": 6,
                "complete": True,
                "assistant_context": {
                    "business_summary": "A neighborhood clinic.",
                    "business_description": "Primary care and diagnostic services.",
                    "products_services": "Consultations and diagnostics.",
                    "introduction": "I am the Portal Clinic assistant.",
                    "fallback_response": "A team member will help you.",
                },
                "branch": {"name": "Main branch"},
            },
            format="json",
            **self.header,
        )
        self.assertEqual(complete.status_code, status.HTTP_200_OK, complete.json())
        self.assertEqual(complete.json()["profile"]["onboarding_completion_percentage"], 100)
        self.assertIsNotNone(complete.json()["profile"]["onboarding_completed_at"])

    def test_invalid_step_does_not_partially_save_payload(self):
        url = reverse("organizations:organization-onboarding", args=[self.organization.id])
        response = self.client.patch(
            url,
            {"step": 7, "organization": {"name": "Must not persist"}},
            format="json",
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.name, "Portal Company")

    def test_manager_cannot_invite_a_higher_role(self):
        manager = User.objects.create_user(
            username="portal-manager", password="test-only-safe-manager-password"
        )
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=manager,
            role="manager",
            status="active",
        )
        self.client.force_authenticate(manager)
        response = self.client.post(
            reverse("organizations:invitation-list", args=[self.organization.id]),
            {"email": "admin@example.test", "role": "admin"},
            format="json",
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
