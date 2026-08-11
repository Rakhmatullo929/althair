from __future__ import annotations

import re
import uuid

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from channels.models import ChannelConnection, ChannelStatus, ChannelType
from crm.models import (
    AssignmentState,
    AutomationState,
    Contact,
    ContactIdentity,
    ContactIdentityType,
    ContactStatus,
    ContactTag,
    Conversation,
    ConversationAIState,
    ConversationStatus,
    CrmActivity,
    FollowUpTask,
    Lead,
    LeadStatus,
    Message,
    MessageContentType,
    MessageDirection,
    MessageSenderType,
    MessageStatus,
    Pipeline,
    PipelineStage,
    PipelineStageType,
)


DEFAULT_STAGES = (
    ("New", "green", PipelineStageType.OPEN),
    ("Contacted", "blue", PipelineStageType.OPEN),
    ("Qualified", "violet", PipelineStageType.OPEN),
    ("Proposal", "amber", PipelineStageType.OPEN),
    ("Won", "emerald", PipelineStageType.WON),
    ("Lost", "slate", PipelineStageType.LOST),
)


class CrmConflict(Exception):
    pass


class ProviderUnavailable(Exception):
    pass


def normalize_identity(identity_type: str, raw_value: str) -> str:
    value = (raw_value or "").strip()
    if identity_type == ContactIdentityType.EMAIL:
        local, separator, domain = value.casefold().partition("@")
        return f"{local}{separator}{domain}" if separator else value.casefold()
    if identity_type == ContactIdentityType.PHONE:
        digits = re.sub(r"\D", "", value)
        if value.startswith("+"):
            return f"+{digits}"
        if len(digits) >= 9:
            return f"+{digits}"
        return digits
    return value.casefold()


def record_activity(
    *, organization, event_type: str, summary: str, actor_membership=None,
    contact=None, conversation=None, lead=None, task=None, metadata=None,
) -> CrmActivity:
    event = CrmActivity(
        organization=organization,
        event_type=event_type,
        summary=summary,
        actor_membership=actor_membership,
        contact_id=getattr(contact, "id", None),
        conversation_id=getattr(conversation, "id", None),
        lead_id=getattr(lead, "id", None),
        task_id=getattr(task, "id", None),
        metadata=metadata or {},
    )
    event.full_clean()
    event.save()
    return event


@transaction.atomic
def ensure_default_pipeline(organization) -> Pipeline:
    pipeline = Pipeline.objects.for_organization(organization).filter(is_default=True).first()
    if pipeline:
        return pipeline
    pipeline = Pipeline.objects.create(
        organization=organization,
        name="Sales",
        is_default=True,
        is_active=True,
    )
    for position, (name, color, stage_type) in enumerate(DEFAULT_STAGES, start=1):
        PipelineStage.objects.create(
            organization=organization,
            pipeline=pipeline,
            name=name,
            position=position,
            color_token=color,
            stage_type=stage_type,
        )
    return pipeline


@transaction.atomic
def create_contact(*, organization, membership, display_name: str, **fields) -> Contact:
    contact = Contact(
        organization=organization,
        display_name=display_name.strip(),
        created_by=membership,
        updated_by=membership,
        **fields,
    )
    contact.full_clean()
    contact.save()
    record_activity(
        organization=organization,
        actor_membership=membership,
        event_type="contact.created",
        summary="Contact created",
        contact=contact,
    )
    return contact


@transaction.atomic
def add_identity(*, organization, contact, identity_type: str, raw_value: str, channel_connection=None, **fields):
    normalized = normalize_identity(identity_type, raw_value)
    if not normalized:
        raise ValueError("Identity value is required.")
    if ContactIdentity.objects.for_organization(organization).filter(
        type=identity_type,
        normalized_value=normalized,
        channel_connection=channel_connection,
    ).exists():
        raise CrmConflict("This identity already belongs to a contact in the organization.")
    identity = ContactIdentity(
        organization=organization,
        contact=contact,
        type=identity_type,
        raw_value=raw_value.strip(),
        normalized_value=normalized,
        channel_connection=channel_connection,
        **fields,
    )
    identity.full_clean(validate_constraints=False)
    try:
        identity.save()
    except IntegrityError as exc:
        raise CrmConflict("This identity already belongs to a contact in the organization.") from exc
    record_activity(
        organization=organization,
        event_type="identity.added",
        summary=f"{identity_type} identity added",
        contact=contact,
    )
    return identity


