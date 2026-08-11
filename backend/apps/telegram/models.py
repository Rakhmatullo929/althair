from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.db import models
from django.db.models import Q

from channels.models import ChannelConnection, ChannelType
from crm.models import Message, validate_plain_text, validate_safe_metadata
from organizations.models import OrganizationMembership, OrganizationOwnedModel


class TelegramUserLinkStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    LINKED = "linked", "Linked"
    EXPIRED = "expired", "Expired"
    REVOKED = "revoked", "Revoked"


class TelegramUserLink(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="telegram_links")
    telegram_user_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    telegram_username = models.CharField(max_length=64, blank=True, validators=[validate_plain_text])
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    status = models.CharField(max_length=16, choices=TelegramUserLinkStatus.choices, default=TelegramUserLinkStatus.PENDING, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    linked_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["telegram_user_id"],
                condition=Q(status=TelegramUserLinkStatus.LINKED),
                name="unique_linked_telegram_user",
            )
        ]


class TelegramManagedRequestStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    AWAITING = "awaiting_user_confirmation", "Awaiting user confirmation"
    CREATED = "created", "Created"
    TOKEN_RECEIVED = "token_received", "Token received"
    WEBHOOK_CONFIGURED = "webhook_configured", "Webhook configured"
    FAILED = "failed", "Failed"
    EXPIRED = "expired", "Expired"
    CANCELLED = "cancelled", "Cancelled"


class TelegramManagedBotRequest(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requested_by = models.ForeignKey(OrganizationMembership, on_delete=models.PROTECT, related_name="telegram_managed_requests")
    linked_telegram_user_id = models.BigIntegerField(db_index=True)
    suggested_username = models.CharField(max_length=32, validators=[validate_plain_text])
    suggested_name = models.CharField(max_length=64, validators=[validate_plain_text])
    request_nonce_hash = models.CharField(max_length=64, unique=True, editable=False)
    status = models.CharField(max_length=32, choices=TelegramManagedRequestStatus.choices, default=TelegramManagedRequestStatus.DRAFT, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    created_bot_user_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    created_bot_username = models.CharField(max_length=64, blank=True, validators=[validate_plain_text])
    error_code = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "status", "expires_at"])]

    def clean(self):
        super().clean()
        if self.requested_by_id and self.requested_by.organization_id != self.organization_id:
            raise ValidationError({"requested_by": "Membership belongs to another organization."})


class TelegramConnectionType(models.TextChoices):
    MANAGED = "managed", "Managed"
    EXISTING = "existing", "Existing"


class TelegramConnectionStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    AWAITING_CREATION = "awaiting_creation", "Awaiting creation"
    CONNECTED = "connected", "Connected"
    DEGRADED = "degraded", "Degraded"
    TOKEN_INVALID = "token_invalid", "Token invalid"
    WEBHOOK_ERROR = "webhook_error", "Webhook error"
    PAUSED = "paused", "Paused"
    REVOKED = "revoked", "Revoked"
    DISCONNECTED = "disconnected", "Disconnected"


class TelegramAutomationMode(models.TextChoices):
    MANUAL = "manual", "Manual"
    SUGGEST = "suggest", "Suggest"
    AUTOPILOT = "autopilot", "Autopilot"


