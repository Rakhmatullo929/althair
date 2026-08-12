from __future__ import annotations

import secrets
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from channels.models import ChannelConnection, ChannelType
from core.utils.encryption import EncryptedTextField
from crm.models import ContactIdentity, Message, validate_plain_text, validate_safe_metadata
from organizations.models import OrganizationMembership, OrganizationOwnedModel, validate_supported_languages


def generate_webhook_key() -> str:
    return secrets.token_urlsafe(32)


def default_supported_languages() -> list[str]:
    return ["ru", "uz", "en"]


class SMSProviderType(models.TextChoices):
    TWILIO = "twilio", "Twilio"
    FAKE = "fake", "Deterministic fake"


class SMSOwnershipMode(models.TextChoices):
    PLATFORM_MANAGED = "platform_managed", "Platform managed"
    CUSTOMER_OWNED = "customer_owned", "Customer owned"


class SMSConnectionStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    CONNECTED = "connected", "Connected"
    DEGRADED = "degraded", "Degraded"
    PAUSED = "paused", "Paused"
    CREDENTIAL_INVALID = "credential_invalid", "Credential invalid"
    SENDER_UNAVAILABLE = "sender_unavailable", "Sender unavailable"
    WEBHOOK_ERROR = "webhook_error", "Webhook error"
    DISCONNECTED = "disconnected", "Disconnected"


class SMSAutomationMode(models.TextChoices):
    MANUAL = "manual", "Manual"
    SUGGEST = "suggest", "Suggest"
    AUTOPILOT = "autopilot", "Autopilot"


