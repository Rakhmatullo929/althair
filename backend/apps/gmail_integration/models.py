from __future__ import annotations

import re
import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from channels.models import ChannelConnection, ChannelType
from core.utils.encryption import EncryptedTextField
from crm.models import Conversation, Message, validate_plain_text, validate_safe_metadata
from organizations.models import OrganizationMembership, OrganizationOwnedModel


class GmailConnectionStatus(models.TextChoices):
    SYNCING = "syncing", "Syncing"
    CONNECTED = "connected", "Connected"
    DEGRADED = "degraded", "Degraded"
    REAUTH_REQUIRED = "reauth_required", "Reauthorization required"
    REVOKED = "revoked", "Revoked"
    PERMISSION_MISSING = "permission_missing", "Permission missing"
    WATCH_EXPIRED = "watch_expired", "Watch expired"
    DISCONNECTED = "disconnected", "Disconnected"


class GmailAutomationMode(models.TextChoices):
    MANUAL = "manual", "Manual"
    SUGGEST = "suggest", "Suggest"
    AUTOPILOT = "autopilot", "Autopilot"


class GmailInitialSyncMode(models.TextChoices):
    FROM_NOW = "from_now", "From now"
    RECENT = "recent", "Recent messages"


class GmailInitialSyncStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


def default_included_labels():
    return ["INBOX"]


def default_excluded_labels():
    return ["SPAM", "TRASH"]


