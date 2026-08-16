from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import time

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from channels.models import ChannelConnection
from crm.models import Contact, ContactIdentity, Conversation, Message, validate_plain_text
from organizations.models import (
    Branch,
    OrganizationMembership,
    OrganizationMembershipStatus,
    OrganizationOwnedModel,
    validate_json_object,
    validate_timezone,
)


def validate_currency(value: str) -> None:
    if value and (len(value) != 3 or not value.isalpha() or value != value.upper()):
        raise ValidationError("Currency must be an uppercase ISO 4217 code.")


def public_booking_key() -> str:
    return secrets.token_urlsafe(24)


def public_reference() -> str:
    return f"BK-{secrets.token_hex(6).upper()}"


def hash_public_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ServiceCategory(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160, validators=[validate_plain_text])
    description = models.TextField(max_length=2000, blank=True, validators=[validate_plain_text])
    position = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                condition=Q(active=True),
                name="booking_unique_active_category_name",
            )
        ]


class Service(OrganizationOwnedModel):
    class BookingMode(models.TextChoices):
        INSTANT = "instant", "Instant"
        REQUIRE_CONFIRMATION = "require_confirmation", "Require confirmation"
        MANUAL_ONLY = "manual_only", "Manual only"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.ForeignKey(
        ServiceCategory, on_delete=models.PROTECT, null=True, blank=True, related_name="services"
    )
    name = models.CharField(max_length=160, validators=[validate_plain_text])
    public_description = models.TextField(max_length=3000, blank=True, validators=[validate_plain_text])
    internal_description = models.TextField(max_length=3000, blank=True, validators=[validate_plain_text])
    duration_minutes = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(5), MaxValueValidator(1440)]
    )
    buffer_before_minutes = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(240)])
    buffer_after_minutes = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(240)])
    price_minor = models.PositiveBigIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True, validators=[validate_currency])
    booking_mode = models.CharField(
        max_length=24, choices=BookingMode.choices, default=BookingMode.INSTANT
    )
    customer_can_choose_staff = models.BooleanField(default=True)
    minimum_notice_minutes = models.PositiveIntegerField(default=60, validators=[MaxValueValidator(525600)])
    maximum_advance_days = models.PositiveSmallIntegerField(default=90, validators=[MaxValueValidator(730)])
    cancellation_notice_minutes = models.PositiveIntegerField(default=120, validators=[MaxValueValidator(525600)])
    active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                condition=Q(active=True),
                name="booking_unique_active_service_name",
            ),
            models.CheckConstraint(
                condition=Q(price_minor__isnull=True, currency="") | Q(price_minor__isnull=False),
                name="booking_service_currency_with_price",
            ),
        ]
        indexes = [models.Index(fields=["organization", "active", "name"])]

    def clean(self):
        super().clean()
        if self.category_id and self.category.organization_id != self.organization_id:
            raise ValidationError({"category": "Category belongs to another organization."})
        if self.price_minor is not None and not self.currency:
            raise ValidationError({"currency": "Currency is required when a price is provided."})


class BookableStaffProfile(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    membership = models.OneToOneField(
        OrganizationMembership, on_delete=models.PROTECT, related_name="bookable_profile"
    )
    display_name = models.CharField(max_length=160, validators=[validate_plain_text])
    branches = models.ManyToManyField(Branch, through="StaffBranchAssignment", related_name="bookable_staff")
    timezone_override = models.CharField(max_length=64, blank=True, validators=[validate_timezone])
    active = models.BooleanField(default=True, db_index=True)
    accepts_online_booking = models.BooleanField(default=True)
    maximum_concurrent_appointments = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(20)]
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name"]
        indexes = [models.Index(fields=["organization", "active", "accepts_online_booking"])]

    def clean(self):
        super().clean()
        if self.membership_id:
            if self.membership.organization_id != self.organization_id:
                raise ValidationError({"membership": "Membership belongs to another organization."})
            if self.active and self.membership.status != OrganizationMembershipStatus.ACTIVE:
                raise ValidationError({"membership": "Only active members can be bookable."})


class StaffBranchAssignment(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    staff_profile = models.ForeignKey(
        BookableStaffProfile, on_delete=models.CASCADE, related_name="branch_assignments"
    )
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="staff_assignments")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["staff_profile", "branch"], name="booking_unique_staff_branch")
        ]