def duplicate_suggestions(contact: Contact):
    values = contact.identities.values("type", "normalized_value")
    identity_query = None
    from django.db.models import Q

    for row in values:
        clause = Q(identities__type=row["type"], identities__normalized_value=row["normalized_value"])
        identity_query = clause if identity_query is None else identity_query | clause
    query = Contact.objects.for_organization(contact.organization).filter(status=ContactStatus.ACTIVE).exclude(pk=contact.pk)
    if identity_query is not None:
        query = query.filter(identity_query)
    elif contact.company_name:
        query = query.filter(company_name__iexact=contact.company_name)
    else:
        return query.none()
    return query.distinct()[:10]


@transaction.atomic
def merge_contacts(*, organization, source: Contact, target: Contact, membership):
    if source.pk == target.pk:
        raise CrmConflict("Source and surviving contact must be different.")
    locked = {
        item.pk: item
        for item in Contact.objects.select_for_update().for_organization(organization).filter(pk__in=[source.pk, target.pk])
    }
    if len(locked) != 2:
        raise Contact.DoesNotExist
    source, target = locked[source.pk], locked[target.pk]
    if source.merged_into_id or target.merged_into_id:
        raise CrmConflict("A merged contact cannot be merged again.")

    for identity in list(source.identities.select_for_update()):
        conflict = ContactIdentity.objects.for_organization(organization).filter(
            type=identity.type,
            normalized_value=identity.normalized_value,
            channel_connection_id=identity.channel_connection_id,
        ).exclude(pk=identity.pk).first()
        if conflict:
            identity.delete()
        else:
            identity.contact = target
            identity.save(update_fields=["contact", "updated_at"])
    for link in source.contact_tags.select_related("tag"):
        ContactTag.objects.get_or_create(
            organization=organization,
            contact=target,
            tag=link.tag,
        )
    source.notes.update(contact=target)
    source.conversations.update(contact=target)
    source.leads.update(contact=target)
    source.tasks.update(related_contact=target)
    source.status = ContactStatus.ARCHIVED
    source.merged_into = target
    source.updated_by = membership
    source.save(update_fields=["status", "merged_into", "updated_by", "updated_at"])
    target.updated_by = membership
    target.save(update_fields=["updated_by", "updated_at"])
    record_activity(
        organization=organization,
        actor_membership=membership,
        event_type="contact.merged",
        summary="Contact merged into surviving contact",
        contact=target,
        metadata={"source_contact_id": str(source.id), "surviving_contact_id": str(target.id)},
    )
    return target


def resolve_contact_identity(*, organization, channel_connection, identity_type, raw_value):
    normalized = normalize_identity(identity_type, raw_value)
    return ContactIdentity.objects.for_organization(organization).select_related("contact").filter(
        channel_connection=channel_connection,
        type=identity_type,
        normalized_value=normalized,
    ).first()


def open_or_find_conversation(*, organization, channel_connection, contact, external_thread_id):
    ai_state = ConversationAIState.OFF
    try:
        from ai_runtime.services import default_ai_state_for_connection

        ai_state = default_ai_state_for_connection(organization, channel_connection)
    except ImportError:
        # Keeps the CRM independently usable during a rolling migration.
        pass
    conversation, _ = Conversation.objects.get_or_create(
        organization=organization,
        channel_connection=channel_connection,
        external_thread_id=external_thread_id,
        defaults={
            "contact": contact,
            "channel_type": channel_connection.type,
            "status": ConversationStatus.OPEN,
            "ai_state": ai_state,
            "ai_state_updated_at": timezone.now(),
        },
    )
    return conversation


