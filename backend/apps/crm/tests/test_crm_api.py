from datetime import timedelta
from io import StringIO
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from channels.models import ChannelConnection, ChannelStatus, ChannelType
from crm.models import (
    Contact,
    ContactIdentity,
    ContactIdentityType,
    Conversation,
    CrmActivity,
    FollowUpTask,
    Lead,
    Message,
    Pipeline,
    Tag,
)
from crm.services import (
    CrmConflict,
    add_identity,
    create_contact,
    ensure_default_pipeline,
    ingest_inbound_message,
    normalize_identity,
    record_delivery_update,
)
from organizations.models import OrganizationMembership
from organizations.services import create_organization


User = get_user_model()


@override_settings(ENABLE_CRM_TEST_CHANNEL=True)
class CrmApiTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="crm-owner", email="owner@crm.test", password="pw12345!")
        self.organization = create_organization(creator=self.owner, name="Alpha CRM", slug="alpha-crm")
        self.membership = OrganizationMembership.objects.get(organization=self.organization, user=self.owner)
        self.agent_user = User.objects.create_user(username="crm-agent", email="agent@crm.test", password="pw12345!")
        self.agent = OrganizationMembership.objects.create(
            organization=self.organization, user=self.agent_user, role="agent", status="active"
        )
        self.viewer_user = User.objects.create_user(username="crm-viewer", email="viewer@crm.test", password="pw12345!")
        self.viewer = OrganizationMembership.objects.create(
            organization=self.organization, user=self.viewer_user, role="viewer", status="active"
        )
        self.other_owner = User.objects.create_user(username="other-owner", password="pw12345!")
        self.other_org = create_organization(creator=self.other_owner, name="Other CRM", slug="other-crm")
        self.internal = ChannelConnection.objects.create(
            organization=self.organization,
            type=ChannelType.WEBCHAT,
            provider="internal_test",
            display_name="Test channel",
            external_identifier="alpha-test",
            status=ChannelStatus.ACTIVE,
            configuration={"test_data": True},
        )
        self.external = ChannelConnection.objects.create(
            organization=self.organization,
            type=ChannelType.SMS,
            provider="planned_sms",
            display_name="Planned SMS",
            external_identifier="alpha-sms",
            status=ChannelStatus.DRAFT,
        )
        self.contact = create_contact(
            organization=self.organization,
            membership=self.membership,
            display_name="Dilnoza Karimova",
            preferred_language="uz",
        )
        self.client.force_authenticate(self.owner)

    def header(self, organization=None):
        return {"HTTP_X_ORGANIZATION_ID": str((organization or self.organization).id)}

    def inbound(self, *, provider_id="provider-1", connection=None, contact_value="dilnoza-web"):
        return ingest_inbound_message(
            organization=self.organization,
            channel_connection=connection or self.internal,
            identity_type=ContactIdentityType.WEB_CHAT,
            sender_value=contact_value,
            sender_display_name="Dilnoza Karimova",
            external_thread_id=f"thread-{contact_value}",
            provider_message_id=provider_id,
            body="I need a consultation.",
            actor_membership=self.membership,
            is_test=True,
        )[0]

    def test_contact_creation_identity_normalization_and_plain_text_validation(self):
        response = self.client.post(
            reverse("crm:contact-list"),
            {
                "display_name": "Aziza Saidova",
                "preferred_language": "ru",
                "identities": [
                    {"type": "phone", "raw_value": "+998 (90) 123-45-67", "is_primary": True},
                    {"type": "email", "raw_value": " Aziza@Example.TEST "},
                ],
            },
            format="json",
            **self.header(),
        )
        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json()["identities"][0]["normalized_value"], "+998901234567")
        self.assertEqual(normalize_identity("email", " Aziza@Example.TEST "), "aziza@example.test")
        rejected = self.client.post(
            reverse("crm:contact-list"), {"display_name": "<b>Unsafe</b>"}, format="json", **self.header()
        )
        self.assertEqual(rejected.status_code, 400)

    def test_duplicate_identity_is_prevented_per_tenant_but_allowed_between_tenants(self):
        add_identity(
            organization=self.organization,
            contact=self.contact,
            identity_type="email",
            raw_value="same@example.test",
        )
        second = create_contact(
            organization=self.organization, membership=self.membership, display_name="Second"
        )
        with self.assertRaises(CrmConflict):
            add_identity(
                organization=self.organization,
                contact=second,
                identity_type="email",
                raw_value="SAME@example.test",
            )
        other_membership = OrganizationMembership.objects.get(organization=self.other_org, user=self.other_owner)
        other_contact = create_contact(
            organization=self.other_org, membership=other_membership, display_name="Other"
        )
        identity = add_identity(
            organization=self.other_org,
            contact=other_contact,
            identity_type="email",
            raw_value="same@example.test",
        )
        self.assertEqual(identity.organization, self.other_org)

    def test_manual_merge_moves_relationships_leaves_tombstone_and_audit(self):
        source = create_contact(
            organization=self.organization, membership=self.membership, display_name="Duplicate"
        )
        add_identity(
            organization=self.organization, contact=source, identity_type="phone", raw_value="+998901112233"
        )
        response = self.client.post(
            reverse("crm:contact-merge", args=[source.id]),
            {"surviving_contact_id": str(self.contact.id)},
            format="json",
            **self.header(),
        )
        self.assertEqual(response.status_code, 200, response.json())
        source.refresh_from_db()
        self.assertEqual(source.status, "archived")
        self.assertEqual(source.merged_into, self.contact)
        self.assertTrue(self.contact.identities.filter(normalized_value="+998901112233").exists())
        self.assertTrue(CrmActivity.objects.filter(organization=self.organization, event_type="contact.merged").exists())

    def test_cross_tenant_merge_and_contact_resource_are_404(self):
        other_membership = OrganizationMembership.objects.get(organization=self.other_org, user=self.other_owner)
        other_contact = create_contact(
            organization=self.other_org, membership=other_membership, display_name="Private"
        )
        detail = self.client.get(reverse("crm:contact-detail", args=[other_contact.id]), **self.header())
        merge = self.client.post(
            reverse("crm:contact-merge", args=[self.contact.id]),
            {"surviving_contact_id": str(other_contact.id)},
            format="json",
            **self.header(),
        )
        self.assertEqual(detail.status_code, 404)
        self.assertEqual(merge.status_code, 404)

    def test_provider_idempotency_unread_and_mark_read_are_transactional(self):
        first = self.inbound(provider_id="idem-1")
        second = self.inbound(provider_id="idem-1")
        self.assertEqual(first.id, second.id)
        conversation = first.conversation
        conversation.refresh_from_db()
        self.assertEqual(conversation.unread_count, 1)
        url = reverse("crm:conversation-mark-read", args=[conversation.id])
        self.assertEqual(self.client.post(url, {}, format="json", **self.header()).json()["unread_count"], 0)
        self.assertEqual(self.client.post(url, {}, format="json", **self.header()).json()["unread_count"], 0)
        conversation.refresh_from_db()
        self.assertEqual(conversation.unread_count, 0)

    def test_client_idempotency_and_internal_test_outbound(self):
        conversation = self.inbound().conversation
        url = reverse("crm:conversation-messages", args=[conversation.id])
        payload = {"body": "A team member will help.", "client_message_id": "client-retry-1"}
        first = self.client.post(url, payload, format="json", **self.header())
        second = self.client.post(url, payload, format="json", **self.header())
        self.assertEqual(first.status_code, 201, first.json())
        self.assertEqual(second.status_code, 200, second.json())
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(Message.objects.filter(client_message_id="client-retry-1").count(), 1)
        delivered, changed = record_delivery_update(
            organization=self.organization,
            channel_connection=self.internal,
            message_id=first.json()["id"],
            status="delivered",
        )
        repeated, repeated_change = record_delivery_update(
            organization=self.organization,
            channel_connection=self.internal,
            message_id=first.json()["id"],
            status="delivered",
        )
        self.assertEqual((delivered.status, changed, repeated.id, repeated_change), ("delivered", True, delivered.id, False))

    def test_external_channel_manual_send_is_honestly_disabled(self):
        conversation = self.inbound(provider_id="external-1", connection=self.external, contact_value="external-user").conversation
        response = self.client.post(
            reverse("crm:conversation-messages", args=[conversation.id]),
            {"body": "Cannot send", "client_message_id": "external-client-1"},
            format="json",
            **self.header(),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "provider_not_connected")

    def test_assignment_notes_resolve_and_reopen_are_audited(self):
        conversation = self.inbound().conversation
        assign = self.client.post(
            reverse("crm:conversation-assign", args=[conversation.id]),
            {"membership_id": str(self.agent.id)},
            format="json",
            **self.header(),
        )
        self.assertEqual(assign.status_code, 200, assign.json())
        note = self.client.post(
            reverse("crm:conversation-notes", args=[conversation.id]),
            {"body": "Call after 16:00."},
            format="json",
            **self.header(),
        )
        self.assertEqual(note.status_code, 201)
        self.assertEqual(note.json()["content_type"], "note")
        self.assertEqual(self.client.post(reverse("crm:conversation-resolve", args=[conversation.id]), {}, **self.header()).status_code, 200)
        self.assertEqual(self.client.post(reverse("crm:conversation-reopen", args=[conversation.id]), {}, **self.header()).status_code, 200)
        self.assertTrue(CrmActivity.objects.filter(conversation_id=conversation.id, event_type="conversation.reopened").exists())

    def test_agent_can_claim_self_but_cannot_assign_another_member(self):
        conversation = self.inbound().conversation
        self.client.force_authenticate(self.agent_user)
        claim = self.client.post(
            reverse("crm:conversation-assign", args=[conversation.id]),
            {"membership_id": str(self.agent.id)}, format="json", **self.header()
        )
        denied = self.client.post(
            reverse("crm:conversation-assign", args=[conversation.id]),
            {"membership_id": str(self.membership.id)}, format="json", **self.header()
        )
        self.assertEqual(claim.status_code, 200)
        self.assertEqual(denied.status_code, 403)

    def test_lead_duplicate_move_win_loss_and_reopen_are_real_transitions(self):
        conversation = self.inbound().conversation
        pipeline = ensure_default_pipeline(self.organization)
        first_stage = pipeline.stages.order_by("position").first()
        payload = {
            "contact": str(conversation.contact_id),
            "source_conversation": str(conversation.id),
            "pipeline": str(pipeline.id),
            "stage": str(first_stage.id),
            "title": "Consultation request",
        }
        created = self.client.post(reverse("crm:lead-list"), payload, format="json", **self.header())
        self.assertEqual(created.status_code, 201, created.json())
        duplicate = self.client.post(reverse("crm:lead-list"), payload, format="json", **self.header())
        self.assertEqual(duplicate.status_code, 409)
        lead_id = created.json()["id"]
        qualified = pipeline.stages.get(name="Qualified")
        moved = self.client.post(reverse("crm:lead-move", args=[lead_id]), {"stage_id": str(qualified.id)}, format="json", **self.header())
        self.assertEqual((moved.status_code, moved.json()["stage_name"]), (200, "Qualified"))
        won = self.client.post(reverse("crm:lead-win", args=[lead_id]), {}, format="json", **self.header())
        self.assertEqual(won.json()["status"], "won")
        reopened = self.client.post(reverse("crm:lead-move", args=[lead_id]), {"stage_id": str(first_stage.id)}, format="json", **self.header())
        self.assertEqual(reopened.json()["status"], "open")
        lost = self.client.post(reverse("crm:lead-lose", args=[lead_id]), {"lost_reason": "No longer needed"}, format="json", **self.header())
        self.assertEqual((lost.json()["status"], lost.json()["lost_reason"]), ("lost", "No longer needed"))

    def test_cross_tenant_stage_and_conversation_ids_return_404(self):
        other_membership = OrganizationMembership.objects.get(
            organization=self.other_org, user=self.other_owner
        )
        other_connection = ChannelConnection.objects.create(
            organization=self.other_org,
            type=ChannelType.WEBCHAT,
            provider="internal_test",
            display_name="Other private channel",
            external_identifier="other-private-test",
            status=ChannelStatus.ACTIVE,
            configuration={"test_data": True},
        )
        other_message, _ = ingest_inbound_message(
            organization=self.other_org,
            channel_connection=other_connection,
            identity_type=ContactIdentityType.WEB_CHAT,
            sender_value="other-private-customer",
            sender_display_name="Other private customer",
            external_thread_id="other-private-thread",
            provider_message_id="other-private-message",
            body="Private tenant content",
            actor_membership=other_membership,
            is_test=True,
        )
        self.assertEqual(
            self.client.get(
                reverse("crm:conversation-detail", args=[other_message.conversation_id]), **self.header()
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse("crm:conversation-messages", args=[other_message.conversation_id]), **self.header()
            ).status_code,
            404,
        )
        other_pipeline = ensure_default_pipeline(self.other_org)
        response = self.client.post(
            reverse("crm:lead-list"),
            {
                "contact": str(self.contact.id),
                "pipeline": str(other_pipeline.id),
                "stage": str(other_pipeline.stages.first().id),
                "title": "Denied",
            },
            format="json",
            **self.header(),
        )
        self.assertEqual(response.status_code, 404)

    def test_follow_up_task_create_complete_and_overview(self):
        task = self.client.post(
            reverse("crm:task-list"),
            {
                "title": "Call customer",
                "due_at": (timezone.now() - timedelta(hours=1)).isoformat(),
                "related_contact": str(self.contact.id),
                "assigned_membership": str(self.membership.id),
            },
            format="json",
            **self.header(),
        )
        self.assertEqual(task.status_code, 201, task.json())
        overview = self.client.get(reverse("crm:crm-overview"), **self.header())
        self.assertEqual(overview.json()["overdue_follow_ups"], 1)
        completed = self.client.patch(
            reverse("crm:task-detail", args=[task.json()["id"]]),
            {"status": "completed"}, format="json", **self.header()
        )
        self.assertEqual(completed.json()["status"], "completed")
        self.assertIsNotNone(completed.json()["completed_at"])

    def test_viewer_and_suspended_organization_are_read_only(self):
        self.client.force_authenticate(self.viewer_user)
        self.assertEqual(self.client.get(reverse("crm:contact-list"), **self.header()).status_code, 200)
        self.assertEqual(
            self.client.post(reverse("crm:contact-list"), {"display_name": "Denied"}, format="json", **self.header()).status_code,
            403,
        )
        self.organization.status = "suspended"
        self.organization.save(update_fields=["status"])
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(reverse("crm:contact-list"), **self.header()).status_code, 200)
        self.assertEqual(
            self.client.patch(reverse("crm:contact-detail", args=[self.contact.id]), {"display_name": "Denied"}, format="json", **self.header()).status_code,
            403,
        )

    def test_superuser_has_no_implicit_tenant_bypass(self):
        superuser = User.objects.create_superuser(username="crm-root", email="root@crm.test", password="pw12345!")
        self.client.force_authenticate(superuser)
        self.assertEqual(self.client.get(reverse("crm:contact-list"), **self.header()).status_code, 403)

    def test_dev_channel_owner_only_and_disabled_flag(self):
        response = self.client.post(
            reverse("crm:dev-test-conversation"),
            {"display_name": "Test person", "body": "Hello"},
            format="json",
            **self.header(),
        )
        self.assertEqual(response.status_code, 201, response.json())
        self.client.force_authenticate(self.agent_user)
        denied = self.client.post(
            reverse("crm:dev-test-conversation"), {"display_name": "Denied"}, format="json", **self.header()
        )
        self.assertEqual(denied.status_code, 403)
        with override_settings(ENABLE_CRM_TEST_CHANNEL=False):
            self.client.force_authenticate(self.owner)
            disabled = self.client.post(
                reverse("crm:dev-test-conversation"), {"display_name": "Disabled"}, format="json", **self.header()
            )
            self.assertEqual(disabled.status_code, 403)

    def test_major_lists_have_bounded_query_counts(self):
        conversations = []
        for index in range(5):
            conversations.append(
                self.inbound(provider_id=f"query-{index}", contact_value=f"query-user-{index}").conversation
            )
        pipeline = ensure_default_pipeline(self.organization)
        stage = pipeline.stages.filter(stage_type="open").first()
        for index, conversation in enumerate(conversations):
            lead = Lead.objects.create(
                organization=self.organization,
                contact=conversation.contact,
                source_conversation=conversation,
                source_channel_type=conversation.channel_type,
                pipeline=pipeline,
                stage=stage,
                title=f"Query lead {index}",
                created_by=self.membership,
                updated_by=self.membership,
            )
            FollowUpTask.objects.create(
                organization=self.organization,
                title=f"Query task {index}",
                due_at=timezone.now() + timedelta(days=1),
                related_contact=conversation.contact,
                related_lead=lead,
                related_conversation=conversation,
                created_by=self.membership,
            )
        for route_name in (
            "crm:contact-list",
            "crm:conversation-list",
            "crm:lead-list",
            "crm:task-list",
            "crm:crm-activity",
        ):
            with CaptureQueriesContext(connection) as captured:
                response = self.client.get(reverse(route_name), **self.header())
            self.assertEqual(response.status_code, 200)
            self.assertLessEqual(
                len(captured), 12, f"{route_name}: {[item['sql'] for item in captured]}"
            )

    def test_crud_filter_and_validation_routes_use_real_scoped_records(self):
        tag_response = self.client.post(
            reverse("crm:tag-list"), {"name": "Priority", "color_token": "amber"},
            format="json", **self.header(),
        )
        self.assertEqual(tag_response.status_code, 201, tag_response.json())
        tag_id = tag_response.json()["id"]
        self.assertEqual(self.client.get(reverse("crm:tag-list"), **self.header()).status_code, 200)

        self.contact.tags.add(
            Tag.objects.get(pk=tag_id), through_defaults={"organization": self.organization}
        )
        today = timezone.localdate().isoformat()
        filters = (
            f"?search=Dilnoza&status=active&language=uz&tag={tag_id}"
            f"&created_from={today}&created_to={today}"
        )
        self.assertEqual(
            self.client.get(reverse("crm:contact-list") + filters, **self.header()).json()["count"], 1
        )
        patched = self.client.patch(
            reverse("crm:contact-detail", args=[self.contact.id]),
            {"company_name": "Mehr Clinic"}, format="json", **self.header(),
        )
        self.assertEqual((patched.status_code, patched.json()["company_name"]), (200, "Mehr Clinic"))

        identity = self.client.post(
            reverse("crm:contact-identities", args=[self.contact.id]),
            {"type": "email", "raw_value": "dilnoza@example.test"},
            format="json", **self.header(),
        )
        self.assertEqual(identity.status_code, 201, identity.json())
        identities_url = reverse("crm:contact-identities", args=[self.contact.id])
        self.assertEqual(len(self.client.get(identities_url, **self.header()).json()), 1)
        identity_url = reverse("crm:contact-identity-detail", args=[self.contact.id, identity.json()["id"]])
        updated_identity = self.client.patch(
            identity_url, {"raw_value": "new@example.test", "is_primary": True},
            format="json", **self.header(),
        )
        self.assertEqual(updated_identity.json()["normalized_value"], "new@example.test")
        self.assertEqual(self.client.delete(identity_url, **self.header()).status_code, 204)

        notes_url = reverse("crm:contact-notes", args=[self.contact.id])
        self.assertEqual(
            self.client.post(notes_url, {"body": "Prefers an evening call."}, format="json", **self.header()).status_code,
            201,
        )
        self.assertEqual(self.client.get(notes_url, **self.header()).json()["count"], 1)

        conversation = self.inbound(provider_id="route-coverage").conversation
        self.contact.tags.add(
            Tag.objects.get(pk=tag_id), through_defaults={"organization": self.organization}
        )
        inbox_filters = (
            f"?unread=true&unassigned=true&status=open&priority=normal"
            f"&channel_type=web_chat&tag={tag_id}&from={today}&to={today}&search=consultation"
        )
        self.assertEqual(
            self.client.get(reverse("crm:conversation-list") + inbox_filters, **self.header()).status_code,
            200,
        )
        conversation_url = reverse("crm:conversation-detail", args=[conversation.id])
        self.assertEqual(self.client.get(conversation_url, **self.header()).status_code, 200)
        updated_conversation = self.client.patch(
            conversation_url,
            {"priority": "high", "automation_state": "ai_paused", "handoff_reason": "Needs a person"},
            format="json", **self.header(),
        )
        self.assertEqual(updated_conversation.json()["priority"], "high")
        messages_url = reverse("crm:conversation-messages", args=[conversation.id])
        self.assertEqual(self.client.get(messages_url, **self.header()).status_code, 200)
        self.assertEqual(self.client.post(messages_url, {}, format="json", **self.header()).status_code, 400)
        self.assertEqual(
            self.client.post(reverse("crm:conversation-notes", args=[conversation.id]), {}, format="json", **self.header()).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(reverse("crm:conversation-assign", args=[conversation.id]), {}, format="json", **self.header()).status_code,
            200,
        )

        self.assertEqual(self.client.get(reverse("crm:pipeline-list"), **self.header()).status_code, 200)
        pipeline_response = self.client.post(
            reverse("crm:pipeline-list"), {"name": "Renewals", "is_default": False, "is_active": True},
            format="json", **self.header(),
        )
        self.assertEqual(pipeline_response.status_code, 201, pipeline_response.json())
        pipeline_id = pipeline_response.json()["id"]
        pipeline_url = reverse("crm:pipeline-detail", args=[pipeline_id])
        self.assertEqual(self.client.get(pipeline_url, **self.header()).status_code, 200)
        self.assertEqual(
            self.client.patch(pipeline_url, {"name": "Renewal sales"}, format="json", **self.header()).json()["name"],
            "Renewal sales",
        )
        stages_url = reverse("crm:pipeline-stages", args=[pipeline_id])
        stage_response = self.client.post(
            stages_url,
            {"name": "Renewal due", "position": 10, "color_token": "blue", "stage_type": "open", "is_active": True},
            format="json", **self.header(),
        )
        self.assertEqual(stage_response.status_code, 201, stage_response.json())
        self.assertEqual(self.client.get(stages_url, **self.header()).status_code, 200)
        stage_url = reverse("crm:pipeline-stage-detail", args=[stage_response.json()["id"]])
        self.assertEqual(
            self.client.patch(stage_url, {"name": "Renewal contacted"}, format="json", **self.header()).json()["name"],
            "Renewal contacted",
        )

        default_pipeline = Pipeline.objects.get(organization=self.organization, is_default=True)
        default_stage = default_pipeline.stages.filter(stage_type="open").first()
        lead_response = self.client.post(
            reverse("crm:lead-list"),
            {
                "contact": str(conversation.contact_id), "source_conversation": str(conversation.id),
                "pipeline": str(default_pipeline.id), "stage": str(default_stage.id),
                "assigned_membership": str(self.membership.id), "title": "Route coverage lead",
                "next_follow_up_at": (timezone.now() + timedelta(days=1)).isoformat(),
            },
            format="json", **self.header(),
        )
        self.assertEqual(lead_response.status_code, 201, lead_response.json())
        lead_id = lead_response.json()["id"]
        lead_filters = "?" + urlencode({
            "pipeline": default_pipeline.id,
            "stage": default_stage.id,
            "assigned_member": self.membership.id,
            "source_channel": "web_chat",
            "status": "open",
            "follow_up_before": (timezone.now() + timedelta(days=2)).isoformat(),
            "search": "Route",
        })
        self.assertEqual(self.client.get(reverse("crm:lead-list") + lead_filters, **self.header()).status_code, 200)
        lead_url = reverse("crm:lead-detail", args=[lead_id])
        self.assertEqual(self.client.get(lead_url, **self.header()).status_code, 200)
        self.assertEqual(
            self.client.patch(lead_url, {"description": "Updated"}, format="json", **self.header()).json()["description"],
            "Updated",
        )
        self.assertEqual(
            self.client.post(
                reverse("crm:lead-move", args=[lead_id]),
                {"stage_id": stage_response.json()["id"]}, format="json", **self.header(),
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(reverse("crm:lead-lose", args=[lead_id]), {}, format="json", **self.header()).status_code,
            400,
        )

        task_response = self.client.post(
            reverse("crm:task-list"),
            {
                "title": "Route coverage follow-up", "due_at": (timezone.now() + timedelta(hours=4)).isoformat(),
                "assigned_membership": str(self.membership.id), "related_contact": str(conversation.contact_id),
                "related_lead": lead_id, "related_conversation": str(conversation.id),
            },
            format="json", **self.header(),
        )
        self.assertEqual(task_response.status_code, 201, task_response.json())
        task_id = task_response.json()["id"]
        due_before = (timezone.now() + timedelta(days=1)).isoformat()
        due_after = (timezone.now() - timedelta(days=1)).isoformat()
        task_filters = "?" + urlencode({
            "status": "open", "assigned_member": self.membership.id,
            "due_before": due_before, "due_after": due_after,
        })
        self.assertEqual(self.client.get(reverse("crm:task-list") + task_filters, **self.header()).status_code, 200)
        task_url = reverse("crm:task-detail", args=[task_id])
        self.assertEqual(self.client.get(task_url, **self.header()).status_code, 200)
        self.client.patch(task_url, {"status": "completed"}, format="json", **self.header())
        reopened_task = self.client.patch(task_url, {"status": "open"}, format="json", **self.header())
        self.assertEqual((reopened_task.status_code, reopened_task.json()["completed_at"]), (200, None))

        activity_filters = (
            f"?contact={conversation.contact_id}&conversation={conversation.id}&lead={lead_id}"
            f"&task={task_id}&event_type=task.updated"
        )
        self.assertEqual(self.client.get(reverse("crm:crm-activity") + activity_filters, **self.header()).status_code, 200)

    def test_seed_command_is_explicitly_gated_and_idempotent(self):
        portal_owner = User.objects.create_user(
            username="portal-seed-owner", email="owner@portal.test", password="pw12345!"
        )
        create_organization(creator=portal_owner, name="Mehr Clinic", slug="mehr-clinic")
        output = StringIO()
        with override_settings(DEBUG=True, ENABLE_CRM_TEST_CHANNEL=True):
            call_command("seed_crm", stdout=output)
            call_command("seed_crm", stdout=output)
        self.assertIn("CRM seed is ready", output.getvalue())
        self.assertEqual(
            Message.objects.filter(organization__slug="mehr-clinic", provider_message_id__startswith="crm-seed-message-").count(),
            3,
        )