class StaffService(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    staff_profile = models.ForeignKey(
        BookableStaffProfile, on_delete=models.CASCADE, related_name="supported_services"
    )
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="staff_assignments")
    duration_override_minutes = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(5), MaxValueValidator(1440)]
    )
    price_override_minor = models.PositiveBigIntegerField(null=True, blank=True)
    active = models.BooleanField(default=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["staff_profile", "service"], name="booking_unique_staff_service")
        ]
        indexes = [models.Index(fields=["organization", "service", "active"])]


class BookableResource(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="bookable_resources")
    name = models.CharField(max_length=160, validators=[validate_plain_text])
    resource_type = models.SlugField(max_length=80)
    capacity = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(100)])
    active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["resource_type", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "name"], condition=Q(active=True), name="booking_unique_active_resource"
            )
        ]
        indexes = [models.Index(fields=["organization", "branch", "resource_type", "active"])]


class ServiceResourceRequirement(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="resource_requirements")
    resource_type = models.SlugField(max_length=80, blank=True)
    specific_resource = models.ForeignKey(
        BookableResource, on_delete=models.PROTECT, null=True, blank=True, related_name="service_requirements"
    )
    quantity = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(20)])
    required = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(resource_type="", specific_resource__isnull=False) | Q(resource_type__gt="")),
                name="booking_requirement_has_resource_selector",
            )
        ]

    def clean(self):
        super().clean()
        if bool(self.resource_type) == bool(self.specific_resource_id):
            raise ValidationError("Choose exactly one resource type or specific resource.")


class ScheduleOwnerType(models.TextChoices):
    BRANCH = "branch", "Branch"
    STAFF = "staff", "Staff"
    RESOURCE = "resource", "Resource"


class WeeklyScheduleRule(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_type = models.CharField(max_length=12, choices=ScheduleOwnerType.choices)
    owner_id = models.UUIDField(db_index=True)
    weekday = models.PositiveSmallIntegerField(validators=[MinValueValidator(0), MaxValueValidator(6)])
    start_local_time = models.TimeField()
    end_local_time = models.TimeField()
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["owner_type", "owner_id", "weekday", "start_local_time"]
        indexes = [models.Index(fields=["organization", "owner_type", "owner_id", "weekday", "active"])]

    def clean(self):
        super().clean()
        if self.start_local_time >= self.end_local_time:
            raise ValidationError("Schedule start must be before end.")
        if self.effective_from and self.effective_to and self.effective_from > self.effective_to:
            raise ValidationError("Effective date range is invalid.")
        if not self.active or not self.organization_id or not self.owner_id:
            return
        overlapping = type(self).objects.filter(
            organization_id=self.organization_id,
            owner_type=self.owner_type,
            owner_id=self.owner_id,
            weekday=self.weekday,
            active=True,
            start_local_time__lt=self.end_local_time,
            end_local_time__gt=self.start_local_time,
        ).exclude(pk=self.pk)
        if self.effective_to:
            overlapping = overlapping.filter(
                Q(effective_from__isnull=True) | Q(effective_from__lte=self.effective_to)
            )
        if self.effective_from:
            overlapping = overlapping.filter(
                Q(effective_to__isnull=True) | Q(effective_to__gte=self.effective_from)
            )
        if overlapping.exists():
            raise ValidationError("Weekly schedule rules cannot overlap for the same owner.")


class ScheduleBreak(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_type = models.CharField(max_length=12, choices=ScheduleOwnerType.choices)
    owner_id = models.UUIDField(db_index=True)
    weekday = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(6)])
    date = models.DateField(null=True, blank=True)
    start_local_time = models.TimeField()
    end_local_time = models.TimeField()
    reason = models.CharField(max_length=500, blank=True, validators=[validate_plain_text])

    class Meta:
        indexes = [models.Index(fields=["organization", "owner_type", "owner_id", "date", "weekday"])]

    def clean(self):
        super().clean()
        if bool(self.date) == (self.weekday is not None):
            raise ValidationError("Choose either a weekday or a date for a break.")
        if self.start_local_time >= self.end_local_time:
            raise ValidationError("Break start must be before end.")