class SMSConnection(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel_connection = models.OneToOneField(
        ChannelConnection, on_delete=models.PROTECT, related_name="sms_connection"
    )
    provider = models.CharField(max_length=16, choices=SMSProviderType.choices, default=SMSProviderType.FAKE)
    ownership_mode = models.CharField(
        max_length=24, choices=SMSOwnershipMode.choices, default=SMSOwnershipMode.PLATFORM_MANAGED
    )
    status = models.CharField(
        max_length=24, choices=SMSConnectionStatus.choices, default=SMSConnectionStatus.DRAFT, db_index=True
    )
    account_sid = models.CharField(max_length=64, blank=True, db_index=True)
    messaging_service_sid = models.CharField(max_length=64, blank=True, db_index=True)
    phone_number_sid = models.CharField(max_length=64, blank=True)
    sender_address = models.CharField(max_length=64, db_index=True)
    sender_country = models.CharField(max_length=2, blank=True)
    sender_capabilities = models.JSONField(default=list, blank=True)
    api_key_sid = models.CharField(max_length=64, blank=True)
    api_key_secret_encrypted = EncryptedTextField(blank=True, default="", editable=False)
    auth_token_encrypted = EncryptedTextField(blank=True, default="", editable=False)
    webhook_public_key = models.CharField(
        max_length=64, unique=True, default=generate_webhook_key, editable=False
    )
    inbound_webhook_status = models.CharField(max_length=24, default="pending")
    status_callback_status = models.CharField(max_length=24, default="pending")
    advanced_opt_out_enabled = models.BooleanField(default=False)
    allow_inbound_support = models.BooleanField(default=True)
    default_language = models.CharField(
        max_length=2, choices=[("ru", "RU"), ("uz", "UZ"), ("en", "EN")], default="ru"
    )
    supported_languages = models.JSONField(default=default_supported_languages)
    ai_mode = models.CharField(max_length=16, choices=SMSAutomationMode.choices, default=SMSAutomationMode.MANUAL)
    last_inbound_at = models.DateTimeField(null=True, blank=True)
    last_send_at = models.DateTimeField(null=True, blank=True)
    last_status_callback_at = models.DateTimeField(null=True, blank=True)
    last_health_check_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=80, blank=True)
    failure_count = models.PositiveSmallIntegerField(default=0)
    circuit_open_until = models.DateTimeField(null=True, blank=True)
    connected_by = models.ForeignKey(
        OrganizationMembership, on_delete=models.PROTECT, related_name="sms_connections_created"
    )
    connected_at = models.DateTimeField(null=True, blank=True)
    disconnected_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "sender_address"],
                condition=~Q(status=SMSConnectionStatus.DISCONNECTED),
                name="unique_active_sms_sender",
            ),
            models.UniqueConstraint(
                fields=["organization"],
                condition=~Q(status=SMSConnectionStatus.DISCONNECTED),
                name="one_active_sms_connection_per_org",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["provider", "sender_address", "status"]),
            models.Index(fields=["messaging_service_sid", "status"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.channel_connection_id:
            if self.channel_connection.organization_id != self.organization_id:
                errors["channel_connection"] = "Channel connection belongs to another organization."
            if self.channel_connection.type != ChannelType.SMS:
                errors["channel_connection"] = "An SMS channel connection is required."
            expected_provider = "twilio" if self.provider == SMSProviderType.TWILIO else "fake_sms"
            if self.channel_connection.provider != expected_provider:
                errors["channel_connection"] = "The channel provider does not match the SMS provider."
        if self.connected_by_id and self.connected_by.organization_id != self.organization_id:
            errors["connected_by"] = "Membership belongs to another organization."
        if self.provider == SMSProviderType.TWILIO and not self.sender_address.startswith("+"):
            errors["sender_address"] = "Twilio SMS senders must use E.164 format."
        if self.messaging_service_sid and not self.messaging_service_sid.startswith("MG"):
            errors["messaging_service_sid"] = "Messaging Service SID must start with MG."
        if self.account_sid and not self.account_sid.startswith("AC"):
            errors["account_sid"] = "Account SID must start with AC."
        try:
            validate_supported_languages(self.supported_languages)
        except ValidationError as exc:
            errors["supported_languages"] = exc.messages
        if self.default_language not in self.supported_languages:
            errors["default_language"] = "Default language must be supported."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original_org = type(self).objects.filter(pk=self.pk).values_list("organization_id", flat=True).first()
            if original_org and original_org != self.organization_id:
                raise ValidationError({"organization": "Organization is immutable."})
        return super().save(*args, **kwargs)


class SMSConsentState(models.TextChoices):
    UNKNOWN = "unknown", "Unknown"
    IMPLIED_SUPPORT = "implied_support", "Inbound support"
    OPTED_IN = "opted_in", "Opted in"
    OPTED_OUT = "opted_out", "Opted out"
    BLOCKED = "blocked", "Blocked"
    INVALID = "invalid", "Invalid number"


class SMSConsentSource(models.TextChoices):
    INBOUND_MESSAGE = "inbound_message", "Inbound message"
    EXPLICIT_FORM = "explicit_form", "Explicit form"
    PROVIDER_OPT_OUT = "provider_opt_out", "Provider opt-out"
    PROVIDER_OPT_IN = "provider_opt_in", "Provider opt-in"
    EMPLOYEE = "employee", "Employee"
    IMPORT = "import", "Import"


class SMSConsent(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(SMSConnection, on_delete=models.CASCADE, related_name="consents")
    contact_identity = models.ForeignKey(ContactIdentity, on_delete=models.CASCADE, related_name="sms_consents")
    state = models.CharField(max_length=24, choices=SMSConsentState.choices, default=SMSConsentState.UNKNOWN, db_index=True)
    source = models.CharField(max_length=24, choices=SMSConsentSource.choices, default=SMSConsentSource.INBOUND_MESSAGE)
    last_keyword = models.CharField(max_length=32, blank=True, validators=[validate_plain_text])
    consented_at = models.DateTimeField(null=True, blank=True)
    opted_out_at = models.DateTimeField(null=True, blank=True)
    updated_by = models.ForeignKey(
        OrganizationMembership, on_delete=models.SET_NULL, null=True, blank=True, related_name="sms_consents_updated"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["connection", "contact_identity"], name="unique_sms_consent_identity")
        ]
        indexes = [models.Index(fields=["organization", "state", "updated_at"])]


class SMSWebhookProcessingStatus(models.TextChoices):
    RECEIVED = "received", "Received"
    PROCESSING = "processing", "Processing"
    PROCESSED = "processed", "Processed"
    FAILED = "failed", "Failed"


class SMSWebhookEnvelope(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(SMSConnection, on_delete=models.CASCADE, related_name="webhook_envelopes")
    provider_message_sid = models.CharField(max_length=64, db_index=True)
    event_type = models.CharField(max_length=16, choices=[("inbound", "Inbound"), ("status", "Status")])
    event_key = models.CharField(max_length=64)
    processing_status = models.CharField(
        max_length=16, choices=SMSWebhookProcessingStatus.choices, default=SMSWebhookProcessingStatus.RECEIVED, db_index=True
    )
    from_address = models.CharField(max_length=64, blank=True)
    to_address = models.CharField(max_length=64, blank=True)
    body = models.TextField(max_length=10000, blank=True, validators=[validate_plain_text])
    num_media = models.PositiveSmallIntegerField(default=0)
    messaging_service_sid = models.CharField(max_length=64, blank=True)
    opt_out_type = models.CharField(max_length=16, blank=True)
    provider_status = models.CharField(max_length=24, blank=True)
    provider_error_code = models.CharField(max_length=32, blank=True)
    provider_segments = models.PositiveSmallIntegerField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    redacted_error = models.CharField(max_length=80, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["connection", "event_key"], name="unique_sms_webhook_event")
        ]
        indexes = [
            models.Index(fields=["organization", "processing_status", "received_at"]),
            models.Index(fields=["connection", "provider_message_sid", "event_type"]),
        ]


class SMSOutboundAttemptStatus(models.TextChoices):
    CREATED = "created", "Created"
    SENDING = "sending", "Sending"
    ACCEPTED = "accepted", "Accepted"
    FAILED = "failed", "Failed"


class SMSOutboundAttempt(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(SMSConnection, on_delete=models.PROTECT, related_name="outbound_attempts")
    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name="sms_outbound_attempt")
    status = models.CharField(max_length=16, choices=SMSOutboundAttemptStatus.choices, default=SMSOutboundAttemptStatus.CREATED)
    provider_message_sid = models.CharField(max_length=64, blank=True, db_index=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    last_error_code = models.CharField(max_length=80, blank=True)
    retryable = models.BooleanField(default=False)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    segment_count_estimated = models.PositiveSmallIntegerField(default=1)
    encoding = models.CharField(max_length=16, default="GSM-7")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class SMSStatusEvent(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(SMSConnection, on_delete=models.CASCADE, related_name="status_events")
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="sms_status_events")
    provider_message_sid = models.CharField(max_length=64, db_index=True)
    provider_status = models.CharField(max_length=24)
    mapped_status = models.CharField(max_length=20)
    provider_error_code = models.CharField(max_length=32, blank=True)
    provider_segments = models.PositiveSmallIntegerField(null=True, blank=True)
    event_key = models.CharField(max_length=64)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["connection", "event_key"], name="unique_sms_status_event")
        ]
        indexes = [models.Index(fields=["organization", "provider_message_sid", "received_at"])]


class SMSAuditEvent(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(SMSConnection, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_events")
    actor_membership = models.ForeignKey(
        OrganizationMembership, on_delete=models.SET_NULL, null=True, blank=True, related_name="sms_audit_events"
    )
    event_type = models.CharField(max_length=80, db_index=True)
    metadata = models.JSONField(default=dict, blank=True, validators=[validate_safe_metadata])
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "event_type", "created_at"])]
