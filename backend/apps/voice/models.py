from __future__ import annotations

import re
import secrets
import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from assistant_context.models import AssistantContextRevision
from channels.models import ChannelConnection, ChannelType
from core.utils.encryption import EncryptedTextField
from crm.models import Contact, Conversation, validate_plain_text, validate_safe_metadata
from organizations.models import (
    Branch,
    OrganizationMembership,
    OrganizationOwnedModel,
    validate_json_object,
    validate_supported_languages,
    validate_working_hours,
)


def generate_webhook_key() -> str:
    return secrets.token_urlsafe(32)


def default_supported_languages() -> list[str]:
    return ["ru", "uz", "en"]


class VoiceCarrierType(models.TextChoices):
    TWILIO_SIP = "twilio_sip", "Twilio SIP"
    FAKE = "fake", "Deterministic fake"


class VoiceOwnershipMode(models.TextChoices):
    PLATFORM_MANAGED = "platform_managed", "Platform managed"
    CUSTOMER_OWNED = "customer_owned", "Customer owned"


class VoiceConnectionStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    CONNECTED = "connected", "Connected"
    DEGRADED = "degraded", "Degraded"
    PAUSED = "paused", "Paused"
    CREDENTIAL_INVALID = "credential_invalid", "Credential invalid"
    SIP_UNAVAILABLE = "sip_unavailable", "SIP unavailable"
    REALTIME_UNAVAILABLE = "realtime_unavailable", "Realtime unavailable"
    DISCONNECTED = "disconnected", "Disconnected"


class VoiceAIMode(models.TextChoices):
    MANUAL = "manual", "Manual"
    SUGGEST = "suggest", "Suggest"
    AUTOPILOT = "autopilot", "Autopilot"


class VoiceDisclosureMode(models.TextChoices):
    AI_DISCLOSURE = "ai_disclosure", "AI disclosure"
    AI_AND_TRANSCRIPT = "ai_and_transcript_disclosure", "AI and transcript disclosure"
    EXPLICIT_TRANSCRIPT = "explicit_transcript_consent", "Explicit transcript consent"


class VoiceTranscriptRetentionMode(models.TextChoices):
    DISABLED = "disabled", "Do not persist transcript"
    THIRTY_DAYS = "30_days", "30 days"
    NINETY_DAYS = "90_days", "90 days"
    INDEFINITE = "indefinite", "Until tenant deletion"