class ScheduleException(OrganizationOwnedModel):
    class ExceptionType(models.TextChoices):
        UNAVAILABLE = "unavailable", "Unavailable"
        AVAILABLE_OVERRIDE = "available_override", "Available override"
        HOLIDAY = "holiday", "Holiday"
        TIME_OFF = "time_off", "Time off"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_type = models.CharField(max_length=12, choices=ScheduleOwnerType.choices)
    owner_id = models.UUIDField(db_index=True)
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField(db_index=True)
    exception_type = models.CharField(max_length=24, choices=ExceptionType.choices)
    reason = models.CharField(max_length=500, blank=True, validators=[validate_plain_text])
    created_by = models.ForeignKey(
        OrganizationMembership, on_delete=models.PROTECT, related_name="booking_schedule_exceptions"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["organization", "owner_type", "owner_id", "starts_at", "ends_at"])]

    def clean(self):
        super().clean()
        if self.starts_at >= self.ends_at:
            raise ValidationError("Exception start must be before end.")


class BookingPolicy(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True, related_name="booking_policies")
    service = models.ForeignKey(Service, on_delete=models.CASCADE, null=True, blank=True, related_name="booking_policies")
    slot_interval_minutes = models.PositiveSmallIntegerField(default=30, validators=[MinValueValidator(5), MaxValueValidator(240)])
    default_minimum_notice_minutes = models.PositiveIntegerField(default=60, validators=[MaxValueValidator(525600)])
    default_maximum_advance_days = models.PositiveSmallIntegerField(default=90, validators=[MaxValueValidator(730)])
    default_cancellation_notice_minutes = models.PositiveIntegerField(default=120, validators=[MaxValueValidator(525600)])
    hold_ttl_seconds = models.PositiveSmallIntegerField(default=300, validators=[MinValueValidator(30), MaxValueValidator(1800)])
    allow_customer_reschedule = models.BooleanField(default=True)
    allow_customer_cancel = models.BooleanField(default=True)
    require_email_or_phone = models.BooleanField(default=True)
    require_customer_confirmation = models.BooleanField(default=False)
    auto_confirm_low_risk_actions = models.BooleanField(default=False)
    waitlist_enabled = models.BooleanField(default=True)
    reminder_schedule = models.JSONField(default=list, blank=True)
    reminder_fallback_channels = models.JSONField(default=list, blank=True)
    no_show_policy_text = models.TextField(max_length=3000, blank=True, validators=[validate_plain_text])
    cancellation_policy_text = models.TextField(max_length=3000, blank=True, validators=[validate_plain_text])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization"],
                condition=Q(branch__isnull=True, service__isnull=True),
                name="booking_one_org_default_policy",
            ),
            models.UniqueConstraint(
                fields=["organization", "branch"],
                condition=Q(branch__isnull=False, service__isnull=True),
                name="booking_one_branch_policy",
            ),
            models.UniqueConstraint(
                fields=["organization", "service"],
                condition=Q(branch__isnull=True, service__isnull=False),
                name="booking_one_service_policy",
            ),
            models.UniqueConstraint(
                fields=["organization", "branch", "service"],
                condition=Q(branch__isnull=False, service__isnull=False),
                name="booking_one_branch_service_policy",
            ),
        ]

    def clean(self):
        super().clean()
        if not isinstance(self.reminder_schedule, list) or not all(
            isinstance(item, int) and 0 < item <= 10080 for item in self.reminder_schedule
        ):
            raise ValidationError({"reminder_schedule": "Use a list of positive minutes, up to seven days."})
        if not isinstance(self.reminder_fallback_channels, list):
            raise ValidationError({"reminder_fallback_channels": "Fallback channels must be a list."})


