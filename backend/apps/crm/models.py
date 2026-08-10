from __future__ import annotations

import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from channels.models import ChannelConnection
from organizations.models import OrganizationMembership, OrganizationOwnedModel


HTML_PATTERN = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")
SECRET_KEY_PARTS = ("authorization", "cookie", "credential", "password", "secret", "token")


def validate_plain_text(value: str) -> None:
    if value and HTML_PATTERN.search(value):
        raise ValidationError(_("HTML is not allowed. Use plain text only."))


def validate_safe_metadata(value: dict) -> None:
    if not isinstance(value, dict):
        raise ValidationError(_("Metadata must be a JSON object."))

    def inspect(item, path="metadata"):
        if isinstance(item, dict):
            for key, child in item.items():
                lowered = str(key).lower()
                if any(part in lowered for part in SECRET_KEY_PARTS):
                    raise ValidationError(_("Sensitive keys are not allowed in metadata."))
                inspect(child, f"{path}.{key}")
        elif isinstance(item, list):
            for child in item:
                inspect(child, path)
        elif isinstance(item, str) and len(item) > 2000:
            raise ValidationError(_("Metadata text values are too long."))

    inspect(value)


class ContactStatus(models.TextChoices):
    ACTIVE = "active", _("Active")
    ARCHIVED = "archived", _("Archived")


class Contact(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    display_name = models.CharField(max_length=200, validators=[validate_plain_text])
    first_name = models.CharField(max_length=100, blank=True, validators=[validate_plain_text])
    last_name = models.CharField(max_length=100, blank=True, validators=[validate_plain_text])
    company_name = models.CharField(max_length=200, blank=True, validators=[validate_plain_text])
    preferred_language = models.CharField(
        max_length=2,
        choices=[("ru", "RU"), ("uz", "UZ"), ("en", "EN")],
        default="ru",
    )
    timezone = models.CharField(max_length=64, default="Asia/Tashkent")
    notes_summary = models.TextField(max_length=2000, blank=True, validators=[validate_plain_text])
    status = models.CharField(max_length=20, choices=ContactStatus.choices, default=ContactStatus.ACTIVE, db_index=True)
    tags = models.ManyToManyField("Tag", through="ContactTag", related_name="contacts")
    merged_into = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="merged_contacts"
    )
    created_by = models.ForeignKey(
        OrganizationMembership, on_delete=models.PROTECT, null=True, blank=True, related_name="contacts_created"
    )
    updated_by = models.ForeignKey(
        OrganizationMembership, on_delete=models.PROTECT, null=True, blank=True, related_name="contacts_updated"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "display_name"]
        indexes = [
            models.Index(fields=["organization", "status", "-updated_at"]),
            models.Index(fields=["organization", "display_name"]),
            models.Index(fields=["organization", "created_at"]),
        ]

    def clean(self):
        super().clean()
        if self.merged_into_id:
            if self.merged_into_id == self.id:
                raise ValidationError({"merged_into": _("A contact cannot merge into itself.")})
            if self.merged_into.organization_id != self.organization_id:
                raise ValidationError({"merged_into": _("Contact belongs to another organization.")})


class ContactIdentityType(models.TextChoices):
    PHONE = "phone", _("Phone")
    EMAIL = "email", _("Email")
    INSTAGRAM = "instagram", "Instagram"
    TELEGRAM = "telegram", "Telegram"
    WHATSAPP = "whatsapp", "WhatsApp"
    WEB_CHAT = "web_chat", _("Web chat")
    EXTERNAL = "external", _("External")


class ContactIdentity(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="identities")
    type = models.CharField(max_length=20, choices=ContactIdentityType.choices, db_index=True)
    raw_value = models.CharField(max_length=320, validators=[validate_plain_text])
    normalized_value = models.CharField(max_length=320, db_index=True, editable=False)
    external_user_id = models.CharField(max_length=255, blank=True, db_index=True)
    channel_connection = models.ForeignKey(
        ChannelConnection, on_delete=models.PROTECT, null=True, blank=True, related_name="contact_identities"
    )
    is_primary = models.BooleanField(default=False)
    is_verified = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True, validators=[validate_safe_metadata])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_primary", "type", "normalized_value"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "type", "normalized_value", "channel_connection"],
                condition=Q(channel_connection__isnull=False),
                name="unique_org_connected_identity",
            ),
            models.UniqueConstraint(
                fields=["organization", "type", "normalized_value"],
                condition=Q(channel_connection__isnull=True),
                name="unique_org_unconnected_identity",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "type", "normalized_value"]),
            models.Index(fields=["organization", "channel_connection", "external_user_id"]),
        ]


