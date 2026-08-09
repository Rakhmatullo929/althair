from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from organizations.models import Organization, SUPPORTED_LANGUAGES, validate_supported_languages


def validate_plain_text(value: str) -> None:
    if "<" in value or ">" in value:
        raise ValidationError("HTML is not accepted in assistant context fields.")


def default_supported_languages() -> list[str]:
    return ["ru"]


class AssistantProfileStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"


class OrganizationAssistantProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.OneToOneField(
        Organization,
        on_delete=models.CASCADE,
        related_name="assistant_profile",
    )
    assistant_name = models.CharField(max_length=120, default="Assistant", validators=[validate_plain_text])
    status = models.CharField(
        max_length=20,
        choices=AssistantProfileStatus.choices,
        default=AssistantProfileStatus.DRAFT,
    )
    business_summary = models.TextField(max_length=1000, blank=True, validators=[validate_plain_text])
    business_description = models.TextField(max_length=6000, blank=True, validators=[validate_plain_text])
    target_customers = models.TextField(max_length=3000, blank=True, validators=[validate_plain_text])
    products_services = models.TextField(max_length=6000, blank=True, validators=[validate_plain_text])
    service_area = models.TextField(max_length=2000, blank=True, validators=[validate_plain_text])
    supported_languages = models.JSONField(default=default_supported_languages, validators=[validate_supported_languages])
    default_language = models.CharField(
        max_length=2,
        choices=[(item, item.upper()) for item in sorted(SUPPORTED_LANGUAGES)],
        default="ru",
    )
    tone_of_voice = models.CharField(max_length=500, blank=True, validators=[validate_plain_text])
    introduction = models.TextField(max_length=2000, blank=True, validators=[validate_plain_text])
    escalation_instructions = models.TextField(max_length=5000, blank=True, validators=[validate_plain_text])
    prohibited_topics = models.TextField(max_length=4000, blank=True, validators=[validate_plain_text])
    prohibited_actions = models.TextField(max_length=4000, blank=True, validators=[validate_plain_text])
    fallback_response = models.TextField(max_length=2000, blank=True, validators=[validate_plain_text])
    additional_instructions = models.TextField(max_length=6000, blank=True, validators=[validate_plain_text])
    version = models.PositiveIntegerField(default=0)
    published_snapshot = models.JSONField(default=dict, blank=True, editable=False)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assistant_profiles_updated",
    )

    class Meta:
        indexes = [models.Index(fields=["organization", "status"])]

    def clean(self):
        super().clean()
        validate_supported_languages(self.supported_languages)
        if self.default_language not in self.supported_languages:
            raise ValidationError({"default_language": "Default language must be supported."})


class AssistantContextRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="assistant_context_revisions",
    )
    profile = models.ForeignKey(
        OrganizationAssistantProfile,
        on_delete=models.CASCADE,
        related_name="revisions",
    )
    version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="assistant_context_revisions_published",
    )
    published_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "version"],
                name="unique_assistant_context_version_per_org",
            )
        ]
        indexes = [models.Index(fields=["organization", "published_at"])]
