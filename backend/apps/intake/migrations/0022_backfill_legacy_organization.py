import uuid

from django.conf import settings
from django.db import migrations


LEGACY_SLUG = "legacy-mmc"
TENANT_MODELS = (
    "Contact",
    "ConversationMessage",
    "ConversationThread",
    "Interaction",
    "InternalNote",
    "JobEvent",
    "JobNote",
    "JobRecord",
    "KnowledgeBaseEntry",
    "SystemPrompt",
    "TeamLead",
)


def backfill_legacy_organization(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    OrganizationProfile = apps.get_model("organizations", "OrganizationProfile")
    OrganizationMembership = apps.get_model("organizations", "OrganizationMembership")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))

    legacy, _ = Organization.objects.get_or_create(
        slug=LEGACY_SLUG,
        defaults={
            "name": "Legacy MMC Workspace",
            "status": "active",
            "industry": "other",
            "default_language": "en",
            "timezone": "Asia/Tashkent",
            "settings": {"legacy_vertical": "mmc"},
        },
    )
    OrganizationProfile.objects.get_or_create(
        organization=legacy,
        defaults={
            "public_business_name": "Legacy MMC Workspace",
            "supported_languages": ["en"],
        },
    )

    users = list(User.objects.order_by("date_joined", "pk"))
    owner = next((user for user in users if user.is_superuser), None)
    if owner is None:
        owner = next((user for user in users if getattr(user, "role", "") == "admin"), None)

    for user in users:
        if user == owner:
            role = "owner"
        elif getattr(user, "role", "") == "admin":
            role = "admin"
        else:
            # Legacy operations users receive the least-privilege operational role.
            role = "agent"
        OrganizationMembership.objects.update_or_create(
            organization=legacy,
            user=user,
            defaults={"role": role, "status": "active", "joined_at": user.date_joined},
        )

    for model_name in TENANT_MODELS:
        model = apps.get_model("intake", model_name)
        model.objects.filter(organization__isnull=True).update(organization=legacy)

    JobNumberCounter = apps.get_model("intake", "JobNumberCounter")
    for counter in JobNumberCounter.objects.filter(organization__isnull=True):
        counter.organization = legacy
        counter.save(update_fields=["organization"])
    for counter in JobNumberCounter.objects.filter(id__isnull=True):
        counter.id = uuid.uuid4()
        counter.save(update_fields=["id"])


class Migration(migrations.Migration):
    dependencies = [
        ("organizations", "0001_initial"),
        ("users", "0003_add_must_change_password"),
        ("intake", "0021_contact_organization_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_legacy_organization, migrations.RunPython.noop),
    ]
