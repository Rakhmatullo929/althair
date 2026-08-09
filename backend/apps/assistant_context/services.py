from django.db import transaction
from django.utils import timezone

from assistant_context.models import AssistantContextRevision, OrganizationAssistantProfile
from assistant_context.serializers import CONTEXT_FIELDS


def assistant_snapshot(profile: OrganizationAssistantProfile) -> dict:
    return {field: getattr(profile, field) for field in CONTEXT_FIELDS}


@transaction.atomic
def publish_assistant_profile(*, profile, actor):
    profile = OrganizationAssistantProfile.objects.select_for_update().get(pk=profile.pk)
    profile.full_clean()
    required = {
        "assistant_name": profile.assistant_name,
        "business_summary": profile.business_summary,
        "business_description": profile.business_description,
        "products_services": profile.products_services,
        "introduction": profile.introduction,
        "fallback_response": profile.fallback_response,
    }
    missing = [name for name, value in required.items() if not str(value).strip()]
    if missing:
        raise ValueError(",".join(missing))
    version = profile.version + 1
    snapshot = assistant_snapshot(profile)
    revision = AssistantContextRevision.objects.create(
        organization=profile.organization,
        profile=profile,
        version=version,
        snapshot=snapshot,
        published_by=actor,
    )
    profile.version = version
    profile.status = "published"
    profile.published_snapshot = snapshot
    profile.published_at = revision.published_at
    profile.updated_by = actor
    profile.save(
        update_fields=[
            "version", "status", "published_snapshot", "published_at", "updated_by", "updated_at",
        ]
    )
    return profile, revision
