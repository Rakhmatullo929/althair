import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from channels.models import ChannelConnection, ChannelStatus, ChannelType
from intake.models import ChannelChoice, Contact, ContactSource, JobRecord
from organizations.models import (
    Organization,
    OrganizationMembership,
    OrganizationMembershipRole,
    OrganizationMembershipStatus,
    OrganizationProfile,
)


class Command(BaseCommand):
    help = "Create two deterministic, non-secret development workspaces."

    def add_arguments(self, parser):
        parser.add_argument("--allow-production", action="store_true")

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["allow_production"]:
            raise CommandError(
                "seed_dev_workspace refuses to run with DEBUG=false; use --allow-production explicitly."
            )

        User = get_user_model()
        owner, created = User.objects.get_or_create(
            username="demo-owner",
            defaults={"email": "demo-owner@example.test", "is_active": True},
        )
        if created:
            owner.set_unusable_password()
            owner.save(update_fields=["password"])

        organizations = []
        for slug, name in (("demo-alpha", "Demo Alpha"), ("demo-beta", "Demo Beta")):
            organization, _ = Organization.objects.get_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "status": "active",
                    "industry": "generic",
                    "default_language": "ru",
                    "timezone": "Asia/Tashkent",
                },
            )
            OrganizationProfile.objects.get_or_create(
                organization=organization,
                defaults={
                    "public_business_name": name,
                    "supported_languages": ["ru", "uz", "en"],
                },
            )
            OrganizationMembership.objects.update_or_create(
                organization=organization,
                user=owner,
                defaults={
                    "role": OrganizationMembershipRole.OWNER,
                    "status": OrganizationMembershipStatus.ACTIVE,
                    "joined_at": timezone.now(),
                },
            )
            contact, _ = Contact.objects.get_or_create(
                organization=organization,
                phone="+19995550123",
                defaults={"name": "Overlapping Demo Customer", "source": ContactSource.MANUAL},
            )
            JobRecord.objects.get_or_create(
                organization=organization,
                job_number="A-001",
                defaults={
                    "contact": contact,
                    "source_channel": ChannelChoice.MANUAL,
                    "service_type": "Demo request",
                },
            )
            organizations.append(organization)

        destinations = (
            ("DEV_SMS_DESTINATION_ORG_A", organizations[0], ChannelType.SMS, "twilio"),
            ("DEV_SMS_DESTINATION_ORG_B", organizations[1], ChannelType.SMS, "twilio"),
            ("DEV_VOICE_DESTINATION_ORG_A", organizations[0], ChannelType.VOICE, "twilio"),
            ("DEV_EMAIL_DESTINATION_ORG_A", organizations[0], ChannelType.GMAIL, "outlook"),
        )
        for env_name, organization, channel_type, provider in destinations:
            destination = os.environ.get(env_name, "").strip()
            if not destination:
                continue
            ChannelConnection.objects.update_or_create(
                organization=organization,
                provider=provider,
                type=channel_type,
                external_identifier=destination,
                defaults={
                    "display_name": f"{organization.name} {channel_type}",
                    "status": ChannelStatus.ACTIVE,
                },
            )

        self.stdout.write(self.style.SUCCESS("Development workspaces seeded."))