class GmailConnection(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel_connection = models.OneToOneField(
        ChannelConnection, on_delete=models.PROTECT, related_name="gmail_connection"
    )
    mailbox_email = models.EmailField(max_length=320, db_index=True)
    mailbox_email_normalized = models.EmailField(max_length=320, editable=False, db_index=True)
    mailbox_name = models.CharField(max_length=200, blank=True, validators=[validate_plain_text])
    google_user_id = models.CharField(max_length=255, db_index=True)
    scope_snapshot = models.JSONField(default=list)
    connection_status = models.CharField(
        max_length=24,
        choices=GmailConnectionStatus.choices,
        default=GmailConnectionStatus.CONNECTED,
        db_index=True,
    )
    automation_mode = models.CharField(
        max_length=20,
        choices=GmailAutomationMode.choices,
        default=GmailAutomationMode.MANUAL,
    )
    initial_sync_mode = models.CharField(
        max_length=20,
        choices=GmailInitialSyncMode.choices,
        default=GmailInitialSyncMode.RECENT,
    )
    initial_sync_status = models.CharField(
        max_length=20,
        choices=GmailInitialSyncStatus.choices,
        default=GmailInitialSyncStatus.PENDING,
        db_index=True,
    )
    initial_sync_max_messages = models.PositiveSmallIntegerField(
        default=100,
        validators=[MinValueValidator(1), MaxValueValidator(500)],
    )
    included_label_ids = models.JSONField(default=default_included_labels)
    excluded_label_ids = models.JSONField(default=default_excluded_labels)
    sync_start_at = models.DateTimeField(null=True, blank=True)
    initial_sync_cancel_requested_at = models.DateTimeField(null=True, blank=True)
    retention_days = models.PositiveSmallIntegerField(
        default=365,
        validators=[MinValueValidator(1), MaxValueValidator(3650)],
    )
    history_id = models.CharField(max_length=64, blank=True)
    watch_expiration_at = models.DateTimeField(null=True, blank=True, db_index=True)
    watch_topic = models.CharField(max_length=500, blank=True, validators=[validate_plain_text])
    last_notification_at = models.DateTimeField(null=True, blank=True)
    last_incremental_sync_at = models.DateTimeField(null=True, blank=True)
    last_full_sync_at = models.DateTimeField(null=True, blank=True)
    last_successful_send_at = models.DateTimeField(null=True, blank=True)
    last_health_check_at = models.DateTimeField(null=True, blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=80, blank=True)
    failure_count = models.PositiveSmallIntegerField(default=0)
    connected_by = models.ForeignKey(
        OrganizationMembership, on_delete=models.PROTECT, related_name="gmail_connections_created"
    )
    connected_at = models.DateTimeField(auto_now_add=True)
    disconnected_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["mailbox_email_normalized"]
        constraints = [
            models.UniqueConstraint(
                fields=["mailbox_email_normalized"],
                condition=Q(
                    connection_status__in=[
                        GmailConnectionStatus.SYNCING,
                        GmailConnectionStatus.CONNECTED,
                        GmailConnectionStatus.DEGRADED,
                        GmailConnectionStatus.REAUTH_REQUIRED,
                        GmailConnectionStatus.REVOKED,
                        GmailConnectionStatus.PERMISSION_MISSING,
                        GmailConnectionStatus.WATCH_EXPIRED,
                    ]
                ),
                name="unique_active_gmail_mailbox",
            )
        ]
        indexes = [
            models.Index(fields=["organization", "connection_status", "-created_at"]),
            models.Index(fields=["mailbox_email_normalized", "connection_status"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        self.mailbox_email_normalized = self.mailbox_email.strip().casefold()
        if self.channel_connection_id:
            channel = self.channel_connection
            if channel.organization_id != self.organization_id:
                errors["channel_connection"] = "Channel connection belongs to another organization."
            if channel.type != ChannelType.GMAIL or channel.provider != "google_gmail":
                errors["channel_connection"] = "A Google Gmail channel connection is required."
        if self.connected_by_id and self.connected_by.organization_id != self.organization_id:
            errors["connected_by"] = "Membership belongs to another organization."
        if not isinstance(self.scope_snapshot, list) or any(
            not isinstance(scope, str) or len(scope) > 300 for scope in self.scope_snapshot
        ):
            errors["scope_snapshot"] = "Scopes must be a list of safe names."
        for field_name in ("included_label_ids", "excluded_label_ids"):
            labels = getattr(self, field_name)
            if (
                not isinstance(labels, list)
                or len(labels) > 20
                or any(
                    not isinstance(label, str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", label)
                    for label in labels
                )
            ):
                errors[field_name] = "Labels must be a bounded list of safe Gmail label IDs."
        if "SPAM" not in self.excluded_label_ids or "TRASH" not in self.excluded_label_ids:
            errors["excluded_label_ids"] = "Spam and Trash must stay excluded."
        if not self.included_label_ids:
            errors["included_label_ids"] = "At least one included label is required."
        if set(self.included_label_ids) & set(self.excluded_label_ids):
            errors["included_label_ids"] = "Included labels cannot contain excluded labels."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values_list("organization_id", flat=True).first()
            if original and original != self.organization_id:
                raise ValidationError({"organization": "Organization ownership is immutable."})
        self.mailbox_email_normalized = self.mailbox_email.strip().casefold()
        return super().save(*args, **kwargs)


class GmailOAuthState(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    state_hash = models.CharField(max_length=64, unique=True, editable=False)
    user_id = models.UUIDField(db_index=True)
    membership = models.ForeignKey(
        OrganizationMembership, on_delete=models.CASCADE, related_name="gmail_oauth_states"
    )
    intended_redirect = models.CharField(max_length=300, validators=[validate_plain_text])
    code_verifier = EncryptedTextField(editable=False)
    initial_sync_mode = models.CharField(
        max_length=20,
        choices=GmailInitialSyncMode.choices,
        default=GmailInitialSyncMode.RECENT,
    )
    initial_sync_max_messages = models.PositiveSmallIntegerField(default=100)
    reconnect_connection = models.ForeignKey(
        GmailConnection,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="oauth_reconnect_states",
    )
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class GmailNotificationStatus(models.TextChoices):
    RECEIVED = "received", "Received"
    PROCESSING = "processing", "Processing"
    PROCESSED = "processed", "Processed"
    IGNORED = "ignored", "Ignored"
    FAILED = "failed", "Failed"
    DEAD_LETTER = "dead_letter", "Dead letter"


class GmailNotification(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(GmailConnection, on_delete=models.PROTECT, related_name="notifications")
    pubsub_message_id = models.CharField(max_length=255, unique=True)
    history_id = models.CharField(max_length=64)
    status = models.CharField(
        max_length=20,
        choices=GmailNotificationStatus.choices,
        default=GmailNotificationStatus.RECEIVED,
        db_index=True,
    )
    attempt_count = models.PositiveSmallIntegerField(default=0)
    safe_error_code = models.CharField(max_length=80, blank=True)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]
        indexes = [models.Index(fields=["organization", "status", "-received_at"])]


class GmailSyncType(models.TextChoices):
    INITIAL = "initial", "Initial"
    INCREMENTAL = "incremental", "Incremental"
    FULL = "full", "Bounded full"
    RECONCILIATION = "reconciliation", "Reconciliation"


class GmailSyncStatus(models.TextChoices):
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class GmailSyncRun(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(GmailConnection, on_delete=models.PROTECT, related_name="sync_runs")
    sync_type = models.CharField(max_length=20, choices=GmailSyncType.choices)
    status = models.CharField(max_length=20, choices=GmailSyncStatus.choices, default=GmailSyncStatus.RUNNING)
    start_history_id = models.CharField(max_length=64, blank=True)
    end_history_id = models.CharField(max_length=64, blank=True)
    imported_count = models.PositiveIntegerField(default=0)
    ignored_count = models.PositiveIntegerField(default=0)
    fallback_reason = models.CharField(max_length=80, blank=True)
    safe_error_code = models.CharField(max_length=80, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]


class GmailMessageRecord(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(GmailConnection, on_delete=models.PROTECT, related_name="message_records")
    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name="gmail_record")
    gmail_message_id = models.CharField(max_length=255)
    gmail_thread_id = models.CharField(max_length=255, db_index=True)
    rfc_message_id = models.CharField(max_length=500, blank=True, validators=[validate_plain_text])
    in_reply_to = models.CharField(max_length=500, blank=True, validators=[validate_plain_text])
    references = models.TextField(max_length=4000, blank=True, validators=[validate_plain_text])
    subject = models.CharField(max_length=500, blank=True, validators=[validate_plain_text])
    reply_to = models.EmailField(max_length=320, blank=True)
    to_recipients = models.JSONField(default=list)
    cc_recipients = models.JSONField(default=list)
    snippet = models.CharField(max_length=500, blank=True, validators=[validate_plain_text])
    internal_date = models.DateTimeField(null=True, blank=True)
    participants = models.JSONField(default=list)
    label_ids = models.JSONField(default=list)
    attachment_metadata = models.JSONField(default=list)
    is_automated = models.BooleanField(default=False)
    is_encrypted = models.BooleanField(default=False)
    is_from_self = models.BooleanField(default=False)
    is_historical = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "gmail_message_id"], name="unique_gmail_provider_message"
            )
        ]
        indexes = [models.Index(fields=["organization", "gmail_thread_id"])]


class GmailOutboundStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    SENDING = "sending", "Sending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    DEAD_LETTER = "dead_letter", "Dead letter"


class GmailOutboundAttempt(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(GmailConnection, on_delete=models.PROTECT, related_name="outbound_attempts")
    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name="gmail_outbound_attempt")
    status = models.CharField(max_length=20, choices=GmailOutboundStatus.choices, default=GmailOutboundStatus.QUEUED)
    attempt_count = models.PositiveSmallIntegerField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(5)]
    )
    provider_request_id = models.CharField(max_length=120, blank=True)
    safe_error_code = models.CharField(max_length=80, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class GmailAuditEvent(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(
        GmailConnection, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_events"
    )
    actor_membership = models.ForeignKey(
        OrganizationMembership, on_delete=models.SET_NULL, null=True, blank=True, related_name="gmail_audit_events"
    )
    event_type = models.CharField(max_length=80, db_index=True)
    metadata = models.JSONField(default=dict, blank=True, validators=[validate_safe_metadata])
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