class Tag(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=60, validators=[validate_plain_text])
    color_token = models.CharField(max_length=30, default="green")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["organization", "name"], name="unique_org_tag_name")]


class ContactTag(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="contact_tags")
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="contact_tags")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["contact", "tag"], name="unique_contact_tag")]


class ContactNote(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="notes")
    author_membership = models.ForeignKey(
        OrganizationMembership, on_delete=models.PROTECT, related_name="contact_notes"
    )
    body = models.TextField(max_length=5000, validators=[validate_plain_text])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


class ConversationStatus(models.TextChoices):
    OPEN = "open", _("Open")
    PENDING = "pending", _("Pending")
    RESOLVED = "resolved", _("Resolved")
    CLOSED = "closed", _("Closed")


class ConversationPriority(models.TextChoices):
    LOW = "low", _("Low")
    NORMAL = "normal", _("Normal")
    HIGH = "high", _("High")
    URGENT = "urgent", _("Urgent")


class AssignmentState(models.TextChoices):
    UNASSIGNED = "unassigned", _("Unassigned")
    ASSIGNED = "assigned", _("Assigned")


class AutomationState(models.TextChoices):
    MANUAL = "manual", _("Manual")
    AI_PAUSED = "ai_paused", _("AI paused")
    AI_AVAILABLE = "ai_available", _("AI available")


class ConversationAIState(models.TextChoices):
    OFF = "off", _("Off")
    SUGGEST = "suggest", _("Suggest")
    AUTOPILOT_TEST = "autopilot_test", _("Internal test autopilot")
    AUTOPILOT_WEB_CHAT = "autopilot_web_chat", _("Web Chat autopilot")
    PAUSED_BY_HUMAN = "paused_by_human", _("Paused by human")
    HANDOFF_REQUIRED = "handoff_required", _("Handoff required")


class Conversation(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel_connection = models.ForeignKey(ChannelConnection, on_delete=models.PROTECT, related_name="conversations")
    channel_type = models.CharField(max_length=20, db_index=True)
    external_thread_id = models.CharField(max_length=255)
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="conversations")
    status = models.CharField(max_length=20, choices=ConversationStatus.choices, default=ConversationStatus.OPEN, db_index=True)
    priority = models.CharField(max_length=20, choices=ConversationPriority.choices, default=ConversationPriority.NORMAL, db_index=True)
    assignment_state = models.CharField(max_length=20, choices=AssignmentState.choices, default=AssignmentState.UNASSIGNED, db_index=True)
    assigned_membership = models.ForeignKey(
        OrganizationMembership, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_conversations"
    )
    automation_state = models.CharField(max_length=20, choices=AutomationState.choices, default=AutomationState.MANUAL)
    ai_state = models.CharField(
        max_length=24,
        choices=ConversationAIState.choices,
        default=ConversationAIState.OFF,
        db_index=True,
    )
    ai_state_updated_at = models.DateTimeField(null=True, blank=True)
    handoff_reason = models.CharField(max_length=500, blank=True, validators=[validate_plain_text])
    unread_count = models.PositiveIntegerField(default=0)
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_inbound_at = models.DateTimeField(null=True, blank=True)
    last_outbound_at = models.DateTimeField(null=True, blank=True)
    subject = models.CharField(max_length=300, blank=True, validators=[validate_plain_text])
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = [models.F("last_message_at").desc(nulls_last=True), "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "channel_connection", "external_thread_id"],
                name="unique_org_external_conversation",
            )
        ]
        indexes = [
            models.Index(fields=["organization", "status", "-last_message_at"]),
            models.Index(fields=["organization", "assigned_membership", "-last_message_at"]),
            models.Index(fields=["organization", "channel_type", "-last_message_at"]),
            models.Index(fields=["organization", "contact", "-last_message_at"]),
            models.Index(fields=["organization", "priority", "-last_message_at"]),
        ]

    def clean(self):
        super().clean()
        expected = AssignmentState.ASSIGNED if self.assigned_membership_id else AssignmentState.UNASSIGNED
        self.assignment_state = expected