@transaction.atomic
def ingest_inbound_message(
    *, organization, channel_connection, identity_type, sender_value,
    sender_display_name, external_thread_id, provider_message_id, body,
    occurred_at=None, metadata=None, actor_membership=None, is_test=False,
    enqueue_ai=True,
):
    if channel_connection.organization_id != organization.id:
        raise ValueError("Channel connection belongs to another organization.")
    existing = Message.objects.for_organization(organization).filter(
        channel_connection=channel_connection,
        provider_message_id=provider_message_id,
    ).first()
    if existing:
        return existing, False
    identity = resolve_contact_identity(
        organization=organization,
        channel_connection=channel_connection,
        identity_type=identity_type,
        raw_value=sender_value,
    )
    if identity:
        contact = identity.contact
    else:
        contact = create_contact(
            organization=organization,
            membership=actor_membership,
            display_name=sender_display_name,
            preferred_language=organization.default_language,
            notes_summary="Simulated test contact" if is_test else "",
        )
        identity = add_identity(
            organization=organization,
            contact=contact,
            identity_type=identity_type,
            raw_value=sender_value,
            channel_connection=channel_connection,
            external_user_id=sender_value,
            metadata={"test_data": True} if is_test else {},
        )
    conversation = open_or_find_conversation(
        organization=organization,
        channel_connection=channel_connection,
        contact=contact,
        external_thread_id=external_thread_id,
    )
    at = occurred_at or timezone.now()
    message = Message(
        organization=organization,
        conversation=conversation,
        channel_connection=channel_connection,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.CUSTOMER,
        provider_message_id=provider_message_id,
        content_type=MessageContentType.TEXT,
        body=body,
        status=MessageStatus.RECEIVED,
        metadata={**(metadata or {}), **({"test_data": True} if is_test else {})},
        occurred_at=at,
    )
    message.full_clean()
    message.save()
    Conversation.objects.filter(pk=conversation.pk).update(
        unread_count=F("unread_count") + 1,
        last_message_at=at,
        last_inbound_at=at,
        status=ConversationStatus.OPEN,
        resolved_at=None,
    )
    record_activity(
        organization=organization,
        actor_membership=actor_membership,
        event_type="message.received",
        summary="Simulated inbound message received" if is_test else "Inbound message received",
        contact=contact,
        conversation=conversation,
        metadata={"test_data": is_test},
    )
    if enqueue_ai:
        transaction.on_commit(lambda: _enqueue_ai_inbound(message.id))
    conversation.refresh_from_db()
    return message, True


def _enqueue_ai_inbound(message_id):
    """Import lazily so CRM ingestion never depends on provider initialization."""
    try:
        from ai_runtime.tasks import evaluate_inbound_message

        evaluate_inbound_message.delay(str(message_id))
    except ImportError:
        return


def is_internal_test_connection(connection: ChannelConnection) -> bool:
    return connection.type == ChannelType.WEBCHAT and connection.provider == "internal_test"


