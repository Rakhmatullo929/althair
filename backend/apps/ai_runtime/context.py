from __future__ import annotations

from dataclasses import dataclass

from assistant_context.models import AssistantContextRevision
from crm.models import FollowUpTask, FollowUpTaskStatus, Lead, LeadStatus, MessageContentType
from organizations.models import Branch

from ai_runtime.models import ConversationSummary


RECENT_MESSAGE_LIMIT = 30
MAX_CONTEXT_CHARACTERS = 30000


class PublishedContextRequired(Exception):
    pass


@dataclass(frozen=True)
class RuntimeContext:
    revision: AssistantContextRevision
    payload: dict
    latest_message: str


def latest_published_revision(organization):
    revision = (
        AssistantContextRevision.objects.filter(organization=organization)
        .select_related("profile")
        .order_by("-version")
        .first()
    )
    if not revision:
        raise PublishedContextRequired("published_ai_context_required")
    return revision


def build_runtime_context(*, organization, conversation, allowed_tools):
    if conversation.organization_id != organization.id:
        raise Conversation.DoesNotExist
    revision = latest_published_revision(organization)
    profile = getattr(organization, "profile", None)
    branches = list(
        Branch.objects.filter(organization=organization, is_active=True)
        .values("id", "name", "address", "timezone", "working_hours")[:20]
    )
    identities = list(
        conversation.contact.identities.filter(organization=organization, is_verified=True)
        .values("type", "normalized_value")[:10]
    )
    messages = list(
        conversation.messages.filter(organization=organization, content_type=MessageContentType.TEXT)
        .order_by("-occurred_at")[:80]
    )
    recent = list(reversed(messages[:RECENT_MESSAGE_LIMIT]))
    summary = _rolling_summary(organization=organization, conversation=conversation, older=list(reversed(messages[RECENT_MESSAGE_LIMIT:])))
    active_lead = (
        Lead.objects.for_organization(organization)
        .filter(contact=conversation.contact, status=LeadStatus.OPEN)
        .select_related("stage")
        .first()
    )
    tasks = list(
        FollowUpTask.objects.for_organization(organization)
        .filter(status=FollowUpTaskStatus.OPEN, related_contact=conversation.contact)
        .values("id", "title", "due_at")[:10]
    )
    provider_context = {"subject": conversation.subject}
    if conversation.channel_type == "gmail":
        try:
            from gmail_integration.models import GmailMessageRecord

            gmail_record = GmailMessageRecord.objects.for_organization(organization).filter(
                message__conversation=conversation,
                message__direction="inbound",
            ).order_by("-message__occurred_at").first()
            if gmail_record:
                provider_context.update(
                    {
                        "participants": gmail_record.participants[:20],
                        "automated": gmail_record.is_automated,
                        "historical": gmail_record.is_historical,
                    }
                )
        except ImportError:
            pass
    payload = {
        "published_ai_context": {"version": revision.version, **revision.snapshot},
        "organization_public_profile": {
            "name": organization.name,
            "public_business_name": getattr(profile, "public_business_name", ""),
            "short_description": getattr(profile, "short_description", ""),
            "timezone": organization.timezone,
        },
        "active_branches": branches,
        "conversation": {
            "id": str(conversation.id),
            "channel_type": conversation.channel_type,
            "internal_test": conversation.channel_connection.provider == "internal_test",
            "provider_context": provider_context,
        },
        "contact": {
            "id": str(conversation.contact_id),
            "display_name": conversation.contact.display_name,
            "preferred_language": conversation.contact.preferred_language,
            "verified_identities": identities,
        },
        "rolling_summary": summary.body if summary else "",
        "recent_messages": [
            {"direction": item.direction, "sender_type": item.sender_type, "body": item.body[:4000], "occurred_at": item.occurred_at.isoformat()}
            for item in recent
        ],
        "active_lead": (
            {"id": str(active_lead.id), "title": active_lead.title, "stage": active_lead.stage.name}
            if active_lead else None
        ),
        "open_follow_up_tasks": [
            {"id": str(item["id"]), "title": item["title"], "due_at": item["due_at"].isoformat()}
            for item in tasks
        ],
        # Tool order is not semantically meaningful. Canonicalizing it keeps the
        # prompt hash stable between policy loading and provider schema loading.
        "allowed_tools": sorted(set(allowed_tools)),
    }
    latest = recent[-1].body if recent else ""
    return RuntimeContext(revision=revision, payload=payload, latest_message=latest)


def _rolling_summary(*, organization, conversation, older):
    if not older:
        return None
    start, end = older[0], older[-1]
    existing = ConversationSummary.objects.for_organization(organization).filter(
        conversation=conversation, start_message=start, end_message=end
    ).first()
    if existing:
        return existing
    excerpts = []
    for message in older[-12:]:
        label = "Customer" if message.direction == "inbound" else "Team"
        excerpts.append(f"{label}: {message.body[:140]}")
    body = "AI-generated extractive summary (verify against messages): " + " | ".join(excerpts)
    ConversationSummary.objects.for_organization(organization).filter(conversation=conversation).delete()
    return ConversationSummary.objects.create(
        organization=organization,
        conversation=conversation,
        start_message=start,
        end_message=end,
        body=body[:2000],
    )


# Circular import is intentionally delayed until all model modules are loaded.
from crm.models import Conversation  # noqa: E402