class MessageDirection(models.TextChoices):
    INBOUND = "inbound", _("Inbound")
    OUTBOUND = "outbound", _("Outbound")
    SYSTEM = "system", _("System")


class MessageSenderType(models.TextChoices):
    CUSTOMER = "customer", _("Customer")
    AGENT = "agent", _("Agent")
    SYSTEM = "system", _("System")
    FUTURE_AI = "future_ai", _("Future AI")
    AI = "ai", _("AI")


class MessageContentType(models.TextChoices):
    TEXT = "text", _("Text")
    NOTE = "note", _("Note")
    EVENT = "event", _("Event")


class MessageStatus(models.TextChoices):
    QUEUED = "queued", _("Queued")
    SENT = "sent", _("Sent")
    DELIVERED = "delivered", _("Delivered")
    FAILED = "failed", _("Failed")
    RECEIVED = "received", _("Received")


class Message(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    channel_connection = models.ForeignKey(ChannelConnection, on_delete=models.PROTECT, related_name="messages")
    direction = models.CharField(max_length=20, choices=MessageDirection.choices)
    sender_type = models.CharField(max_length=20, choices=MessageSenderType.choices)
    sender_membership = models.ForeignKey(
        OrganizationMembership, on_delete=models.SET_NULL, null=True, blank=True, related_name="sent_messages"
    )
    provider_message_id = models.CharField(max_length=255, null=True, blank=True)
    client_message_id = models.CharField(max_length=255, null=True, blank=True)
    content_type = models.CharField(max_length=20, choices=MessageContentType.choices, default=MessageContentType.TEXT)
    body = models.TextField(max_length=10000, validators=[validate_plain_text])
    status = models.CharField(max_length=20, choices=MessageStatus.choices)
    error_code = models.CharField(max_length=80, blank=True)
    reply_to = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="replies")
    metadata = models.JSONField(default=dict, blank=True, validators=[validate_safe_metadata])
    occurred_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["occurred_at", "created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "channel_connection", "provider_message_id"],
                condition=Q(provider_message_id__isnull=False),
                name="unique_org_provider_message",
            ),
            models.UniqueConstraint(
                fields=["organization", "conversation", "client_message_id"],
                condition=Q(client_message_id__isnull=False),
                name="unique_org_client_message",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "conversation", "-occurred_at"]),
            models.Index(fields=["organization", "status", "-occurred_at"]),
        ]


class Pipeline(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, validators=[validate_plain_text])
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="unique_org_pipeline_name"),
            models.UniqueConstraint(
                fields=["organization"], condition=Q(is_default=True), name="one_default_pipeline_per_org"
            ),
        ]


class PipelineStageType(models.TextChoices):
    OPEN = "open", _("Open")
    WON = "won", _("Won")
    LOST = "lost", _("Lost")