class PublicBookingProfile(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    public_key = models.CharField(max_length=64, unique=True, default=public_booking_key, editable=False)
    enabled = models.BooleanField(default=False, db_index=True)
    title = models.CharField(max_length=160, blank=True, validators=[validate_plain_text])
    intro_text = models.TextField(max_length=2000, blank=True, validators=[validate_plain_text])
    privacy_url = models.URLField(blank=True)
    terms_url = models.URLField(blank=True)
    allowed_origins = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization"], name="booking_one_public_profile")]


class PublicBookingSession(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey(PublicBookingProfile, on_delete=models.CASCADE, related_name="sessions")
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    contact = models.ForeignKey(Contact, on_delete=models.SET_NULL, null=True, blank=True, related_name="booking_sessions")
    consented_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AppointmentHold(OrganizationOwnedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CONVERTED = "converted", "Converted"
        EXPIRED = "expired", "Expired"
        RELEASED = "released", "Released"

    class CreatedByType(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        EMPLOYEE = "employee", "Employee"
        AI = "ai", "AI"
        SYSTEM = "system", "System"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="appointment_holds")
    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="appointment_holds")
    staff_profile = models.ForeignKey(
        BookableStaffProfile, on_delete=models.PROTECT, null=True, blank=True, related_name="appointment_holds"
    )
    resources = models.ManyToManyField(BookableResource, through="AppointmentHoldResource", related_name="holds")
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="appointment_holds")
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField(db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    idempotency_key = models.CharField(max_length=200)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    created_by_type = models.CharField(max_length=16, choices=CreatedByType.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "idempotency_key"], name="booking_unique_hold_retry")
        ]
        indexes = [
            models.Index(fields=["organization", "staff_profile", "starts_at", "ends_at", "status"]),
            models.Index(fields=["organization", "status", "expires_at"]),
        ]

    def clean(self):
        super().clean()
        if self.starts_at >= self.ends_at:
            raise ValidationError("Hold start must be before end.")


class AppointmentHoldResource(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    hold = models.ForeignKey(AppointmentHold, on_delete=models.CASCADE, related_name="resource_links")
    resource = models.ForeignKey(BookableResource, on_delete=models.PROTECT, related_name="hold_links")
    quantity = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["hold", "resource"], name="booking_unique_hold_resource")]


class Appointment(OrganizationOwnedModel):
    class Status(models.TextChoices):
        PENDING_CONFIRMATION = "pending_confirmation", "Pending confirmation"
        CONFIRMED = "confirmed", "Confirmed"
        CHECKED_IN = "checked_in", "Checked in"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        NO_SHOW = "no_show", "No show"
        REJECTED = "rejected", "Rejected"

    class ConfirmationStatus(models.TextChoices):
        NOT_REQUIRED = "not_required", "Not required"
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    public_reference = models.CharField(max_length=40, unique=True, default=public_reference, editable=False)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="appointments")
    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="appointments")
    service_name_snapshot = models.CharField(max_length=160, validators=[validate_plain_text])
    duration_snapshot_minutes = models.PositiveSmallIntegerField()
    price_snapshot_minor = models.PositiveBigIntegerField(null=True, blank=True)
    currency_snapshot = models.CharField(max_length=3, blank=True, validators=[validate_currency])
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="appointments")
    primary_identity = models.ForeignKey(
        ContactIdentity, on_delete=models.PROTECT, null=True, blank=True, related_name="appointments"
    )
    staff_profile = models.ForeignKey(
        BookableStaffProfile, on_delete=models.PROTECT, null=True, blank=True, related_name="appointments"
    )
    resources = models.ManyToManyField(BookableResource, through="AppointmentResource", related_name="appointments")
    source_channel_type = models.CharField(max_length=24, blank=True, db_index=True)
    source_channel_connection = models.ForeignKey(
        ChannelConnection, on_delete=models.SET_NULL, null=True, blank=True, related_name="appointments"
    )
    source_conversation = models.ForeignKey(
        Conversation, on_delete=models.SET_NULL, null=True, blank=True, related_name="appointments"
    )
    source_message = models.ForeignKey(
        Message, on_delete=models.SET_NULL, null=True, blank=True, related_name="appointments"
    )
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField(db_index=True)
    customer_timezone = models.CharField(max_length=64, validators=[validate_timezone])
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.CONFIRMED, db_index=True)
    confirmation_status = models.CharField(
        max_length=16, choices=ConfirmationStatus.choices, default=ConfirmationStatus.NOT_REQUIRED
    )
    confirmation_token_hash = models.CharField(max_length=64, blank=True, editable=False)
    confirmation_expires_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.CharField(max_length=1000, blank=True, validators=[validate_plain_text])
    internal_notes = models.TextField(max_length=5000, blank=True, validators=[validate_plain_text])
    customer_notes = models.TextField(max_length=3000, blank=True, validators=[validate_plain_text])
    created_by_type = models.CharField(max_length=16, choices=AppointmentHold.CreatedByType.choices)
    created_by_membership = models.ForeignKey(
        OrganizationMembership, on_delete=models.PROTECT, null=True, blank=True, related_name="appointments_created"
    )
    idempotency_key = models.CharField(max_length=200)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["starts_at", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "idempotency_key"], name="booking_unique_appointment_retry")
        ]
        indexes = [
            models.Index(fields=["organization", "starts_at", "status"]),
            models.Index(fields=["organization", "staff_profile", "starts_at", "ends_at", "status"]),
            models.Index(fields=["organization", "contact", "-starts_at"]),
        ]

    def clean(self):
        super().clean()
        if self.starts_at >= self.ends_at:
            raise ValidationError("Appointment start must be before end.")
        if self.primary_identity_id and self.primary_identity.contact_id != self.contact_id:
            raise ValidationError({"primary_identity": "Identity belongs to another contact."})
        if self.price_snapshot_minor is not None and not self.currency_snapshot:
            raise ValidationError({"currency_snapshot": "Currency is required for a price snapshot."})