@transaction.atomic
def send_outbound_message(
    *,
    organization,
    conversation,
    membership,
    body,
    client_message_id,
    human_agent=False,
    cc=None,
):
    if conversation.organization_id != organization.id or conversation.channel_connection.organization_id != organization.id:
        raise ValueError("Conversation belongs to another organization.")
    if conversation.channel_connection.type == ChannelType.INSTAGRAM:
        from instagram.services import send_instagram_message

        return send_instagram_message(
            conversation=conversation,
            body=body,
            client_message_id=client_message_id,
            membership=membership,
            human_agent=human_agent,
        )
    if conversation.channel_connection.type == ChannelType.TELEGRAM:
        from telegram.services import send_telegram_message

        return send_telegram_message(
            conversation=conversation,
            body=body,
            client_message_id=client_message_id,
            membership=membership,
        )
    if conversation.channel_connection.type == ChannelType.GMAIL:
        from gmail_integration.services import send_gmail_message

        return send_gmail_message(
            conversation=conversation,
            body=body,
            client_message_id=client_message_id,
            membership=membership,
            cc=cc,
        )
    is_test = settings.ENABLE_CRM_TEST_CHANNEL and is_internal_test_connection(conversation.channel_connection)
    is_public_web_chat = False
    try:
        from web_chat.services import can_send_public_web_chat

        is_public_web_chat = can_send_public_web_chat(conversation)
    except ImportError:
        pass
    if not is_test and not is_public_web_chat:
        raise ProviderUnavailable("Sending is unavailable until this provider is connected.")
    existing = Message.objects.for_organization(organization).filter(
        conversation=conversation,
        client_message_id=client_message_id,
    ).first()
    if existing:
        return existing, False
    at = timezone.now()
    message = Message(
        organization=organization,
        conversation=conversation,
        channel_connection=conversation.channel_connection,
        direction=MessageDirection.OUTBOUND,
        sender_type=MessageSenderType.AGENT,
        sender_membership=membership,
        client_message_id=client_message_id,
        content_type=MessageContentType.TEXT,
        body=body,
        status=MessageStatus.SENT,
        metadata={"test_data": is_test, "provider": conversation.channel_connection.provider},
        occurred_at=at,
    )
    message.full_clean()
    message.save()
    Conversation.objects.filter(pk=conversation.pk).update(last_message_at=at, last_outbound_at=at)
    Conversation.objects.filter(pk=conversation.pk).update(
        ai_state=ConversationAIState.PAUSED_BY_HUMAN,
        ai_state_updated_at=at,
    )
    try:
        from ai_runtime.services import supersede_active_runs

        supersede_active_runs(conversation=conversation, reason="human_reply")
    except ImportError:
        pass
    record_activity(
        organization=organization,
        actor_membership=membership,
        event_type="message.sent",
        summary="Manual test reply sent" if is_test else "Web Chat reply sent",
        contact=conversation.contact,
        conversation=conversation,
        metadata={"test_data": is_test},
    )
    if is_public_web_chat:
        from web_chat.services import publish_message_event

        transaction.on_commit(lambda: publish_message_event(message))
    return message, True


@transaction.atomic
def record_delivery_update(*, organization, channel_connection, message_id, status, error_code=""):
    """Apply a verified provider delivery update without retaining provider payloads."""
    if channel_connection.organization_id != organization.id:
        raise ValueError("Channel connection belongs to another organization.")
    if status not in {MessageStatus.SENT, MessageStatus.DELIVERED, MessageStatus.READ, MessageStatus.FAILED}:
        raise ValueError("Unsupported outbound delivery status.")
    safe_error = str(error_code or "").strip()
    if safe_error and (len(safe_error) > 80 or not re.fullmatch(r"[a-zA-Z0-9_.:-]+", safe_error)):
        raise ValueError("Delivery error_code must be a short non-secret machine code.")
    message = Message.objects.select_for_update().for_organization(organization).get(
        pk=message_id,
        channel_connection=channel_connection,
        direction=MessageDirection.OUTBOUND,
    )
    next_error = safe_error if status == MessageStatus.FAILED else ""
    if message.status == status and message.error_code == next_error:
        return message, False
    message.status = status
    message.error_code = next_error
    message.full_clean()
    message.save(update_fields=["status", "error_code", "updated_at"])
    record_activity(
        organization=organization,
        event_type=f"message.{status}",
        summary=f"Outbound message {status}",
        contact=message.conversation.contact,
        conversation=message.conversation,
        metadata={"status": status},
    )
    return message, True