class PipelineStage(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pipeline = models.ForeignKey(Pipeline, on_delete=models.CASCADE, related_name="stages")
    name = models.CharField(max_length=100, validators=[validate_plain_text])
    position = models.PositiveSmallIntegerField()
    color_token = models.CharField(max_length=30, default="green")
    stage_type = models.CharField(max_length=10, choices=PipelineStageType.choices, default=PipelineStageType.OPEN)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["position", "name"]
        constraints = [
            models.UniqueConstraint(fields=["pipeline", "position"], name="unique_pipeline_stage_position"),
            models.UniqueConstraint(fields=["pipeline", "name"], name="unique_pipeline_stage_name"),
        ]


class LeadStatus(models.TextChoices):
    OPEN = "open", _("Open")
    WON = "won", _("Won")
    LOST = "lost", _("Lost")
    ARCHIVED = "archived", _("Archived")


class Lead(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="leads")
    source_conversation = models.ForeignKey(
        Conversation, on_delete=models.SET_NULL, null=True, blank=True, related_name="leads"
    )
    source_channel_type = models.CharField(max_length=20, blank=True, db_index=True)
    pipeline = models.ForeignKey(Pipeline, on_delete=models.PROTECT, related_name="leads")
    stage = models.ForeignKey(PipelineStage, on_delete=models.PROTECT, related_name="leads")
    title = models.CharField(max_length=200, validators=[validate_plain_text])
    description = models.TextField(max_length=5000, blank=True, validators=[validate_plain_text])
    assigned_membership = models.ForeignKey(
        OrganizationMembership, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_leads"
    )
    estimated_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True)
    status = models.CharField(max_length=20, choices=LeadStatus.choices, default=LeadStatus.OPEN, db_index=True)
    lost_reason = models.CharField(max_length=500, blank=True, validators=[validate_plain_text])
    next_follow_up_at = models.DateTimeField(null=True, blank=True, db_index=True)
    won_at = models.DateTimeField(null=True, blank=True)
    lost_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(OrganizationMembership, on_delete=models.PROTECT, related_name="leads_created")
    updated_by = models.ForeignKey(OrganizationMembership, on_delete=models.PROTECT, related_name="leads_updated")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["organization", "status", "pipeline", "stage"]),
            models.Index(fields=["organization", "assigned_membership", "status"]),
            models.Index(fields=["organization", "contact", "status"]),
            models.Index(fields=["organization", "next_follow_up_at"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.stage_id and self.pipeline_id and self.stage.pipeline_id != self.pipeline_id:
            errors["stage"] = _("Stage does not belong to the selected pipeline.")
        if self.source_conversation_id and self.source_conversation.contact_id != self.contact_id:
            errors["source_conversation"] = _("Conversation belongs to another contact.")
        if self.estimated_value is not None and not self.currency:
            errors["currency"] = _("Currency is required when a value is provided.")
        if errors:
            raise ValidationError(errors)


class FollowUpTaskStatus(models.TextChoices):
    OPEN = "open", _("Open")
    COMPLETED = "completed", _("Completed")
    CANCELLED = "cancelled", _("Cancelled")


class FollowUpTask(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200, validators=[validate_plain_text])
    due_at = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=20, choices=FollowUpTaskStatus.choices, default=FollowUpTaskStatus.OPEN, db_index=True)
    assigned_membership = models.ForeignKey(
        OrganizationMembership, on_delete=models.SET_NULL, null=True, blank=True, related_name="follow_up_tasks"
    )
    related_contact = models.ForeignKey(Contact, on_delete=models.CASCADE, null=True, blank=True, related_name="tasks")
    related_lead = models.ForeignKey(Lead, on_delete=models.CASCADE, null=True, blank=True, related_name="tasks")
    related_conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, null=True, blank=True, related_name="tasks"
    )
    created_by = models.ForeignKey(OrganizationMembership, on_delete=models.PROTECT, related_name="tasks_created")
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status", "due_at"]
        indexes = [
            models.Index(fields=["organization", "status", "due_at"]),
            models.Index(fields=["organization", "assigned_membership", "status", "due_at"]),
        ]

    def clean(self):
        super().clean()
        if not any([self.related_contact_id, self.related_lead_id, self.related_conversation_id]):
            raise ValidationError(_("A task must be linked to a contact, lead, or conversation."))


class CrmActivity(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_type = models.CharField(max_length=80, db_index=True)
    actor_membership = models.ForeignKey(
        OrganizationMembership, on_delete=models.SET_NULL, null=True, blank=True, related_name="crm_activities"
    )
    contact_id = models.UUIDField(null=True, blank=True, db_index=True)
    conversation_id = models.UUIDField(null=True, blank=True, db_index=True)
    lead_id = models.UUIDField(null=True, blank=True, db_index=True)
    task_id = models.UUIDField(null=True, blank=True, db_index=True)
    summary = models.CharField(max_length=500, validators=[validate_plain_text])
    metadata = models.JSONField(default=dict, blank=True, validators=[validate_safe_metadata])
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["organization", "-created_at", "event_type"])]

    def save(self, *args, **kwargs):
        if self.pk and CrmActivity.objects.filter(pk=self.pk).exists():
            raise ValidationError(_("CRM activity records are immutable."))
        return super().save(*args, **kwargs)
