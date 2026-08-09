from __future__ import annotations

import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


SUPPORTED_LANGUAGES = frozenset({"ru", "uz", "en"})


def validate_timezone(value: str) -> None:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValidationError(_("Enter a valid IANA timezone.")) from exc


def validate_supported_languages(value: list[str]) -> None:
    if not isinstance(value, list) or not value:
        raise ValidationError(_("Supported languages must be a non-empty list."))
    if len(value) != len(set(value)) or any(item not in SUPPORTED_LANGUAGES for item in value):
        raise ValidationError(_("Supported languages may contain only ru, uz, and en."))


def validate_json_object(value: dict) -> None:
    if not isinstance(value, dict):
        raise ValidationError(_("This value must be a JSON object."))


class OrganizationStatus(models.TextChoices):
    TRIAL = "trial", _("Trial")
    ACTIVE = "active", _("Active")
    SUSPENDED = "suspended", _("Suspended")
    ARCHIVED = "archived", _("Archived")


class OrganizationIndustry(models.TextChoices):
    GENERIC = "generic", _("Generic")
    BEAUTY = "beauty", _("Beauty")
    BARBERSHOP = "barbershop", _("Barbershop")
    CLINIC = "clinic", _("Clinic")
    ECOMMERCE = "ecommerce", _("E-commerce")
    EDUCATION = "education", _("Education")
    AUTO_SERVICE = "auto_service", _("Auto service")
    REAL_ESTATE = "real_estate", _("Real estate")
    HOSPITALITY = "hospitality", _("Hospitality")
    OTHER = "other", _("Other")


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=80, unique=True)
    status = models.CharField(
        max_length=20,
        choices=OrganizationStatus.choices,
        default=OrganizationStatus.TRIAL,
        db_index=True,
    )
    industry = models.CharField(
        max_length=30,
        choices=OrganizationIndustry.choices,
        default=OrganizationIndustry.GENERIC,
        db_index=True,
    )
    default_language = models.CharField(
        max_length=2,
        choices=[(item, item.upper()) for item in sorted(SUPPORTED_LANGUAGES)],
        default="ru",
    )
    timezone = models.CharField(max_length=64, default="Asia/Tashkent", validators=[validate_timezone])
    logo = models.ImageField(upload_to="organizations/logos/", null=True, blank=True)
    logo_url = models.URLField(max_length=500, blank=True)
    settings = models.JSONField(default=dict, blank=True, validators=[validate_json_object])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name", "created_at"]
        indexes = [models.Index(fields=["status", "created_at"])]

    def clean(self) -> None:
        super().clean()
        if self.status == OrganizationStatus.ARCHIVED and not self.archived_at:
            self.archived_at = timezone.now()
        elif self.status != OrganizationStatus.ARCHIVED:
            self.archived_at = None

    def __str__(self) -> str:
        return self.name


class OrganizationMembershipRole(models.TextChoices):
    OWNER = "owner", _("Owner")
    ADMIN = "admin", _("Admin")
    MANAGER = "manager", _("Manager")
    AGENT = "agent", _("Agent")
    VIEWER = "viewer", _("Viewer")


class OrganizationMembershipStatus(models.TextChoices):
    INVITED = "invited", _("Invited")
    ACTIVE = "active", _("Active")
    SUSPENDED = "suspended", _("Suspended")


class OrganizationMembership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="organization_memberships")
    role = models.CharField(max_length=20, choices=OrganizationMembershipRole.choices)
    status = models.CharField(
        max_length=20,
        choices=OrganizationMembershipStatus.choices,
        default=OrganizationMembershipStatus.INVITED,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    joined_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "user"], name="unique_org_user_membership"),
        ]
        indexes = [
            models.Index(fields=["organization", "status", "role"]),
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} @ {self.organization_id} ({self.role})"


class OrganizationInvitationStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    ACCEPTED = "accepted", _("Accepted")
    REVOKED = "revoked", _("Revoked")
    EXPIRED = "expired", _("Expired")


class OrganizationInvitation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField(max_length=254)
    role = models.CharField(max_length=20, choices=OrganizationMembershipRole.choices)
    status = models.CharField(
        max_length=20,
        choices=OrganizationInvitationStatus.choices,
        default=OrganizationInvitationStatus.PENDING,
        db_index=True,
    )
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    expires_at = models.DateTimeField(db_index=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="organization_invitations_sent",
    )
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="organization_invitations_accepted",
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["organization", "email", "status"])]

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)


class Branch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="branches")
    name = models.CharField(max_length=160)
    address = models.CharField(max_length=500, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    timezone = models.CharField(max_length=64, default="Asia/Tashkent", validators=[validate_timezone])
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                condition=Q(is_active=True),
                name="unique_active_branch_name_per_org",
            ),
        ]
        indexes = [models.Index(fields=["organization", "is_active"])]

    def __str__(self) -> str:
        return f"{self.organization.name}: {self.name}"


class OrganizationProfileStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    PUBLISHED = "published", _("Published")


class OrganizationProfile(models.Model):
    organization = models.OneToOneField(
        Organization,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="profile",
    )
    public_business_name = models.CharField(max_length=160, blank=True)
    short_description = models.TextField(max_length=2000, blank=True)
    target_customers = models.TextField(max_length=3000, blank=True)
    products_services_summary = models.TextField(max_length=5000, blank=True)
    business_rules = models.TextField(max_length=5000, blank=True)
    preferred_communication_tone = models.CharField(max_length=500, blank=True)
    supported_languages = models.JSONField(default=list, validators=[validate_supported_languages])
    response_guidelines = models.TextField(max_length=5000, blank=True)
    escalation_instructions = models.TextField(max_length=5000, blank=True)
    public_contact_information = models.JSONField(default=dict, blank=True, validators=[validate_json_object])
    onboarding_completion_percentage = models.PositiveSmallIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    status = models.CharField(
        max_length=20,
        choices=OrganizationProfileStatus.choices,
        default=OrganizationProfileStatus.DRAFT,
    )
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)

    def clean(self) -> None:
        super().clean()
        validate_supported_languages(self.supported_languages)
        if self.status == OrganizationProfileStatus.PUBLISHED and not self.published_at:
            self.published_at = timezone.now()


class OrganizationOwnedQuerySet(models.QuerySet):
    def for_organization(self, organization: Organization | uuid.UUID):
        organization_id = getattr(organization, "pk", organization)
        return self.filter(organization_id=organization_id)


class OrganizationOwnedManager(models.Manager.from_queryset(OrganizationOwnedQuerySet)):
    pass


class OrganizationOwnedModel(models.Model):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)ss",
    )
    objects = OrganizationOwnedManager()

    class Meta:
        abstract = True
        indexes = [models.Index(fields=["organization"])]

    def validate_organization_relationships(self) -> None:
        if not self.organization_id:
            raise ValidationError({"organization": _("Organization is required.")})
        errors: dict[str, str] = {}
        for field in self._meta.concrete_fields:
            if not field.is_relation or field.name == "organization":
                continue
            related_id = getattr(self, field.attname, None)
            if related_id is None:
                continue
            related = getattr(self, field.name, None)
            related_org_id = getattr(related, "organization_id", None)
            if related_org_id is not None and related_org_id != self.organization_id:
                errors[field.name] = _("Related object belongs to another organization.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.validate_organization_relationships()
        return super().save(*args, **kwargs)