class VoiceConnection(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel_connection = models.OneToOneField(
        ChannelConnection, on_delete=models.PROTECT, related_name="voice_connection"
    )
    carrier = models.CharField(max_length=20, choices=VoiceCarrierType.choices, default=VoiceCarrierType.FAKE)
    ownership_mode = models.CharField(
        max_length=24, choices=VoiceOwnershipMode.choices, default=VoiceOwnershipMode.PLATFORM_MANAGED
    )
    status = models.CharField(
        max_length=28, choices=VoiceConnectionStatus.choices, default=VoiceConnectionStatus.DRAFT, db_index=True
    )
    phone_number_e164 = models.CharField(max_length=32, db_index=True)
    phone_number_sid = models.CharField(max_length=64, blank=True)
    sip_trunk_sid = models.CharField(max_length=64, blank=True)
    carrier_account_sid = models.CharField(max_length=64, blank=True)
    carrier_api_key_sid = models.CharField(max_length=64, blank=True)
    carrier_api_key_secret_encrypted = EncryptedTextField(blank=True, default="", editable=False)
    carrier_auth_token_encrypted = EncryptedTextField(blank=True, default="", editable=False)
    openai_project_id = models.CharField(max_length=120, blank=True)
    sip_destination = models.CharField(max_length=255, blank=True)
    webhook_public_key = models.CharField(max_length=64, unique=True, default=generate_webhook_key, editable=False)
    default_language = models.CharField(
        max_length=2, choices=[("ru", "RU"), ("uz", "UZ"), ("en", "EN")], default="ru"
    )
    supported_languages = models.JSONField(default=default_supported_languages)
    ai_mode = models.CharField(max_length=16, choices=VoiceAIMode.choices, default=VoiceAIMode.AUTOPILOT)
    realtime_model_alias = models.CharField(max_length=120, blank=True, validators=[validate_plain_text])
    voice_name = models.CharField(max_length=80, default="marin", validators=[validate_plain_text])
    reasoning_effort = models.CharField(
        max_length=12, choices=[("low", "Low"), ("medium", "Medium"), ("high", "High")], default="low"
    )
    greeting = models.CharField(max_length=1000, blank=True, validators=[validate_plain_text])
    business_hours_behavior = models.CharField(
        max_length=24,
        choices=[("accept", "Accept"), ("callback", "Callback"), ("reject", "Reject")],
        default="callback",
    )
    business_hours = models.JSONField(default=dict, blank=True, validators=[validate_working_hours])
    after_hours_message = models.CharField(max_length=1000, blank=True, validators=[validate_plain_text])
    disclosure_mode = models.CharField(
        max_length=40, choices=VoiceDisclosureMode.choices, default=VoiceDisclosureMode.AI_AND_TRANSCRIPT
    )
    transcript_retention_mode = models.CharField(
        max_length=20,
        choices=VoiceTranscriptRetentionMode.choices,
        default=VoiceTranscriptRetentionMode.THIRTY_DAYS,
    )
    recording_mode = models.CharField(max_length=16, default="disabled", editable=False)
    max_call_seconds = models.PositiveIntegerField(
        default=900, validators=[MinValueValidator(30), MaxValueValidator(3600)]
    )
    max_concurrent_calls = models.PositiveSmallIntegerField(
        default=3, validators=[MinValueValidator(1), MaxValueValidator(100)]
    )
    daily_minute_limit = models.PositiveIntegerField(default=300, validators=[MinValueValidator(1)])
    monthly_minute_limit = models.PositiveIntegerField(default=5000, validators=[MinValueValidator(1)])
    max_tools_per_call = models.PositiveSmallIntegerField(default=8, validators=[MinValueValidator(1), MaxValueValidator(30)])
    max_transfer_attempts = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(3)])
    last_inbound_at = models.DateTimeField(null=True, blank=True)
    last_call_at = models.DateTimeField(null=True, blank=True)
    last_health_check_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=80, blank=True)
    failure_count = models.PositiveSmallIntegerField(default=0)
    circuit_open_until = models.DateTimeField(null=True, blank=True)
    connected_by = models.ForeignKey(
        OrganizationMembership, on_delete=models.PROTECT, related_name="voice_connections_created"
    )
    connected_at = models.DateTimeField(null=True, blank=True)
    disconnected_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["phone_number_e164"],
                condition=~Q(status=VoiceConnectionStatus.DISCONNECTED),
                name="unique_active_voice_called_number",
            )
        ]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["phone_number_e164", "status"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.channel_connection_id:
            if self.channel_connection.organization_id != self.organization_id:
                errors["channel_connection"] = "Channel connection belongs to another organization."
            if self.channel_connection.type != ChannelType.VOICE:
                errors["channel_connection"] = "A Voice channel connection is required."
            expected = "twilio_sip" if self.carrier == VoiceCarrierType.TWILIO_SIP else "fake_voice"
            if self.channel_connection.provider != expected:
                errors["channel_connection"] = "The channel provider does not match the Voice carrier."
        if self.connected_by_id and self.connected_by.organization_id != self.organization_id:
            errors["connected_by"] = "Membership belongs to another organization."
        if not re.fullmatch(r"\+[1-9]\d{6,14}", self.phone_number_e164 or ""):
            errors["phone_number_e164"] = "Phone number must use E.164 format."
        if self.carrier_account_sid and not self.carrier_account_sid.startswith("AC"):
            errors["carrier_account_sid"] = "Twilio Account SID must start with AC."
        if self.sip_trunk_sid and not self.sip_trunk_sid.startswith("TK"):
            errors["sip_trunk_sid"] = "Twilio SIP Trunk SID must start with TK."
        try:
            validate_supported_languages(self.supported_languages)
        except ValidationError as exc:
            errors["supported_languages"] = exc.messages
        if self.default_language not in self.supported_languages:
            errors["default_language"] = "Default language must be supported."
        if self.recording_mode != "disabled":
            errors["recording_mode"] = "Audio recording is disabled in this stage."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values_list("organization_id", flat=True).first()
            if original and original != self.organization_id:
                raise ValidationError({"organization": "Organization is immutable."})
        return super().save(*args, **kwargs)


class VoiceTransferDestination(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    voice_connection = models.ForeignKey(
        VoiceConnection, on_delete=models.CASCADE, related_name="transfer_destinations"
    )
    key = models.SlugField(max_length=80)
    display_name = models.CharField(max_length=160, validators=[validate_plain_text])
    destination_type = models.CharField(max_length=8, choices=[("phone", "Phone"), ("sip", "SIP")])
    destination_encrypted = EncryptedTextField(editable=False)
    branch = models.ForeignKey(
        Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name="voice_transfer_destinations"
    )
    priority = models.PositiveSmallIntegerField(default=100)
    active = models.BooleanField(default=True, db_index=True)
    business_hours = models.JSONField(default=dict, blank=True, validators=[validate_working_hours])
    fallback_behavior = models.CharField(
        max_length=24,
        choices=[
            ("next_destination", "Next destination"),
            ("callback_task", "Callback task"),
            ("voicemail_message", "Voicemail message"),
            ("end_call", "End call"),
        ],
        default="callback_task",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "display_name"]
        constraints = [
            models.UniqueConstraint(fields=["voice_connection", "key"], name="unique_voice_transfer_key")
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.voice_connection_id and self.voice_connection.organization_id != self.organization_id:
            errors["voice_connection"] = "Voice connection belongs to another organization."
        if self.branch_id and self.branch.organization_id != self.organization_id:
            errors["branch"] = "Branch belongs to another organization."
        destination = str(self.destination_encrypted or "")
        if self.destination_type == "phone" and not re.fullmatch(r"\+[1-9]\d{6,14}", destination):
            errors["destination"] = "Phone transfer destinations must use E.164 format."
        if self.destination_type == "sip" and not destination.lower().startswith("sip:"):
            errors["destination"] = "SIP transfer destinations must start with sip:."
        if errors:
            raise ValidationError(errors)

    @property
    def target_uri(self) -> str:
        value = str(self.destination_encrypted)
        return f"tel:{value}" if self.destination_type == "phone" else value


class VoiceCallStatus(models.TextChoices):
    INCOMING = "incoming", "Incoming"
    ROUTING = "routing", "Routing"
    RINGING = "ringing", "Ringing"
    ACCEPTED = "accepted", "Accepted"
    ACTIVE = "active", "Active"
    TRANSFER_REQUESTED = "transfer_requested", "Transfer requested"
    TRANSFERRED = "transferred", "Transferred"
    COMPLETED = "completed", "Completed"
    REJECTED = "rejected", "Rejected"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class VoiceCall(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    voice_connection = models.ForeignKey(VoiceConnection, on_delete=models.PROTECT, related_name="calls")
    conversation = models.ForeignKey(Conversation, on_delete=models.PROTECT, related_name="voice_calls")
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="voice_calls")
    ai_context_revision = models.ForeignKey(
        AssistantContextRevision, on_delete=models.PROTECT, null=True, blank=True, related_name="voice_calls"
    )
    provider_call_id = models.CharField(max_length=160, unique=True)
    carrier_call_id = models.CharField(max_length=80, blank=True, db_index=True)
    direction = models.CharField(max_length=12, default="inbound", editable=False)
    caller_e164 = models.CharField(max_length=32, blank=True)
    caller_display = models.CharField(max_length=80, blank=True)
    called_e164 = models.CharField(max_length=32)
    status = models.CharField(max_length=28, choices=VoiceCallStatus.choices, default=VoiceCallStatus.INCOMING, db_index=True)
    rejection_reason = models.CharField(max_length=80, blank=True)
    selected_language = models.CharField(max_length=2, blank=True)
    ai_mode = models.CharField(max_length=16, choices=VoiceAIMode.choices)
    realtime_provider = models.CharField(max_length=24, default="fake")
    realtime_model = models.CharField(max_length=120, blank=True)
    voice_name = models.CharField(max_length=80, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    billable_seconds = models.PositiveIntegerField(null=True, blank=True)
    transfer_destination_key = models.CharField(max_length=80, blank=True)
    transfer_status = models.CharField(max_length=32, blank=True)
    hangup_actor = models.CharField(
        max_length=16,
        choices=[
            ("caller", "Caller"), ("ai", "AI"), ("employee", "Employee"),
            ("provider", "Provider"), ("system", "System"), ("unknown", "Unknown"),
        ],
        default="unknown",
    )
    consent_state = models.CharField(
        max_length=20,
        choices=[("not_required", "Not required"), ("pending", "Pending"), ("granted", "Granted"), ("declined", "Declined")],
        default="not_required",
    )
    consent_recorded_at = models.DateTimeField(null=True, blank=True)
    transcript_storage_allowed = models.BooleanField(default=True)
    disclosure_version = models.CharField(max_length=40, default="voice-disclosure-v1")
    summary = models.TextField(max_length=2000, blank=True, validators=[validate_plain_text])
    outcome = models.CharField(
        max_length=32,
        choices=[
            ("", "Pending"), ("answered", "Answered"), ("lead_created", "Lead created"),
            ("task_created", "Task created"), ("transferred", "Transferred"),
            ("callback_requested", "Callback requested"), ("abandoned", "Abandoned"),
            ("failed", "Failed"), ("rejected", "Rejected"),
        ],
        blank=True,
    )
    error_category = models.CharField(max_length=80, blank=True)
    ai_control_active = models.BooleanField(default=True)
    human_takeover_at = models.DateTimeField(null=True, blank=True)
    interruption_count = models.PositiveSmallIntegerField(default=0)
    unclear_turn_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "status", "-created_at"]),
            models.Index(fields=["organization", "contact", "-created_at"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        for field in ("voice_connection", "conversation", "contact"):
            value = getattr(self, field, None)
            if value and value.organization_id != self.organization_id:
                errors[field] = f"{field.replace('_', ' ').title()} belongs to another organization."
        if self.conversation_id and self.contact_id and self.conversation.contact_id != self.contact_id:
            errors["contact"] = "Contact does not match the conversation."
        if self.ai_context_revision_id and self.ai_context_revision.organization_id != self.organization_id:
            errors["ai_context_revision"] = "AI Context belongs to another organization."
        if errors:
            raise ValidationError(errors)


class VoiceTranscriptSegment(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    call = models.ForeignKey(VoiceCall, on_delete=models.CASCADE, related_name="transcript_segments")
    sequence = models.PositiveIntegerField()
    speaker = models.CharField(
        max_length=16,
        choices=[("caller", "Caller"), ("assistant", "Assistant"), ("employee", "Employee"), ("system", "System")],
    )
    text = models.TextField(max_length=4000, validators=[validate_plain_text])
    language = models.CharField(max_length=2, blank=True)
    start_ms = models.PositiveIntegerField(null=True, blank=True)
    end_ms = models.PositiveIntegerField(null=True, blank=True)
    final = models.BooleanField(default=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence", "created_at"]
        constraints = [models.UniqueConstraint(fields=["call", "sequence"], name="unique_voice_segment_sequence")]

    def clean(self):
        super().clean()
        if self.call_id and self.call.organization_id != self.organization_id:
            raise ValidationError({"call": "Call belongs to another organization."})
        if not self.final:
            raise ValidationError({"final": "Only final transcript segments may be persisted."})


class VoiceWebhookEnvelope(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(VoiceConnection, on_delete=models.CASCADE, related_name="webhook_envelopes")
    call = models.ForeignKey(VoiceCall, on_delete=models.SET_NULL, null=True, blank=True, related_name="webhook_envelopes")
    provider_event_id = models.CharField(max_length=160, unique=True)
    event_type = models.CharField(max_length=80)
    processing_status = models.CharField(
        max_length=16,
        choices=[("received", "Received"), ("processed", "Processed"), ("failed", "Failed")],
        default="received",
    )
    safe_metadata = models.JSONField(default=dict, blank=True, validators=[validate_safe_metadata])
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    redacted_error = models.CharField(max_length=80, blank=True)


class VoiceControllerJob(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    call = models.OneToOneField(VoiceCall, on_delete=models.CASCADE, related_name="controller_job")
    status = models.CharField(
        max_length=16,
        choices=[("pending", "Pending"), ("running", "Running"), ("completed", "Completed"), ("failed", "Failed")],
        default="pending",
        db_index=True,
    )
    attempt_count = models.PositiveSmallIntegerField(default=0)
    available_at = models.DateTimeField()
    locked_until = models.DateTimeField(null=True, blank=True)
    worker_id = models.CharField(max_length=120, blank=True)
    last_error_code = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class VoiceToolCall(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    call = models.ForeignKey(VoiceCall, on_delete=models.CASCADE, related_name="tool_calls")
    tool_name = models.CharField(max_length=80)
    provider_call_id = models.CharField(max_length=160)
    input_redacted = models.JSONField(default=dict, validators=[validate_safe_metadata])
    output_redacted = models.JSONField(default=dict, blank=True, validators=[validate_safe_metadata])
    status = models.CharField(
        max_length=20,
        choices=[("proposed", "Proposed"), ("running", "Running"), ("succeeded", "Succeeded"), ("failed", "Failed"), ("rejected", "Rejected")],
        default="proposed",
    )
    confirmation_marker = models.CharField(max_length=160, blank=True)
    idempotency_key = models.CharField(max_length=200, unique=True)
    duration_ms = models.PositiveIntegerField(default=0)
    error_category = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["call", "provider_call_id"], name="unique_voice_tool_provider_call")
        ]


class VoiceTransferAttempt(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    call = models.ForeignKey(VoiceCall, on_delete=models.CASCADE, related_name="transfer_attempts")
    destination = models.ForeignKey(
        VoiceTransferDestination, on_delete=models.PROTECT, related_name="transfer_attempts"
    )
    provider_transfer_id = models.CharField(max_length=160, blank=True)
    status = models.CharField(
        max_length=16,
        choices=[("requested", "Requested"), ("accepted", "Accepted"), ("failed", "Failed"), ("callback", "Callback")],
        default="requested",
    )
    error_category = models.CharField(max_length=80, blank=True)
    idempotency_key = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)


class VoiceUsageEvent(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    call = models.OneToOneField(VoiceCall, on_delete=models.CASCADE, related_name="usage_event")
    provider = models.CharField(max_length=24)
    model = models.CharField(max_length=120)
    input_audio_tokens = models.PositiveIntegerField(default=0)
    output_audio_tokens = models.PositiveIntegerField(default=0)
    input_text_tokens = models.PositiveIntegerField(default=0)
    output_text_tokens = models.PositiveIntegerField(default=0)
    duration_seconds = models.PositiveIntegerField(default=0)
    tool_successes = models.PositiveSmallIntegerField(default=0)
    tool_failures = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class VoiceCarrierStatusEvent(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(VoiceConnection, on_delete=models.CASCADE, related_name="carrier_events")
    call = models.ForeignKey(VoiceCall, on_delete=models.SET_NULL, null=True, blank=True, related_name="carrier_events")
    carrier_call_id = models.CharField(max_length=80, db_index=True)
    carrier_status = models.CharField(max_length=32)
    error_code = models.CharField(max_length=40, blank=True)
    event_key = models.CharField(max_length=160)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["connection", "event_key"], name="unique_voice_carrier_event")]


class VoiceAuditEvent(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(
        VoiceConnection, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_events"
    )
    call = models.ForeignKey(VoiceCall, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_events")
    actor_membership = models.ForeignKey(
        OrganizationMembership, on_delete=models.SET_NULL, null=True, blank=True, related_name="voice_audit_events"
    )
    event_type = models.CharField(max_length=80, db_index=True)
    metadata = models.JSONField(default=dict, blank=True, validators=[validate_safe_metadata])
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
