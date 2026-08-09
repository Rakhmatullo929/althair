from __future__ import annotations

import hashlib
import hmac
import json
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from core.utils.encryption import EncryptedTextField
from organizations.models import Branch, OrganizationOwnedModel, validate_json_object


class ChannelType(models.TextChoices):
    INSTAGRAM = "instagram", "Instagram"
    TELEGRAM = "telegram", "Telegram"
    WHATSAPP = "whatsapp", "WhatsApp"
    GMAIL = "gmail", "Gmail"
    SMS = "sms", "SMS"
    VOICE = "voice", "Voice"
    WEBCHAT = "webchat", "Web chat"
    OTHER = "other", "Other"


class ChannelStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    CONNECTING = "connecting", "Connecting"
    ACTIVE = "active", "Active"
    ERROR = "error", "Error"
    DISCONNECTED = "disconnected", "Disconnected"


class ChannelConnection(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="channel_connections",
    )
    type = models.CharField(max_length=20, choices=ChannelType.choices, db_index=True)
    provider = models.CharField(max_length=80, db_index=True)
    display_name = models.CharField(max_length=160)
    external_identifier = models.CharField(max_length=255, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=ChannelStatus.choices,
        default=ChannelStatus.DRAFT,
        db_index=True,
    )
    configuration = models.JSONField(default=dict, blank=True, validators=[validate_json_object])
    encrypted_credentials = EncryptedTextField(blank=True, default="")
    webhook_secret_hash = models.CharField(max_length=64, blank=True, editable=False)
    last_error_code = models.CharField(max_length=80, blank=True)
    last_error_message = models.CharField(max_length=500, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "provider", "type", "external_identifier"],
                name="unique_org_channel_destination",
            ),
            models.UniqueConstraint(
                fields=["provider", "type", "external_identifier"],
                condition=Q(status=ChannelStatus.ACTIVE),
                name="unique_active_provider_destination",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status", "type"]),
            models.Index(fields=["provider", "type", "external_identifier", "status"]),
        ]

    def clean(self) -> None:
        super().clean()
        if self.branch_id and self.branch.organization_id != self.organization_id:
            raise ValidationError({"branch": "Branch belongs to another organization."})
        if not self.provider.strip() or not self.external_identifier.strip():
            raise ValidationError("Provider and external identifier are required.")

    def set_credentials(self, value: dict) -> None:
        if not isinstance(value, dict):
            raise ValidationError({"credentials": "Credentials must be a JSON object."})
        self.encrypted_credentials = json.dumps(value, separators=(",", ":"), sort_keys=True)

    def get_credentials(self) -> dict:
        if not self.encrypted_credentials:
            return {}
        return json.loads(self.encrypted_credentials)

    def set_webhook_secret(self, raw_secret: str) -> None:
        self.webhook_secret_hash = hashlib.sha256(raw_secret.encode("utf-8")).hexdigest()

    def verify_webhook_secret(self, raw_secret: str) -> bool:
        if not self.webhook_secret_hash:
            return False
        return hmac.compare_digest(
            self.webhook_secret_hash,
            hashlib.sha256(raw_secret.encode("utf-8")).hexdigest(),
        )

    def __str__(self) -> str:
        return f"{self.display_name} ({self.type}/{self.provider})"