@transaction.atomic
def add_system_message(*, conversation, membership, body, event_type):
    now = timezone.now()
    message = Message.objects.create(
        organization=conversation.organization,
        conversation=conversation,
        channel_connection=conversation.channel_connection,
        direction=MessageDirection.SYSTEM,
        sender_type=MessageSenderType.SYSTEM,
        sender_membership=membership,
        content_type=MessageContentType.EVENT,
        body=body,
        status=MessageStatus.DELIVERED,
        occurred_at=now,
    )
    Conversation.objects.filter(pk=conversation.pk).update(last_message_at=now)
    record_activity(
        organization=conversation.organization,
        actor_membership=membership,
        event_type=event_type,
        summary=body,
        contact=conversation.contact,
        conversation=conversation,
    )
    return message


@transaction.atomic
def add_internal_note(*, conversation, membership, body):
    now = timezone.now()
    message = Message(
        organization=conversation.organization,
        conversation=conversation,
        channel_connection=conversation.channel_connection,
        direction=MessageDirection.SYSTEM,
        sender_type=MessageSenderType.AGENT,
        sender_membership=membership,
        content_type=MessageContentType.NOTE,
        body=body,
        status=MessageStatus.DELIVERED,
        occurred_at=now,
    )
    message.full_clean()
    message.save()
    record_activity(
        organization=conversation.organization,
        actor_membership=membership,
        event_type="conversation.note_added",
        summary="Internal note added",
        contact=conversation.contact,
        conversation=conversation,
    )
    return message


@transaction.atomic
def create_test_conversation(*, organization, membership, display_name, identity_value, body):
    if not settings.ENABLE_CRM_TEST_CHANNEL:
        raise ProviderUnavailable("The development test channel is disabled.")
    connection, _ = ChannelConnection.objects.get_or_create(
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
    sequence = uuid.uuid4().hex
    message, _ = ingest_inbound_message(
        organization=organization,
        channel_connection=connection,
        identity_type=ContactIdentityType.WEB_CHAT,
        sender_value=identity_value or f"test-{sequence}",
        sender_display_name=display_name,
        external_thread_id=f"test-thread-{sequence}",
        provider_message_id=f"test-message-{sequence}",
        body=body,
        actor_membership=membership,
        is_test=True,
    )
    record_activity(
        organization=organization,
        actor_membership=membership,
        event_type="test_data.created",
        summary="Development test conversation created",
        contact=message.conversation.contact,
        conversation=message.conversation,
        metadata={"test_data": True},
    )
    return message.conversation


@transaction.atomic
def move_lead(*, lead, stage, membership):
    if stage.pipeline_id != lead.pipeline_id:
        raise ValueError("Stage does not belong to the lead pipeline.")
    old_stage = lead.stage
    lead.stage = stage
    lead.updated_by = membership
    now = timezone.now()
    if stage.stage_type == PipelineStageType.WON:
        lead.status = LeadStatus.WON
        lead.won_at = now
        lead.lost_at = None
        lead.lost_reason = ""
    elif stage.stage_type == PipelineStageType.LOST:
        lead.status = LeadStatus.LOST
        lead.lost_at = now
        lead.won_at = None
    else:
        lead.status = LeadStatus.OPEN
        lead.won_at = None
        lead.lost_at = None
        lead.lost_reason = ""
    lead.full_clean()
    lead.save()
    record_activity(
        organization=lead.organization,
        actor_membership=membership,
        event_type=(
            "lead.won" if stage.stage_type == PipelineStageType.WON
            else "lead.lost" if stage.stage_type == PipelineStageType.LOST
            else "lead.stage_changed"
        ),
        summary=(
            "Lead marked won" if stage.stage_type == PipelineStageType.WON
            else "Lead marked lost" if stage.stage_type == PipelineStageType.LOST
            else f"Lead moved from {old_stage.name} to {stage.name}"
        ),
        contact=lead.contact,
        conversation=lead.source_conversation,
        lead=lead,
        metadata={"from_stage": old_stage.name, "to_stage": stage.name, "status": lead.status},
    )
    if lead.source_conversation:
        add_system_message(
            conversation=lead.source_conversation,
            membership=membership,
            body=f"Lead moved to {stage.name}",
            event_type="conversation.lead_stage_changed",
        )
    return lead