class AppointmentResource(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name="resource_links")
    resource = models.ForeignKey(BookableResource, on_delete=models.PROTECT, related_name="appointment_links")
    quantity = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["appointment", "resource"], name="booking_unique_appointment_resource")
        ]


class AppointmentEvent(OrganizationOwnedModel):
    class EventType(models.TextChoices):
        CREATED = "created", "Created"
        CONFIRMED = "confirmed", "Confirmed"
        RESCHEDULED = "rescheduled", "Rescheduled"
        CANCELLED = "cancelled", "Cancelled"
        STATUS_CHANGED = "status_changed", "Status changed"
        STAFF_CHANGED = "staff_changed", "Staff changed"
        RESOURCE_CHANGED = "resource_changed", "Resource changed"
        REMINDER_QUEUED = "reminder_queued", "Reminder queued"
        REMINDER_SENT = "reminder_sent", "Reminder sent"
        REMINDER_FAILED = "reminder_failed", "Reminder failed"
        CUSTOMER_REPLIED = "customer_replied", "Customer replied"
        NO_SHOW = "no_show", "No show"
        COMPLETED = "completed", "Completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=32, choices=EventType.choices, db_index=True)
    actor_type = models.CharField(max_length=16)
    actor_membership = models.ForeignKey(
        OrganizationMembership, on_delete=models.SET_NULL, null=True, blank=True, related_name="appointment_events"
    )
    summary = models.CharField(max_length=500, validators=[validate_plain_text])
    metadata = models.JSONField(default=dict, blank=True, validators=[validate_json_object])
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["organization", "appointment", "created_at"])]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Appointment events are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Appointment events are immutable.")


class WaitlistEntry(OrganizationOwnedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        OFFERED = "offered", "Offered"
        BOOKED = "booked", "Booked"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="waitlist_entries")
    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="waitlist_entries")
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="waitlist_entries")
    preferred_staff = models.ForeignKey(
        BookableStaffProfile, on_delete=models.PROTECT, null=True, blank=True, related_name="waitlist_entries"
    )
    earliest_date = models.DateField()
    latest_date = models.DateField()
    preferred_time_windows = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    offer_expires_at = models.DateTimeField(null=True, blank=True)
    offered_hold = models.ForeignKey(
        AppointmentHold, on_delete=models.SET_NULL, null=True, blank=True, related_name="waitlist_offers"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["organization", "branch", "service", "status", "earliest_date"])]

    def clean(self):
        super().clean()
        if self.earliest_date > self.latest_date:
            raise ValidationError("Waitlist date range is invalid.")


class AppointmentReminder(OrganizationOwnedModel):
    class ReminderType(models.TextChoices):
        CONFIRMATION = "confirmation", "Confirmation"
        UPCOMING = "upcoming", "Upcoming"
        CHANGED = "changed", "Changed"
        CANCELLED = "cancelled", "Cancelled"
        WAITLIST_OFFER = "waitlist_offer", "Waitlist offer"
        FOLLOW_UP = "follow_up", "Follow up"

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        SKIPPED = "skipped", "Skipped"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name="reminders")
    reminder_type = models.CharField(max_length=24, choices=ReminderType.choices)
    scheduled_for = models.DateTimeField(db_index=True)
    preferred_channel = models.CharField(max_length=24, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SCHEDULED, db_index=True)
    provider_message = models.ForeignKey(
        Message, on_delete=models.SET_NULL, null=True, blank=True, related_name="appointment_reminders"
    )
    idempotency_key = models.CharField(max_length=200)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    last_error_code = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "idempotency_key"], name="booking_unique_reminder_retry")
        ]
        indexes = [models.Index(fields=["organization", "status", "scheduled_for"])]
