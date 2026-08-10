from __future__ import annotations

import secrets
import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from channels.models import ChannelConnection, ChannelStatus, ChannelType
from crm.models import Contact, Conversation, Message, validate_plain_text, validate_safe_metadata
from organizations.models import Branch, OrganizationMembership, OrganizationOwnedModel


def generate_public_key() -> str:
    return f"wc_live_{secrets.token_urlsafe(18)}"


def default_languages() -> list[str]:
    return ["ru", "uz", "en"]


def default_theme() -> dict:
    return {"accent": "emerald", "position": "right", "radius": "rounded"}


class InstallationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    REVOKED = "revoked", "Revoked"


class InstallationAIMode(models.TextChoices):
    MANUAL = "manual", "Manual"
    SUGGEST = "suggest", "Suggest"
    AUTOPILOT = "autopilot", "Autopilot"


class WebChatInstallation(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel_connection = models.OneToOneField(
        ChannelConnection, on_delete=models.PROTECT, related_name="web_chat_installation"
    )
    public_key = models.CharField(max_length=80, unique=True, default=generate_public_key, editable=False)
    status = models.CharField(max_length=20, choices=InstallationStatus.choices, default=InstallationStatus.DRAFT, db_index=True)
    display_name = models.CharField(max_length=160, default="Website chat", validators=[validate_plain_text])
    assistant_label = models.CharField(max_length=120, default="Assistant", validators=[validate_plain_text])
    greeting = models.CharField(max_length=500, default="Hello! How can we help?", validators=[validate_plain_text])
    offline_message = models.CharField(max_length=500, default="Our team will reply when available.", validators=[validate_plain_text])
    human_handoff_message = models.CharField(max_length=500, default="A team member will continue here.", validators=[validate_plain_text])
    privacy_policy_url = models.URLField(max_length=500, blank=True)
    terms_url = models.URLField(max_length=500, blank=True)
    consent_text = models.CharField(max_length=1000, default="I agree that this conversation may be stored to provide support.", validators=[validate_plain_text])
    consent_version = models.CharField(max_length=40, default="1", validators=[validate_plain_text])
    require_consent = models.BooleanField(default=True)
    require_prechat_form = models.BooleanField(default=False)
    collect_name = models.BooleanField(default=True)
    collect_email = models.BooleanField(default=False)
    collect_phone = models.BooleanField(default=False)
    default_language = models.CharField(max_length=2, choices=[("ru", "RU"), ("uz", "UZ"), ("en", "EN")], default="ru")
    supported_languages = models.JSONField(default=default_languages)
    default_branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name="web_chat_installations")
    allowed_origins = models.JSONField(default=list)
    theme_config = models.JSONField(default=default_theme, validators=[validate_safe_metadata])
    ai_mode = models.CharField(max_length=20, choices=InstallationAIMode.choices, default=InstallationAIMode.MANUAL)
    retention_days = models.PositiveSmallIntegerField(default=30, validators=[MinValueValidator(1), MaxValueValidator(365)])
    production_approved = models.BooleanField(default=False, editable=False)
    live_ai_opt_in = models.BooleanField(default=False)
    created_by = models.ForeignKey(OrganizationMembership, on_delete=models.PROTECT, related_name="web_chat_installations_created")
    updated_by = models.ForeignKey(OrganizationMembership, on_delete=models.PROTECT, related_name="web_chat_installations_updated")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "status", "-created_at"])]

    def clean(self):
        super().clean()
        errors = {}
        if self.channel_connection_id:
            if self.channel_connection.organization_id != self.organization_id:
                errors["channel_connection"] = "Channel connection belongs to another organization."
            if self.channel_connection.type != ChannelType.WEBCHAT or self.channel_connection.provider != "public_web_chat":
                errors["channel_connection"] = "A public Web Chat connection is required."
        for field in ("created_by", "updated_by"):
            value = getattr(self, field, None)
            if value and value.organization_id != self.organization_id:
                errors[field] = "Membership belongs to another organization."
        if self.default_branch_id and self.default_branch.organization_id != self.organization_id:
            errors["default_branch"] = "Branch belongs to another organization."
        languages = self.supported_languages
        if not isinstance(languages, list) or not languages or any(item not in {"ru", "uz", "en"} for item in languages):
            errors["supported_languages"] = "Choose one or more supported languages."
        if self.default_language not in languages:
            errors["default_language"] = "Default language must be supported."
        if not isinstance(self.allowed_origins, list):
            errors["allowed_origins"] = "Allowed origins must be a list."
        theme = self.theme_config
        if not isinstance(theme, dict) or set(theme) - {"accent", "position", "radius"}:
            errors["theme_config"] = "Only safe theme tokens are allowed."
        elif (
            theme.get("accent", "emerald") not in {"emerald", "forest", "teal"}
            or theme.get("position", "right") not in {"left", "right"}
            or theme.get("radius", "rounded") not in {"rounded", "soft", "square"}
        ):
            errors["theme_config"] = "Unsupported theme token."
        if errors:
            raise ValidationError(errors)

    def sync_connection_status(self):
        status = {
            InstallationStatus.ACTIVE: ChannelStatus.ACTIVE,
            InstallationStatus.DRAFT: ChannelStatus.DRAFT,
            InstallationStatus.PAUSED: ChannelStatus.DISCONNECTED,
            InstallationStatus.REVOKED: ChannelStatus.DISCONNECTED,
        }[self.status]
        ChannelConnection.objects.filter(pk=self.channel_connection_id).update(
            status=status, display_name=self.display_name, external_identifier=self.public_key
        )


class WebChatSessionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    HANDED_OFF = "handed_off", "Handed off"
    CLOSED = "closed", "Closed"
    EXPIRED = "expired", "Expired"
    BLOCKED = "blocked", "Blocked"


class WebChatSession(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    installation = models.ForeignKey(WebChatInstallation, on_delete=models.PROTECT, related_name="sessions")
    conversation = models.OneToOneField(Conversation, on_delete=models.SET_NULL, null=True, blank=True, related_name="web_chat_session")
    contact = models.ForeignKey(Contact, on_delete=models.SET_NULL, null=True, blank=True, related_name="web_chat_sessions")
    public_session_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    token_hash = models.CharField(max_length=64, editable=False)
    token_version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=WebChatSessionStatus.choices, default=WebChatSessionStatus.ACTIVE, db_index=True)
    consented_at = models.DateTimeField(null=True, blank=True)
    consent_version = models.CharField(max_length=40, blank=True)
    language = models.CharField(max_length=2, choices=[("ru", "RU"), ("uz", "UZ"), ("en", "EN")])
    origin = models.CharField(max_length=255)
    ip_hash = models.CharField(max_length=64, blank=True, editable=False)
    started_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    abuse_score = models.PositiveSmallIntegerField(default=0)
    first_message_at = models.DateTimeField(null=True, blank=True)
    first_response_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["organization", "status", "-started_at"]),
            models.Index(fields=["installation", "status", "expires_at"]),
        ]

    def clean(self):
        super().clean()
        if self.installation_id and self.installation.organization_id != self.organization_id:
            raise ValidationError({"installation": "Installation belongs to another organization."})
        if self.conversation_id and self.conversation.organization_id != self.organization_id:
            raise ValidationError({"conversation": "Conversation belongs to another organization."})
        if self.contact_id and self.contact.organization_id != self.organization_id:
            raise ValidationError({"contact": "Contact belongs to another organization."})


class WebChatEvent(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(WebChatSession, on_delete=models.CASCADE, related_name="events")
    sequence = models.PositiveBigIntegerField()
    event_type = models.CharField(max_length=40, db_index=True)
    message = models.ForeignKey(Message, on_delete=models.SET_NULL, null=True, blank=True, related_name="web_chat_events")
    safe_payload = models.JSONField(default=dict, blank=True, validators=[validate_safe_metadata])
    client_event_key = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(fields=["session", "sequence"], name="unique_web_chat_session_sequence"),
            models.UniqueConstraint(
                fields=["session", "client_event_key"],
                condition=~Q(client_event_key=""),
                name="unique_web_chat_client_event",
            ),
        ]
        indexes = [models.Index(fields=["session", "sequence"])]


class WebChatKeyRotation(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    installation = models.ForeignKey(WebChatInstallation, on_delete=models.CASCADE, related_name="key_rotations")
    previous_key_hash = models.CharField(max_length=64, editable=False)
    rotated_by = models.ForeignKey(OrganizationMembership, on_delete=models.PROTECT, related_name="web_chat_key_rotations")
    rotated_at = models.DateTimeField(auto_now_add=True)


class WebChatMetric(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    installation = models.ForeignKey(WebChatInstallation, on_delete=models.CASCADE, related_name="metrics")
    session = models.ForeignKey(WebChatSession, on_delete=models.SET_NULL, null=True, blank=True, related_name="metrics")
    event_type = models.CharField(max_length=40, db_index=True)
    safe_category = models.CharField(max_length=80, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["organization", "event_type", "-occurred_at"])]