class TelegramBotConnection(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel_connection = models.OneToOneField(ChannelConnection, on_delete=models.PROTECT, related_name="telegram_connection")
    managed_request = models.OneToOneField(TelegramManagedBotRequest, on_delete=models.SET_NULL, null=True, blank=True, related_name="connection")
    connection_type = models.CharField(max_length=16, choices=TelegramConnectionType.choices)
    bot_user_id = models.BigIntegerField(db_index=True)
    bot_username = models.CharField(max_length=64, validators=[validate_plain_text])
    bot_name = models.CharField(max_length=64, validators=[validate_plain_text])
    owner_telegram_user_id = models.BigIntegerField(null=True, blank=True)
    manager_bot_user_id = models.BigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=24, choices=TelegramConnectionStatus.choices, default=TelegramConnectionStatus.DRAFT, db_index=True)
    token_version = models.PositiveIntegerField(default=1)
    webhook_public_key = models.CharField(max_length=64, unique=True, editable=False)
    webhook_status = models.CharField(max_length=24, default="pending")
    allowed_updates = models.JSONField(default=list)
    access_restricted = models.BooleanField(default=False)
    permitted_telegram_user_ids = models.JSONField(default=list, blank=True)
    default_language = models.CharField(max_length=2, choices=[("ru", "RU"), ("uz", "UZ"), ("en", "EN")], default="ru")
    supported_languages = models.JSONField(default=list)
    privacy_url = models.URLField(max_length=500, blank=True)
    automation_mode = models.CharField(max_length=16, choices=TelegramAutomationMode.choices, default=TelegramAutomationMode.MANUAL)
    last_update_at = models.DateTimeField(null=True, blank=True)
    last_send_at = models.DateTimeField(null=True, blank=True)
    last_health_check_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=80, blank=True)
    failure_count = models.PositiveSmallIntegerField(default=0)
    circuit_open_until = models.DateTimeField(null=True, blank=True)
    connected_by = models.ForeignKey(OrganizationMembership, on_delete=models.PROTECT, related_name="telegram_connections_created")
    connected_at = models.DateTimeField(null=True, blank=True)
    disconnected_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["bot_user_id"],
                condition=~Q(status__in=[TelegramConnectionStatus.REVOKED, TelegramConnectionStatus.DISCONNECTED]),
                name="unique_active_telegram_bot",
            ),
            models.UniqueConstraint(
                fields=["organization"],
                condition=~Q(status__in=[TelegramConnectionStatus.REVOKED, TelegramConnectionStatus.DISCONNECTED]),
                name="one_active_telegram_bot_per_org",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.channel_connection_id:
            if self.channel_connection.organization_id != self.organization_id:
                errors["channel_connection"] = "Channel connection belongs to another organization."
            if self.channel_connection.type != ChannelType.TELEGRAM or self.channel_connection.provider != "telegram_bot_api":
                errors["channel_connection"] = "A Telegram Bot API channel connection is required."
        if self.connected_by_id and self.connected_by.organization_id != self.organization_id:
            errors["connected_by"] = "Membership belongs to another organization."
        if not isinstance(self.allowed_updates, list) or not isinstance(self.permitted_telegram_user_ids, list):
            errors["allowed_updates"] = "Telegram list fields must be arrays."
        if len(self.permitted_telegram_user_ids) > 10:
            errors["permitted_telegram_user_ids"] = "At most 10 additional users are supported."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values_list("organization_id", flat=True).first()
            if original and original != self.organization_id:
                raise ValidationError({"organization": "Organization ownership is immutable."})
        return super().save(*args, **kwargs)


class TelegramEventStatus(models.TextChoices):
    RECEIVED = "received", "Received"
    PROCESSING = "processing", "Processing"
    PROCESSED = "processed", "Processed"
    IGNORED = "ignored", "Ignored"
    FAILED = "failed", "Failed"
    DEAD_LETTER = "dead_letter", "Dead letter"


class TelegramManagerEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    update_id = models.BigIntegerField(unique=True)
    update_type = models.CharField(max_length=40)
    normalized_payload = models.JSONField(default=dict, validators=[validate_safe_metadata])
    status = models.CharField(max_length=20, choices=TelegramEventStatus.choices, default=TelegramEventStatus.RECEIVED, db_index=True)
    organization_id = models.UUIDField(null=True, blank=True, db_index=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    safe_error_code = models.CharField(max_length=80, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)


class TelegramWebhookEvent(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(TelegramBotConnection, on_delete=models.PROTECT, related_name="webhook_events")
    update_id = models.BigIntegerField()
    update_type = models.CharField(max_length=40, db_index=True)
    normalized_payload = models.JSONField(default=dict, validators=[validate_safe_metadata])
    status = models.CharField(max_length=20, choices=TelegramEventStatus.choices, default=TelegramEventStatus.RECEIVED, db_index=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    safe_error_code = models.CharField(max_length=80, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]
        constraints = [models.UniqueConstraint(fields=["connection", "update_id"], name="unique_telegram_update_per_bot")]
        indexes = [models.Index(fields=["organization", "status", "-received_at"])]


class TelegramAuditEvent(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(TelegramBotConnection, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_events")
    managed_request = models.ForeignKey(TelegramManagedBotRequest, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_events")
    actor_membership = models.ForeignKey(OrganizationMembership, on_delete=models.SET_NULL, null=True, blank=True)
    event_type = models.CharField(max_length=80, db_index=True)
    metadata = models.JSONField(default=dict, validators=[validate_safe_metadata])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class TelegramOutboundAttempt(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(TelegramBotConnection, on_delete=models.PROTECT, related_name="outbound_attempts")
    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name="telegram_outbound_attempt")
    status = models.CharField(max_length=20, choices=[("queued", "Queued"), ("sending", "Sending"), ("sent", "Sent"), ("failed", "Failed"), ("dead_letter", "Dead letter")], default="queued", db_index=True)
    attempt_count = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(5)])
    safe_error_code = models.CharField(max_length=80, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
