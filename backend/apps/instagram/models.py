from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from channels.models import ChannelConnection, ChannelType
from crm.models import Conversation, Message, validate_plain_text, validate_safe_metadata
from organizations.models import OrganizationMembership, OrganizationOwnedModel


class InstagramConnectionStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    CONNECTED = "connected", "Connected"
    DEGRADED = "degraded", "Degraded"
    EXPIRED = "expired", "Expired"
    REVOKED = "revoked", "Revoked"
    DISCONNECTED = "disconnected", "Disconnected"


class InstagramAutomationMode(models.TextChoices):
    MANUAL = "manual", "Manual"
    SUGGEST = "suggest", "Suggest"
    AUTOPILOT = "autopilot", "Autopilot"


class InstagramConnection(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel_connection = models.OneToOneField(
        ChannelConnection,
        on_delete=models.PROTECT,
        related_name="instagram_connection",
    )
    instagram_user_id = models.CharField(max_length=255, db_index=True)
    username = models.CharField(max_length=160, validators=[validate_plain_text])
    account_type = models.CharField(max_length=40, default="BUSINESS", validators=[validate_plain_text])
    profile_name = models.CharField(max_length=200, blank=True, validators=[validate_plain_text])
    profile_picture_url = models.URLField(max_length=1000, blank=True)
    profile_picture_expires_at = models.DateTimeField(null=True, blank=True)
    graph_api_version = models.CharField(max_length=32, blank=True, validators=[validate_plain_text])
    permission_snapshot = models.JSONField(default=list)
    webhook_subscription_status = models.CharField(max_length=40, default="pending")
    connection_status = models.CharField(
        max_length=20,
        choices=InstagramConnectionStatus.choices,
        default=InstagramConnectionStatus.DRAFT,
        db_index=True,
    )
    automation_mode = models.CharField(
        max_length=20,
        choices=InstagramAutomationMode.choices,
        default=InstagramAutomationMode.MANUAL,
    )
    human_agent_approved = models.BooleanField(default=False)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    last_webhook_at = models.DateTimeField(null=True, blank=True)
    last_successful_send_at = models.DateTimeField(null=True, blank=True)
    last_health_check_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=80, blank=True)
    failure_count = models.PositiveSmallIntegerField(default=0)
    circuit_open_until = models.DateTimeField(null=True, blank=True)
    connected_by = models.ForeignKey(
        OrganizationMembership,
        on_delete=models.PROTECT,
        related_name="instagram_connections_created",
    )
    connected_at = models.DateTimeField(null=True, blank=True)
    disconnected_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["instagram_user_id"],
                condition=Q(
                    connection_status__in=[
                        InstagramConnectionStatus.DRAFT,
                        InstagramConnectionStatus.CONNECTED,
                        InstagramConnectionStatus.DEGRADED,
                        InstagramConnectionStatus.EXPIRED,
                    ]
                ),
                name="unique_attached_instagram_account",
            )
        ]
        indexes = [
            models.Index(fields=["organization", "connection_status", "-created_at"]),
            models.Index(fields=["instagram_user_id", "connection_status"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.channel_connection_id:
            if self.channel_connection.organization_id != self.organization_id:
                errors["channel_connection"] = "Channel connection belongs to another organization."
            if (
                self.channel_connection.type != ChannelType.INSTAGRAM
                or self.channel_connection.provider != "meta_instagram"
            ):
                errors["channel_connection"] = "A Meta Instagram channel connection is required."
        if self.connected_by_id and self.connected_by.organization_id != self.organization_id:
            errors["connected_by"] = "Membership belongs to another organization."
        if not isinstance(self.permission_snapshot, list) or any(
            not isinstance(value, str) or len(value) > 120 for value in self.permission_snapshot
        ):
            errors["permission_snapshot"] = "Permissions must be a list of safe names."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values_list("organization_id", flat=True).first()
            if original and original != self.organization_id:
                raise ValidationError({"organization": "Organization ownership is immutable."})
        return super().save(*args, **kwargs)


class InstagramOAuthState(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    state_hash = models.CharField(max_length=64, unique=True, editable=False)
    user_id = models.UUIDField(db_index=True)
    membership = models.ForeignKey(
        OrganizationMembership,
        on_delete=models.CASCADE,
        related_name="instagram_oauth_states",
    )
    intended_redirect = models.CharField(max_length=300, validators=[validate_plain_text])
    nonce_hash = models.CharField(max_length=64, editable=False)
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "user_id", "expires_at"])]


class InstagramWebhookStatus(models.TextChoices):
    RECEIVED = "received", "Received"
    PROCESSING = "processing", "Processing"
    PROCESSED = "processed", "Processed"
    IGNORED = "ignored", "Ignored"
    FAILED = "failed", "Failed"
    DEAD_LETTER = "dead_letter", "Dead letter"


class InstagramWebhookEvent(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(
        InstagramConnection,
        on_delete=models.PROTECT,
        related_name="webhook_events",
    )
    event_hash = models.CharField(max_length=64, unique=True, editable=False)
    professional_account_id = models.CharField(max_length=255, db_index=True)
    event_type = models.CharField(max_length=60, db_index=True)
    normalized_payload = models.JSONField(default=dict, validators=[validate_safe_metadata])
    status = models.CharField(
        max_length=20,
        choices=InstagramWebhookStatus.choices,
        default=InstagramWebhookStatus.RECEIVED,
        db_index=True,
    )
    attempt_count = models.PositiveSmallIntegerField(default=0)
    safe_error_code = models.CharField(max_length=80, blank=True)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]
        indexes = [
            models.Index(fields=["organization", "status", "-received_at"]),
            models.Index(fields=["professional_account_id", "event_type", "-received_at"]),
        ]


class InstagramConversationWindow(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.OneToOneField(
        Conversation,
        on_delete=models.CASCADE,
        related_name="instagram_window",
    )
    last_customer_message_at = models.DateTimeField()
    standard_window_expires_at = models.DateTimeField(db_index=True)
    human_agent_window_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["organization", "standard_window_expires_at"])]


class InstagramOutboundStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    SENDING = "sending", "Sending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    DEAD_LETTER = "dead_letter", "Dead letter"


class InstagramOutboundAttempt(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(
        InstagramConnection,
        on_delete=models.PROTECT,
        related_name="outbound_attempts",
    )
    message = models.OneToOneField(
        Message,
        on_delete=models.CASCADE,
        related_name="instagram_outbound_attempt",
    )
    status = models.CharField(
        max_length=20,
        choices=InstagramOutboundStatus.choices,
        default=InstagramOutboundStatus.QUEUED,
        db_index=True,
    )
    attempt_count = models.PositiveSmallIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(5)],
    )
    provider_request_id = models.CharField(max_length=120, blank=True)
    safe_error_code = models.CharField(max_length=80, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "status", "-created_at"])]
