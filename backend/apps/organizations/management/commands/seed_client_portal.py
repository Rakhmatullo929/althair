import os

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.management.base import CommandError
from django.core.management.base import BaseCommand
from django.utils import timezone

from assistant_context.models import OrganizationAssistantProfile
from assistant_context.services import publish_assistant_profile
from channels.models import ChannelConnection
from organizations.models import Branch, Organization, OrganizationMembership, OrganizationProfile


class Command(BaseCommand):
    help = "Create deterministic, non-production client portal demo and E2E data."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_client_portal is available only with DEBUG=true.")
        seed_password = os.environ.get("CLIENT_PORTAL_SEED_PASSWORD")
        if not seed_password:
            raise CommandError("Set CLIENT_PORTAL_SEED_PASSWORD for the development seed.")
        User = get_user_model()
        owner, _ = User.objects.get_or_create(
            username="owner@portal.test",
            defaults={"email": "owner@portal.test", "first_name": "Aziza", "last_name": "Karimova"},
        )
        owner.email = "owner@portal.test"
        owner.first_name = "Aziza"
        owner.last_name = "Karimova"
        owner.set_password(seed_password)
        owner.save()

        member, _ = User.objects.get_or_create(
            username="member@portal.test",
            defaults={"email": "member@portal.test", "first_name": "Timur", "last_name": "Saidov"},
        )
        member.email = "member@portal.test"
        member.first_name = "Timur"
        member.last_name = "Saidov"
        member.set_password(seed_password)
        member.save()

        primary, _ = Organization.objects.update_or_create(
            slug="mehr-clinic",
            defaults={
                "name": "Mehr Clinic",
                "status": "active",
                "industry": "clinic",
                "default_language": "ru",
                "timezone": "Asia/Tashkent",
            },
        )
        secondary, _ = Organization.objects.update_or_create(
            slug="atlas-academy",
            defaults={
                "name": "Atlas Academy",
                "status": "active",
                "industry": "education",
                "default_language": "uz",
                "timezone": "Asia/Tashkent",
            },
        )
        suspended, _ = Organization.objects.update_or_create(
            slug="paused-studio",
            defaults={
                "name": "Paused Studio",
                "status": "suspended",
                "industry": "beauty",
                "default_language": "en",
                "timezone": "Asia/Tashkent",
            },
        )
        for organization, role in ((primary, "owner"), (secondary, "admin"), (suspended, "owner")):
            OrganizationMembership.objects.update_or_create(
                organization=organization,
                user=owner,
                defaults={"role": role, "status": "active", "joined_at": timezone.now()},
            )
            OrganizationProfile.objects.update_or_create(
                organization=organization,
                defaults={
                    "public_business_name": organization.name,
                    "supported_languages": [organization.default_language],
                    "public_contact_information": {
                        "website": "https://example.test",
                        "phone": "+998 71 200 20 20",
                        "email": f"hello@{organization.slug}.test",
                    },
                    "onboarding_completion_percentage": 100 if organization == primary else 50,
                    "onboarding_current_step": 6 if organization == primary else 3,
                    "onboarding_completed_steps": [1, 2, 3, 4, 5, 6] if organization == primary else [1, 2, 3],
                    "onboarding_completed_at": timezone.now() if organization == primary else None,
                },
            )
        OrganizationMembership.objects.update_or_create(
            organization=primary,
            user=member,
            defaults={"role": "agent", "status": "active", "joined_at": timezone.now()},
        )

        Branch.objects.update_or_create(
            organization=primary,
            name="Chilonzor",
            defaults={
                "address": "Bunyodkor ko‘chasi 12, Toshkent",
                "phone": "+998 71 200 20 20",
                "email": "chilonzor@mehr-clinic.test",
                "timezone": "Asia/Tashkent",
                "working_hours": {
                    day: [{"open": "09:00", "close": "18:00"}]
                    for day in ("mon", "tue", "wed", "thu", "fri")
                },
                "is_active": True,
            },
        )
        Branch.objects.update_or_create(
            organization=secondary,
            name="Yunusobod Campus",
            defaults={"address": "Amir Temur shoh ko‘chasi 108", "timezone": "Asia/Tashkent", "is_active": True},
        )
        ChannelConnection.objects.update_or_create(
            organization=primary,
            provider="internal",
            type="webchat",
            external_identifier="mehr-clinic-web",
            defaults={"display_name": "Website chat", "status": "draft", "configuration": {"notes": "Awaiting provider activation"}},
        )
        ChannelConnection.objects.update_or_create(
            organization=primary,
            provider="twilio",
            type="sms",
            external_identifier="+998712002020",
            defaults={"display_name": "Clinic SMS record", "status": "disconnected", "configuration": {}},
        )

        assistant, _ = OrganizationAssistantProfile.objects.update_or_create(
            organization=primary,
            defaults={
                "assistant_name": "Mehr",
                "business_summary": "A neighborhood clinic focused on clear, respectful patient communication.",
                "business_description": "Mehr Clinic provides primary consultations and diagnostic services in Tashkent.",
                "target_customers": "Families and working adults in Tashkent.",
                "products_services": "Primary consultations and diagnostic services.",
                "service_area": "Tashkent",
                "supported_languages": ["ru", "uz", "en"],
                "default_language": "ru",
                "tone_of_voice": "Calm, concise, and respectful.",
                "introduction": "I am Mehr, the clinic's digital front-office assistant.",
                "escalation_instructions": "Hand off urgent or clinical questions to a human immediately.",
                "prohibited_topics": "Diagnosis and treatment advice.",
                "prohibited_actions": "Never prescribe medicine or claim a provider connection is active.",
                "fallback_response": "I cannot confirm that yet; a team member will help.",
                "additional_instructions": "Use plain language and never invent availability.",
                "updated_by": owner,
            },
        )
        if assistant.version == 0:
            publish_assistant_profile(profile=assistant, actor=owner)
        assistant.refresh_from_db()
        assistant.business_summary += " Draft review in progress."
        assistant.status = "draft"
        assistant.updated_by = owner
        assistant.save(update_fields=["business_summary", "status", "updated_by", "updated_at"])

        self.stdout.write(self.style.SUCCESS("Client portal seed is ready for owner@portal.test."))
