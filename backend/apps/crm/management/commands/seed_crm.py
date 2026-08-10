from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from channels.models import ChannelConnection, ChannelStatus, ChannelType
from crm.models import ContactIdentityType, FollowUpTask, Lead, LeadStatus
from crm.services import ensure_default_pipeline, ingest_inbound_message
from organizations.models import Organization, OrganizationMembership, OrganizationMembershipStatus


class Command(BaseCommand):
    help = "Create deterministic development-only CRM records without external providers."

    def handle(self, *args, **options):
        if not settings.DEBUG or not settings.ENABLE_CRM_TEST_CHANNEL:
            raise CommandError("seed_crm requires DEBUG=true and ENABLE_CRM_TEST_CHANNEL=true.")
        organization = Organization.objects.get(slug="mehr-clinic")
        membership = OrganizationMembership.objects.get(
            organization=organization,
            user__email="owner@portal.test",
            status=OrganizationMembershipStatus.ACTIVE,
        )
        connection, _ = ChannelConnection.objects.update_or_create(
            organization=organization,
            provider="internal_test",
            type=ChannelType.WEBCHAT,
            external_identifier=f"internal-test:{organization.id}",
            defaults={
                "display_name": "Development test channel",
                "status": ChannelStatus.ACTIVE,
                "configuration": {"test_data": True, "outbound_mode": "store_only"},
            },
        )
        inquiries = (
            ("Dilnoza Akramova", "test-dilnoza", "I would like to book a first consultation next week."),
            ("Bekzod Yunusov", "test-bekzod", "Could you explain which diagnostic services are available?"),
            ("Madina Ismoilova", "test-madina", "Please ask a team member to call me back today."),
        )
        conversations = []
        for index, (name, identity, body) in enumerate(inquiries, start=1):
            message, _ = ingest_inbound_message(
                organization=organization,
                channel_connection=connection,
                identity_type=ContactIdentityType.WEB_CHAT,
                sender_value=identity,
                sender_display_name=name,
                external_thread_id=f"crm-seed-thread-{index}",
                provider_message_id=f"crm-seed-message-{index}",
                body=body,
                actor_membership=membership,
                is_test=True,
            )
            conversations.append(message.conversation)
        pipeline = ensure_default_pipeline(organization)
        first_stage = pipeline.stages.order_by("position").first()
        lead, _ = Lead.objects.get_or_create(
            organization=organization,
            contact=conversations[0].contact,
            source_conversation=conversations[0],
            status=LeadStatus.OPEN,
            defaults={
                "source_channel_type": conversations[0].channel_type,
                "pipeline": pipeline,
                "stage": first_stage,
                "title": "Initial clinic consultation",
                "description": "Customer requested a first consultation.",
                "assigned_membership": membership,
                "next_follow_up_at": timezone.now() + timedelta(days=1),
                "created_by": membership,
                "updated_by": membership,
            },
        )
        FollowUpTask.objects.get_or_create(
            organization=organization,
            related_lead=lead,
            title="Confirm preferred consultation time",
            defaults={
                "due_at": timezone.now() + timedelta(days=1),
                "assigned_membership": membership,
                "related_contact": lead.contact,
                "related_conversation": lead.source_conversation,
                "created_by": membership,
            },
        )
        FollowUpTask.objects.get_or_create(
            organization=organization,
            related_contact=conversations[2].contact,
            title="Return customer call",
            defaults={
                "due_at": timezone.now() - timedelta(hours=2),
                "assigned_membership": membership,
                "related_conversation": conversations[2],
                "created_by": membership,
            },
        )
        self.stdout.write(self.style.SUCCESS("CRM seed is ready for owner@portal.test."))
