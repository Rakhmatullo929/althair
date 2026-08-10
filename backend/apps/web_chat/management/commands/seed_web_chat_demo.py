from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ai_runtime.models import OrganizationAIRuntimeConfig, RuntimeProvider
from channels.models import ChannelConnection, ChannelStatus, ChannelType
from organizations.models import Organization, OrganizationMembership, OrganizationMembershipRole
from web_chat.models import InstallationAIMode, InstallationStatus, WebChatInstallation


class Command(BaseCommand):
    help = "Create the deterministic, local-only public Web Chat demo installation."

    def add_arguments(self, parser):
        parser.add_argument("--organization", default="mehr-clinic")

    def handle(self, *args, **options):
        organization = Organization.objects.filter(slug=options["organization"]).first()
        if not organization:
            raise CommandError("Run seed_client_portal before seed_web_chat_demo.")
        membership = (
            OrganizationMembership.objects.filter(
                organization=organization,
                status="active",
                role__in=[OrganizationMembershipRole.OWNER, OrganizationMembershipRole.ADMIN],
            )
            .order_by("created_at")
            .first()
        )
        if not membership:
            raise CommandError("The demo organization needs an active owner or admin.")

        public_key = settings.WEB_CHAT_DEMO_INSTALLATION_KEY
        connection, _ = ChannelConnection.objects.update_or_create(
            organization=organization,
            provider="public_web_chat",
            external_identifier=public_key,
            defaults={
                "type": ChannelType.WEBCHAT,
                "display_name": "Website chat",
                "status": ChannelStatus.ACTIVE,
            },
        )
        installation, _ = WebChatInstallation.objects.update_or_create(
            organization=organization,
            public_key=public_key,
            defaults={
                "channel_connection": connection,
                "display_name": "Mehr Clinic website",
                "status": InstallationStatus.ACTIVE,
                "allowed_origins": ["http://localhost:3001"],
                "collect_name": True,
                "collect_email": True,
                "require_consent": True,
                "default_language": "ru",
                "supported_languages": ["ru", "uz", "en"],
                "ai_mode": InstallationAIMode.AUTOPILOT,
                "created_by": membership,
                "updated_by": membership,
            },
        )
        installation.full_clean()
        installation.save()
        config, _ = OrganizationAIRuntimeConfig.objects.get_or_create(organization=organization)
        config.enabled = True
        config.provider = RuntimeProvider.FAKE
        config.allowed_channel_connections.add(connection)
        config.save(update_fields=["enabled", "provider", "updated_at"])
        self.stdout.write(self.style.SUCCESS(f"Web Chat demo ready: {public_key}"))
