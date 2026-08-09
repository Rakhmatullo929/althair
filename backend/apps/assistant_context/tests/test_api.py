from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from assistant_context.models import AssistantContextRevision
from organizations.models import Organization, OrganizationMembership
from organizations.services import create_organization

User = get_user_model()


COMPLETE_CONTEXT = {
    "assistant_name": "Mehr",
    "business_summary": "A customer-focused neighborhood clinic.",
    "business_description": "The clinic provides primary consultations and diagnostics.",
    "target_customers": "Families in Tashkent.",
    "products_services": "Consultations and diagnostic services.",
    "service_area": "Tashkent",
    "supported_languages": ["ru", "uz", "en"],
    "default_language": "uz",
    "tone_of_voice": "Calm, concise, and respectful.",
    "introduction": "I am Mehr, the clinic's digital front-office assistant.",
    "escalation_instructions": "Hand off urgent medical questions immediately.",
    "prohibited_topics": "Diagnosis and treatment advice.",
    "prohibited_actions": "Never confirm a booking or prescribe medicine.",
    "fallback_response": "I cannot confirm that yet; a team member will help.",
    "additional_instructions": "Use plain language.",
}


class AssistantContextApiTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="context-owner@example.test",
            email="context-owner@example.test",
            password="test-only-correct-horse-battery-92!",
        )
        self.organization = create_organization(
            creator=self.owner,
            name="Context Co",
            slug="context-co",
        )
        self.other = Organization.objects.create(name="Other", slug="context-other")
        self.client.force_authenticate(self.owner)
        self.header = {"HTTP_X_ORGANIZATION_ID": str(self.organization.id)}

    def test_draft_publish_versioning_and_published_snapshot_preservation(self):
        detail_url = reverse("assistant_context:detail")
        publish_url = reverse("assistant_context:publish")
        draft = self.client.patch(detail_url, COMPLETE_CONTEXT, format="json", **self.header)
        self.assertEqual(draft.status_code, status.HTTP_200_OK, draft.json())
        published = self.client.post(publish_url, {}, format="json", **self.header)
        self.assertEqual(published.status_code, status.HTTP_200_OK, published.json())
        self.assertEqual(published.json()["profile"]["version"], 1)
        self.assertEqual(AssistantContextRevision.objects.count(), 1)
        original_snapshot = published.json()["profile"]["published_snapshot"]

        edited = self.client.patch(
            detail_url,
            {"business_summary": "A changed draft."},
            format="json",
            **self.header,
        )
        self.assertEqual(edited.json()["status"], "draft")
        self.assertEqual(edited.json()["published_snapshot"], original_snapshot)
        republished = self.client.post(publish_url, {}, format="json", **self.header)
        self.assertEqual(republished.json()["profile"]["version"], 2)
        self.assertEqual(AssistantContextRevision.objects.count(), 2)

    def test_viewer_is_read_only_and_suspended_org_is_read_only(self):
        viewer = User.objects.create_user(username="context-viewer", password="pw12345!")
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=viewer,
            role="viewer",
            status="active",
        )
        self.client.force_authenticate(viewer)
        self.assertEqual(
            self.client.get(reverse("assistant_context:detail"), **self.header).status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            self.client.patch(
                reverse("assistant_context:detail"),
                {"assistant_name": "Denied"},
                format="json",
                **self.header,
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.organization.status = "suspended"
        self.organization.save(update_fields=["status"])
        before_updated_at = self.organization.assistant_profile.updated_at
        self.client.force_authenticate(self.owner)
        read = self.client.get(reverse("assistant_context:detail"), **self.header)
        self.assertEqual(read.status_code, status.HTTP_200_OK)
        self.organization.assistant_profile.refresh_from_db()
        self.assertEqual(self.organization.assistant_profile.updated_at, before_updated_at)
        self.assertEqual(
            self.client.patch(
                reverse("assistant_context:detail"),
                {"assistant_name": "Denied"},
                format="json",
                **self.header,
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_cross_tenant_header_cannot_read_and_superuser_has_no_bypass(self):
        response = self.client.get(
            reverse("assistant_context:detail"),
            HTTP_X_ORGANIZATION_ID=str(self.other.id),
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        superuser = User.objects.create_superuser(
            username="root@example.test",
            email="root@example.test",
            password="test-only-correct-horse-battery-92!",
        )
        self.client.force_authenticate(superuser)
        bypass = self.client.get(reverse("assistant_context:detail"), **self.header)
        self.assertEqual(bypass.status_code, status.HTTP_403_FORBIDDEN)

    def test_html_is_rejected(self):
        response = self.client.patch(
            reverse("assistant_context:detail"),
            {**COMPLETE_CONTEXT, "introduction": "<script>alert(1)</script>"},
            format="json",
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
