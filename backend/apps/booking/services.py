from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from billing.services import BillingError, EntitlementService, record_usage
from booking.models import (
    Appointment,
    AppointmentEvent,
    AppointmentHold,
    AppointmentHoldResource,
    AppointmentReminder,
    AppointmentResource,
    BookableResource,
    BookableStaffProfile,
    BookingPolicy,
    PublicBookingProfile,
    ScheduleBreak,
    ScheduleException,
    ScheduleOwnerType,
    Service,
    StaffBranchAssignment,
    StaffService,
    WaitlistEntry,
    WeeklyScheduleRule,
    hash_public_token,
)
from crm.models import Contact, ContactIdentity, Conversation
from crm.services import ProviderUnavailable, record_activity, send_outbound_message
from organizations.models import Branch, OrganizationMembershipStatus


BLOCKING_APPOINTMENT_STATUSES = {
    Appointment.Status.PENDING_CONFIRMATION,
    Appointment.Status.CONFIRMED,
    Appointment.Status.CHECKED_IN,
    Appointment.Status.IN_PROGRESS,
}


class BookingError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 400, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


@dataclass(frozen=True)
class Slot:
    starts_at: datetime
    ends_at: datetime
    local_start: str
    local_end: str
    timezone: str
    fold: int
    staff_profile_id: str
    resource_allocations: tuple[tuple[str, int], ...]

    def as_dict(self) -> dict:
        return {
            "starts_at": self.starts_at.isoformat(),
            "ends_at": self.ends_at.isoformat(),
            "local_start": self.local_start,
            "local_end": self.local_end,
            "timezone": self.timezone,
            "fold": self.fold,
            "staff_profile_id": self.staff_profile_id,
            "resources": [
                {"resource_id": resource_id, "quantity": quantity}
                for resource_id, quantity in self.resource_allocations
            ],
        }


def _scoped(queryset, object_id, code="resource_not_found"):
    try:
        return queryset.get(pk=object_id)
    except (queryset.model.DoesNotExist, ValueError, ValidationError) as exc:
        raise BookingError(code, "The requested booking resource was not found.", status_code=404) from exc


def _require_booking(organization, feature="booking"):
    try:
        return EntitlementService(organization).require(feature)
    except BillingError as exc:
        raise BookingError(exc.code, str(exc), status_code=exc.status_code, details=exc.details) from exc


def resolve_policy(*, organization, branch, service) -> BookingPolicy:
    candidates = BookingPolicy.objects.for_organization(organization).filter(
        Q(branch=branch) | Q(branch__isnull=True),
        Q(service=service) | Q(service__isnull=True),
    )
    policy = (
        candidates.filter(branch=branch, service=service).first()
        or candidates.filter(branch=branch, service__isnull=True).first()
        or candidates.filter(branch__isnull=True, service=service).first()
        or candidates.filter(branch__isnull=True, service__isnull=True).first()
    )
    if policy:
        return policy
    return BookingPolicy(
        organization=organization,
        slot_interval_minutes=settings.BOOKING_DEFAULT_SLOT_INTERVAL_MINUTES,
        hold_ttl_seconds=settings.BOOKING_HOLD_TTL_SECONDS,
        reminder_schedule=list(settings.BOOKING_DEFAULT_REMINDER_MINUTES),
    )


def _owner_exists(organization, owner_type, owner_id) -> bool:
    querysets = {
        ScheduleOwnerType.BRANCH: Branch.objects.filter(organization=organization),
        ScheduleOwnerType.STAFF: BookableStaffProfile.objects.for_organization(organization),
        ScheduleOwnerType.RESOURCE: BookableResource.objects.for_organization(organization),
    }
    queryset = querysets.get(owner_type)
    return bool(queryset and queryset.filter(pk=owner_id).exists())


class ScheduleService:
    @staticmethod
    def validate_owner(*, organization, owner_type, owner_id):
        if not _owner_exists(organization, owner_type, owner_id):
            raise BookingError("schedule_owner_not_found", "Schedule owner was not found.", status_code=404)

    @staticmethod
    def local_candidates(day: date, local_time: time, timezone_name: str) -> list[datetime]:
        """Return valid UTC instants, preserving both folds and rejecting DST gaps."""
        zone = ZoneInfo(timezone_name)
        naive = datetime.combine(day, local_time)
        result = []
        for fold in (0, 1):
            candidate = naive.replace(tzinfo=zone, fold=fold)
            utc_candidate = candidate.astimezone(ZoneInfo("UTC"))
            if utc_candidate.astimezone(zone).replace(tzinfo=None) != naive:
                continue
            if utc_candidate not in result:
                result.append(utc_candidate)
        return sorted(result)


def _weekly_periods(*, organization, owner_type, owner_id, day, fallback=None):
    rules = WeeklyScheduleRule.objects.for_organization(organization).filter(
        owner_type=owner_type,
        owner_id=owner_id,
        weekday=day.weekday(),
        active=True,
    ).filter(Q(effective_from__isnull=True) | Q(effective_from__lte=day)).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=day)
    )
    periods = [(rule.start_local_time, rule.end_local_time) for rule in rules]
    if periods or fallback is None:
        return periods
    weekday_key = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[day.weekday()]
    result = []
    for item in fallback.get(weekday_key, []):
        try:
            result.append((time.fromisoformat(item["open"]), time.fromisoformat(item["close"])))
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _contains(periods, start: time, end: time) -> bool:
    return any(period_start <= start and end <= period_end for period_start, period_end in periods)


def _blocked_by_break(*, organization, owner_type, owner_id, day, start, end) -> bool:
    return ScheduleBreak.objects.for_organization(organization).filter(
        owner_type=owner_type,
        owner_id=owner_id,
    ).filter(Q(date=day) | Q(date__isnull=True, weekday=day.weekday())).filter(
        start_local_time__lt=end,
        end_local_time__gt=start,
    ).exists()


def _blocked_by_exception(*, organization, owner_type, owner_id, starts_at, ends_at) -> bool:
    exceptions = ScheduleException.objects.for_organization(organization).filter(
        owner_type=owner_type,
        owner_id=owner_id,
        starts_at__lt=ends_at,
        ends_at__gt=starts_at,
    )
    return exceptions.exclude(exception_type=ScheduleException.ExceptionType.AVAILABLE_OVERRIDE).exists()


def _available_override_covers(*, organization, owner_type, owner_id, starts_at, ends_at) -> bool:
    return ScheduleException.objects.for_organization(organization).filter(
        owner_type=owner_type,
        owner_id=owner_id,
        exception_type=ScheduleException.ExceptionType.AVAILABLE_OVERRIDE,
        starts_at__lte=starts_at,
        ends_at__gte=ends_at,
    ).exists()


def _has_weekly_rules(*, organization, owner_type, owner_id) -> bool:
    return WeeklyScheduleRule.objects.for_organization(organization).filter(
        owner_type=owner_type,
        owner_id=owner_id,
        active=True,
    ).exists()


def _owner_schedule_allows(
    *, organization, owner_type, owner_id, starts_at, ends_at, timezone_name, inherit_when_empty
) -> bool:
    if _available_override_covers(
        organization=organization,
        owner_type=owner_type,
        owner_id=owner_id,
        starts_at=starts_at,
        ends_at=ends_at,
    ):
        return True
    zone = ZoneInfo(timezone_name)
    local_start = starts_at.astimezone(zone)
    local_end = ends_at.astimezone(zone)
    if local_start.date() != local_end.date():
        return False
    periods = _weekly_periods(
        organization=organization,
        owner_type=owner_type,
        owner_id=owner_id,
        day=local_start.date(),
    )
    if not periods:
        return inherit_when_empty and not _has_weekly_rules(
            organization=organization,
            owner_type=owner_type,
            owner_id=owner_id,
        )
    return _contains(periods, local_start.time(), local_end.time())


def _branch_candidate_periods(*, organization, branch, day):
    periods = list(
        _weekly_periods(
            organization=organization,
            owner_type=ScheduleOwnerType.BRANCH,
            owner_id=branch.id,
            day=day,
            fallback=branch.working_hours,
        )
    )
    zone = ZoneInfo(branch.timezone)
    day_start = datetime.combine(day, time.min, tzinfo=zone)
    day_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    overrides = ScheduleException.objects.for_organization(organization).filter(
        owner_type=ScheduleOwnerType.BRANCH,
        owner_id=branch.id,
        exception_type=ScheduleException.ExceptionType.AVAILABLE_OVERRIDE,
        starts_at__lt=day_end,
        ends_at__gt=day_start,
    )
    for exception in overrides:
        local_start = max(exception.starts_at.astimezone(zone), day_start)
        local_end = min(exception.ends_at.astimezone(zone), day_end)
        if local_start.date() == day and local_end > local_start:
            periods.append((local_start.time(), time.max if local_end == day_end else local_end.time()))
    return sorted(set(periods))


def _appointment_overlap(queryset, starts_at, ends_at):
    return queryset.filter(
        status__in=BLOCKING_APPOINTMENT_STATUSES,
        starts_at__lt=ends_at,
        ends_at__gt=starts_at,
    )


def _hold_overlap(queryset, starts_at, ends_at, now):
    return queryset.filter(
        status=AppointmentHold.Status.ACTIVE,
        expires_at__gt=now,
        starts_at__lt=ends_at,
        ends_at__gt=starts_at,
    )


def _resource_capacity_available(*, organization, resource, starts_at, ends_at, quantity, now) -> bool:
    appointment_quantity = AppointmentResource.objects.for_organization(organization).filter(
        resource=resource,
        appointment__status__in=BLOCKING_APPOINTMENT_STATUSES,
        appointment__starts_at__lt=ends_at,
        appointment__ends_at__gt=starts_at,
    ).aggregate(total=Sum("quantity"))["total"] or 0
    hold_quantity = AppointmentHoldResource.objects.for_organization(organization).filter(
        resource=resource,
        hold__status=AppointmentHold.Status.ACTIVE,
        hold__expires_at__gt=now,
        hold__starts_at__lt=ends_at,
        hold__ends_at__gt=starts_at,
    ).aggregate(total=Sum("quantity"))["total"] or 0
    return appointment_quantity + hold_quantity + quantity <= resource.capacity


def _allocate_resources(*, organization, branch, service, starts_at, ends_at, now):
    allocations = []
    for requirement in service.resource_requirements.filter(required=True).select_related("specific_resource"):
        candidates = BookableResource.objects.for_organization(organization).filter(branch=branch, active=True)
        if requirement.specific_resource_id:
            candidates = candidates.filter(pk=requirement.specific_resource_id)
        else:
            candidates = candidates.filter(resource_type=requirement.resource_type)
        selected = next(
            (
                resource
                for resource in candidates.order_by("name", "id")
                if _owner_schedule_allows(
                    organization=organization,
                    owner_type=ScheduleOwnerType.RESOURCE,
                    owner_id=resource.id,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    timezone_name=branch.timezone,
                    inherit_when_empty=True,
                )
                and not _blocked_by_break(
                    organization=organization,
                    owner_type=ScheduleOwnerType.RESOURCE,
                    owner_id=resource.id,
                    day=starts_at.astimezone(ZoneInfo(branch.timezone)).date(),
                    start=starts_at.astimezone(ZoneInfo(branch.timezone)).time(),
                    end=ends_at.astimezone(ZoneInfo(branch.timezone)).time(),
                )
                and not _blocked_by_exception(
                    organization=organization,
                    owner_type=ScheduleOwnerType.RESOURCE,
                    owner_id=resource.id,
                    starts_at=starts_at,
                    ends_at=ends_at,
                )
                and _resource_capacity_available(
                    organization=organization,
                    resource=resource,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    quantity=requirement.quantity,
                    now=now,
                )
            ),
            None,
        )
        if not selected:
            return None
        allocations.append((str(selected.id), requirement.quantity))
    return tuple(allocations)


class AvailabilityService:
    def __init__(self, organization, *, now=None):
        self.organization = organization
        self.now = now or timezone.now()

    def slots(self, *, branch_id, service_id, date_from, date_to, staff_profile_id=None) -> list[Slot]:
        _require_booking(self.organization)
        branch = _scoped(
            Branch.objects.filter(organization=self.organization, is_active=True), branch_id, "branch_not_found"
        )
        service = _scoped(
            Service.objects.for_organization(self.organization).filter(active=True), service_id, "service_not_found"
        )
        if (date_to - date_from).days < 0 or (date_to - date_from).days >= settings.BOOKING_MAX_AVAILABILITY_DAYS:
            raise BookingError("availability_range_invalid", "Availability range must be between one and 31 days.")
        maximum = self.now + timedelta(days=service.maximum_advance_days)
        minimum = self.now + timedelta(minutes=service.minimum_notice_minutes)
        staff = BookableStaffProfile.objects.for_organization(self.organization).filter(
            active=True,
            accepts_online_booking=True,
            membership__status=OrganizationMembershipStatus.ACTIVE,
            branch_assignments__branch=branch,
            branch_assignments__organization=self.organization,
            supported_services__service=service,
            supported_services__active=True,
            supported_services__organization=self.organization,
        ).distinct()
        if staff_profile_id:
            staff = staff.filter(pk=staff_profile_id)
        zone = ZoneInfo(branch.timezone)
        policy = resolve_policy(organization=self.organization, branch=branch, service=service)
        slots = []
        day = date_from
        while day <= date_to:
            branch_periods = _branch_candidate_periods(
                organization=self.organization, branch=branch, day=day
            )
            for profile in staff.order_by("display_name", "id"):
                assignment = StaffService.objects.for_organization(self.organization).filter(
                    staff_profile=profile,
                    service=service,
                    active=True,
                ).first()
                duration_minutes = assignment.duration_override_minutes or service.duration_minutes
                for period_start, period_end in branch_periods:
                    cursor = datetime.combine(day, period_start)
                    last_start = datetime.combine(day, period_end) - timedelta(minutes=duration_minutes)
                    while cursor <= last_start:
                        candidates = ScheduleService.local_candidates(day, cursor.time(), branch.timezone)
                        for starts_at in candidates:
                            ends_at = starts_at + timedelta(minutes=duration_minutes)
                            if starts_at < minimum or ends_at > maximum:
                                continue
                            busy_start = starts_at - timedelta(minutes=service.buffer_before_minutes)
                            busy_end = ends_at + timedelta(minutes=service.buffer_after_minutes)
                            branch_busy_start = busy_start.astimezone(zone)
                            branch_busy_end = busy_end.astimezone(zone)
                            if branch_busy_start.date() != day or branch_busy_end.date() != day:
                                continue
                            if not _contains(
                                branch_periods,
                                branch_busy_start.time(),
                                branch_busy_end.time(),
                            ) and not _available_override_covers(
                                organization=self.organization,
                                owner_type=ScheduleOwnerType.BRANCH,
                                owner_id=branch.id,
                                starts_at=busy_start,
                                ends_at=busy_end,
                            ):
                                continue
                            staff_timezone = profile.timezone_override or branch.timezone
                            if not _owner_schedule_allows(
                                organization=self.organization,
                                owner_type=ScheduleOwnerType.STAFF,
                                owner_id=profile.id,
                                starts_at=busy_start,
                                ends_at=busy_end,
                                timezone_name=staff_timezone,
                                inherit_when_empty=True,
                            ):
                                continue
                            staff_zone = ZoneInfo(staff_timezone)
                            staff_busy_start = busy_start.astimezone(staff_zone)
                            staff_busy_end = busy_end.astimezone(staff_zone)
                            schedule_checks = (
                                (
                                    ScheduleOwnerType.BRANCH,
                                    branch.id,
                                    day,
                                    branch_busy_start.time(),
                                    branch_busy_end.time(),
                                ),
                                (
                                    ScheduleOwnerType.STAFF,
                                    profile.id,
                                    staff_busy_start.date(),
                                    staff_busy_start.time(),
                                    staff_busy_end.time(),
                                ),
                            )
                            if any(
                                _blocked_by_break(
                                    organization=self.organization,
                                    owner_type=owner_type,
                                    owner_id=owner_id,
                                    day=owner_day,
                                    start=owner_start,
                                    end=owner_end,
                                )
                                or _blocked_by_exception(
                                    organization=self.organization,
                                    owner_type=owner_type,
                                    owner_id=owner_id,
                                    starts_at=busy_start,
                                    ends_at=busy_end,
                                )
                                for owner_type, owner_id, owner_day, owner_start, owner_end in schedule_checks
                            ):
                                continue
                            appointment_count = _appointment_overlap(
                                Appointment.objects.for_organization(self.organization).filter(staff_profile=profile),
                                busy_start,
                                busy_end,
                            ).count()
                            hold_count = _hold_overlap(
                                AppointmentHold.objects.for_organization(self.organization).filter(staff_profile=profile),
                                busy_start,
                                busy_end,
                                self.now,
                            ).count()
                            if appointment_count + hold_count >= profile.maximum_concurrent_appointments:
                                continue
                            allocations = _allocate_resources(
                                organization=self.organization,
                                branch=branch,
                                service=service,
                                starts_at=busy_start,
                                ends_at=busy_end,
                                now=self.now,
                            )
                            if allocations is None:
                                continue
                            local_start = starts_at.astimezone(zone)
                            local_finish = ends_at.astimezone(zone)
                            slots.append(
                                Slot(
                                    starts_at=starts_at,
                                    ends_at=ends_at,
                                    local_start=local_start.isoformat(),
                                    local_end=local_finish.isoformat(),
                                    timezone=branch.timezone,
                                    fold=local_start.fold,
                                    staff_profile_id=str(profile.id),
                                    resource_allocations=allocations,
                                )
                            )
                        cursor += timedelta(minutes=policy.slot_interval_minutes)
            day += timedelta(days=1)
        unique = {(slot.starts_at, slot.staff_profile_id): slot for slot in slots}
        return sorted(unique.values(), key=lambda item: (item.starts_at, item.staff_profile_id))


def _lock_key(value) -> int:
    return int.from_bytes(hashlib.sha256(str(value).encode()).digest()[:8], "big", signed=True)


def _advisory_lock(*values):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        for value in sorted({_lock_key(value) for value in values}):
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [value])


def _matching_slot(*, organization, branch, service, starts_at, staff_profile_id, now):
    local_day = starts_at.astimezone(ZoneInfo(branch.timezone)).date()
    slots = AvailabilityService(organization, now=now).slots(
        branch_id=branch.id,
        service_id=service.id,
        date_from=local_day,
        date_to=local_day,
        staff_profile_id=staff_profile_id,
    )
    return next((slot for slot in slots if slot.starts_at == starts_at), None)


class AppointmentHoldService:
    @staticmethod
    @transaction.atomic
    def create(*, organization, branch_id, service_id, contact_id, starts_at, idempotency_key,
               staff_profile_id=None, created_by_type=AppointmentHold.CreatedByType.CUSTOMER, now=None):
        now = now or timezone.now()
        if not str(idempotency_key or "").strip():
            raise BookingError("idempotency_key_required", "An Idempotency-Key is required.")
        existing = AppointmentHold.objects.for_organization(organization).filter(
            idempotency_key=idempotency_key
        ).first()
        if existing:
            return existing, False
        branch = _scoped(Branch.objects.filter(organization=organization, is_active=True), branch_id)
        service = _scoped(Service.objects.for_organization(organization).filter(active=True), service_id)
        contact = _scoped(Contact.objects.for_organization(organization), contact_id)
        _advisory_lock(organization.id, branch.id, staff_profile_id or "auto", starts_at.isoformat())
        Branch.objects.select_for_update().get(pk=branch.pk)
        existing = AppointmentHold.objects.for_organization(organization).filter(
            idempotency_key=idempotency_key
        ).first()
        if existing:
            return existing, False
        slot = _matching_slot(
            organization=organization,
            branch=branch,
            service=service,
            starts_at=starts_at,
            staff_profile_id=staff_profile_id,
            now=now,
        )
        if not slot:
            raise BookingError("slot_unavailable", "This time is no longer available.", status_code=409)
        profile = BookableStaffProfile.objects.select_for_update().get(pk=slot.staff_profile_id)
        resource_ids = [item[0] for item in slot.resource_allocations]
        list(BookableResource.objects.select_for_update().filter(pk__in=resource_ids).order_by("pk"))
        policy = resolve_policy(organization=organization, branch=branch, service=service)
        hold = AppointmentHold(
            organization=organization,
            branch=branch,
            service=service,
            staff_profile=profile,
            contact=contact,
            starts_at=slot.starts_at,
            ends_at=slot.ends_at,
            expires_at=now + timedelta(seconds=policy.hold_ttl_seconds),
            idempotency_key=idempotency_key,
            created_by_type=created_by_type,
        )
        hold.full_clean()
        hold.save()
        for resource_id, quantity in slot.resource_allocations:
            AppointmentHoldResource.objects.create(
                organization=organization,
                hold=hold,
                resource_id=resource_id,
                quantity=quantity,
            )
        return hold, True


def _event(appointment, event_type, summary, *, actor_type, actor_membership=None, metadata=None):
    event = AppointmentEvent(
        organization=appointment.organization,
        appointment=appointment,
        event_type=event_type,
        actor_type=actor_type,
        actor_membership=actor_membership,
        summary=summary,
        metadata=metadata or {},
    )
    event.full_clean()
    event.save()
    return event


def _record_crm(appointment, event_type, summary, actor_membership=None):
    return record_activity(
        organization=appointment.organization,
        actor_membership=actor_membership,
        event_type=f"booking.{event_type}",
        summary=summary,
        contact=appointment.contact,
        conversation=appointment.source_conversation,
        metadata={"appointment_id": str(appointment.id), "public_reference": appointment.public_reference},
    )


def schedule_reminders(appointment, *, now=None):
    if not settings.BOOKING_REMINDERS_ENABLE:
        return
    try:
        _require_booking(appointment.organization, "appointment_reminders")
    except BookingError:
        return
    now = now or timezone.now()
    policy = resolve_policy(
        organization=appointment.organization, branch=appointment.branch, service=appointment.service
    )
    for minutes in sorted(set(policy.reminder_schedule), reverse=True):
        scheduled_for = appointment.starts_at - timedelta(minutes=minutes)
        if scheduled_for <= now:
            continue
        AppointmentReminder.objects.get_or_create(
            organization=appointment.organization,
            idempotency_key=f"appointment:{appointment.id}:upcoming:{minutes}:{appointment.starts_at.isoformat()}",
            defaults={
                "appointment": appointment,
                "reminder_type": AppointmentReminder.ReminderType.UPCOMING,
                "scheduled_for": scheduled_for,
            },
        )


class AppointmentService:
    @staticmethod
    @transaction.atomic
    def create_from_hold(*, organization, hold_id, idempotency_key, customer_timezone,
                         primary_identity_id=None, customer_notes="", created_by_membership=None,
                         source_conversation=None, source_message=None, now=None):
        now = now or timezone.now()
        if not str(idempotency_key or "").strip():
            raise BookingError("idempotency_key_required", "An Idempotency-Key is required.")
        _require_booking(organization, "monthly_booking_appointments")
        existing = Appointment.objects.for_organization(organization).filter(
            idempotency_key=idempotency_key
        ).first()
        if existing:
            return existing, False, None
        hold = _scoped(
            AppointmentHold.objects.select_for_update(of=("self",)).for_organization(organization).select_related(
                "branch", "service", "staff_profile", "contact"
            ),
            hold_id,
        )
        _advisory_lock(organization.id, hold.branch_id, hold.staff_profile_id, hold.starts_at.isoformat())
        if hold.status != AppointmentHold.Status.ACTIVE or hold.expires_at <= now:
            if hold.status == AppointmentHold.Status.ACTIVE:
                hold.status = AppointmentHold.Status.EXPIRED
                hold.save(update_fields=["status"])
            raise BookingError("hold_expired", "The temporary reservation has expired.", status_code=409)
        identity = None
        if primary_identity_id:
            identity = _scoped(
                ContactIdentity.objects.for_organization(organization).filter(contact=hold.contact),
                primary_identity_id,
            )
        policy = resolve_policy(organization=organization, branch=hold.branch, service=hold.service)
        manual_confirmation = hold.service.booking_mode == Service.BookingMode.MANUAL_ONLY
        requires_confirmation = (
            hold.service.booking_mode == Service.BookingMode.REQUIRE_CONFIRMATION
            or policy.require_customer_confirmation
        )
        pending = manual_confirmation or requires_confirmation
        token = secrets.token_urlsafe(32) if requires_confirmation and not manual_confirmation else None
        staff_service = StaffService.objects.for_organization(organization).filter(
            staff_profile=hold.staff_profile,
            service=hold.service,
            active=True,
        ).first()
        price_snapshot_minor = (
            staff_service.price_override_minor
            if staff_service and staff_service.price_override_minor is not None
            else hold.service.price_minor
        )
        appointment = Appointment(
            organization=organization,
            branch=hold.branch,
            service=hold.service,
            service_name_snapshot=hold.service.name,
            duration_snapshot_minutes=int((hold.ends_at - hold.starts_at).total_seconds() // 60),
            price_snapshot_minor=price_snapshot_minor,
            currency_snapshot=hold.service.currency,
            contact=hold.contact,
            primary_identity=identity,
            staff_profile=hold.staff_profile,
            source_channel_type=(source_conversation.channel_type if source_conversation else "public_web"),
            source_channel_connection=(source_conversation.channel_connection if source_conversation else None),
            source_conversation=source_conversation,
            source_message=source_message,
            starts_at=hold.starts_at,
            ends_at=hold.ends_at,
            customer_timezone=customer_timezone,
            status=(Appointment.Status.PENDING_CONFIRMATION if pending else Appointment.Status.CONFIRMED),
            confirmation_status=(Appointment.ConfirmationStatus.PENDING if pending else Appointment.ConfirmationStatus.NOT_REQUIRED),
            confirmation_token_hash=hash_public_token(token) if token else "",
            confirmation_expires_at=now + timedelta(hours=24) if token else None,
            customer_notes=customer_notes.strip(),
            created_by_type=hold.created_by_type,
            created_by_membership=created_by_membership,
            idempotency_key=idempotency_key,
            confirmed_at=None if pending else now,
        )
        appointment.full_clean()
        appointment.save()
        for link in hold.resource_links.select_related("resource"):
            AppointmentResource.objects.create(
                organization=organization,
                appointment=appointment,
                resource=link.resource,
                quantity=link.quantity,
            )
        hold.status = AppointmentHold.Status.CONVERTED
        hold.save(update_fields=["status"])
        _event(
            appointment,
            AppointmentEvent.EventType.CREATED,
            "Appointment created",
            actor_type=hold.created_by_type,
            actor_membership=created_by_membership,
        )
        _record_crm(appointment, "created", "Appointment created", created_by_membership)
        schedule_reminders(appointment, now=now)
        record_usage(
            organization=organization,
            meter_key="booking_appointments",
            quantity=1,
            unit="appointment",
            source_type="booking.appointment",
            source_id=str(appointment.id),
            idempotency_key=f"booking-created:{appointment.id}",
            occurred_at=now,
        )
        return appointment, True, token

    @staticmethod
    @transaction.atomic
    def confirm(*, organization, appointment_id, actor_type, actor_membership=None, token=None, now=None):
        now = now or timezone.now()
        appointment = _scoped(
            Appointment.objects.select_for_update().for_organization(organization), appointment_id
        )
        if appointment.status == Appointment.Status.CONFIRMED:
            return appointment, False
        if appointment.status != Appointment.Status.PENDING_CONFIRMATION:
            raise BookingError("invalid_status_transition", "Only pending appointments can be confirmed.", status_code=409)
        if actor_type == "customer" and appointment.service.booking_mode == Service.BookingMode.MANUAL_ONLY:
            raise BookingError(
                "manual_confirmation_required",
                "This appointment must be confirmed by the organization.",
                status_code=403,
            )
        if actor_type == "customer" and appointment.confirmation_token_hash and token is None:
            raise BookingError("invalid_confirmation_token", "Confirmation link is invalid.", status_code=403)
        if token is not None:
            if not appointment.confirmation_token_hash or not secrets.compare_digest(
                appointment.confirmation_token_hash, hash_public_token(token)
            ):
                raise BookingError("invalid_confirmation_token", "Confirmation link is invalid.", status_code=403)
            if not appointment.confirmation_expires_at or appointment.confirmation_expires_at <= now:
                raise BookingError("confirmation_expired", "Confirmation link has expired.", status_code=410)
        appointment.status = Appointment.Status.CONFIRMED
        appointment.confirmation_status = Appointment.ConfirmationStatus.CONFIRMED
        appointment.confirmed_at = now
        appointment.save(update_fields=["status", "confirmation_status", "confirmed_at", "updated_at"])
        _event(
            appointment,
            AppointmentEvent.EventType.CONFIRMED,
            "Appointment confirmed",
            actor_type=actor_type,
            actor_membership=actor_membership,
        )
        _record_crm(appointment, "confirmed", "Appointment confirmed", actor_membership)
        return appointment, True

    @staticmethod
    @transaction.atomic
    def reschedule(*, organization, appointment_id, starts_at, idempotency_key, actor_type,
                   actor_membership=None, customer=False, now=None):
        now = now or timezone.now()
        if not str(idempotency_key or "").strip():
            raise BookingError("idempotency_key_required", "An Idempotency-Key is required.")
        appointment = _scoped(
            Appointment.objects.select_for_update(of=("self",)).for_organization(organization).select_related(
                "branch", "service", "staff_profile", "contact"
            ),
            appointment_id,
        )
        if appointment.status not in BLOCKING_APPOINTMENT_STATUSES:
            raise BookingError("invalid_status_transition", "This appointment cannot be rescheduled.", status_code=409)
        policy = resolve_policy(organization=organization, branch=appointment.branch, service=appointment.service)
        if customer and not policy.allow_customer_reschedule:
            raise BookingError("reschedule_not_allowed", "Customer rescheduling is disabled.", status_code=403)
        hold, _ = AppointmentHoldService.create(
            organization=organization,
            branch_id=appointment.branch_id,
            service_id=appointment.service_id,
            contact_id=appointment.contact_id,
            starts_at=starts_at,
            staff_profile_id=appointment.staff_profile_id,
            idempotency_key=f"reschedule:{appointment.id}:{idempotency_key}",
            created_by_type=actor_type,
            now=now,
        )
        old = {"starts_at": appointment.starts_at.isoformat(), "ends_at": appointment.ends_at.isoformat()}
        appointment.starts_at = hold.starts_at
        appointment.ends_at = hold.ends_at
        appointment.resources.clear()
        for link in hold.resource_links.all():
            AppointmentResource.objects.create(
                organization=organization,
                appointment=appointment,
                resource=link.resource,
                quantity=link.quantity,
            )
        appointment.save(update_fields=["starts_at", "ends_at", "updated_at"])
        hold.status = AppointmentHold.Status.CONVERTED
        hold.save(update_fields=["status"])
        appointment.reminders.filter(status=AppointmentReminder.Status.SCHEDULED).update(
            status=AppointmentReminder.Status.CANCELLED
        )
        schedule_reminders(appointment, now=now)
        _event(
            appointment,
            AppointmentEvent.EventType.RESCHEDULED,
            "Appointment rescheduled",
            actor_type=actor_type,
            actor_membership=actor_membership,
            metadata={**old, "new_starts_at": appointment.starts_at.isoformat()},
        )
        _record_crm(appointment, "rescheduled", "Appointment rescheduled", actor_membership)
        return appointment

    @staticmethod
    @transaction.atomic
    def cancel(*, organization, appointment_id, reason, actor_type, actor_membership=None,
               customer=False, now=None):
        now = now or timezone.now()
        appointment = _scoped(
            Appointment.objects.select_for_update(of=("self",)).for_organization(organization).select_related("branch", "service"),
            appointment_id,
        )
        if appointment.status == Appointment.Status.CANCELLED:
            return appointment, False
        if appointment.status not in BLOCKING_APPOINTMENT_STATUSES:
            raise BookingError("invalid_status_transition", "This appointment cannot be cancelled.", status_code=409)
        policy = resolve_policy(organization=organization, branch=appointment.branch, service=appointment.service)
        notice = appointment.service.cancellation_notice_minutes or policy.default_cancellation_notice_minutes
        if customer and (not policy.allow_customer_cancel or appointment.starts_at - now < timedelta(minutes=notice)):
            raise BookingError("cancellation_window_closed", "Online cancellation is no longer available.", status_code=403)
        appointment.status = Appointment.Status.CANCELLED
        appointment.cancelled_at = now
        appointment.cancellation_reason = reason.strip()
        appointment.save(update_fields=["status", "cancelled_at", "cancellation_reason", "updated_at"])
        appointment.reminders.filter(status=AppointmentReminder.Status.SCHEDULED).update(
            status=AppointmentReminder.Status.CANCELLED
        )
        _event(
            appointment,
            AppointmentEvent.EventType.CANCELLED,
            "Appointment cancelled",
            actor_type=actor_type,
            actor_membership=actor_membership,
        )
        _record_crm(appointment, "cancelled", "Appointment cancelled", actor_membership)
        return appointment, True

    @staticmethod
    @transaction.atomic
    def set_status(*, organization, appointment_id, status, actor_membership):
        allowed = {
            Appointment.Status.CONFIRMED: {Appointment.Status.CHECKED_IN, Appointment.Status.NO_SHOW},
            Appointment.Status.CHECKED_IN: {Appointment.Status.IN_PROGRESS, Appointment.Status.NO_SHOW},
            Appointment.Status.IN_PROGRESS: {Appointment.Status.COMPLETED},
            Appointment.Status.PENDING_CONFIRMATION: {Appointment.Status.REJECTED},
        }
        appointment = _scoped(Appointment.objects.select_for_update().for_organization(organization), appointment_id)
        if status not in allowed.get(appointment.status, set()):
            raise BookingError("invalid_status_transition", "This status transition is not allowed.", status_code=409)
        appointment.status = status
        if status == Appointment.Status.COMPLETED:
            appointment.completed_at = timezone.now()
        appointment.save(update_fields=["status", "completed_at", "updated_at"])
        event_type = (
            AppointmentEvent.EventType.NO_SHOW if status == Appointment.Status.NO_SHOW
            else AppointmentEvent.EventType.COMPLETED if status == Appointment.Status.COMPLETED
            else AppointmentEvent.EventType.STATUS_CHANGED
        )
        _event(
            appointment,
            event_type,
            f"Appointment status changed to {status}",
            actor_type="employee",
            actor_membership=actor_membership,
        )
        _record_crm(appointment, "status_changed", f"Appointment status changed to {status}", actor_membership)
        return appointment


class WaitlistService:
    @staticmethod
    def create(*, organization, branch_id, service_id, contact_id, earliest_date, latest_date,
               preferred_staff_id=None, preferred_time_windows=None):
        _require_booking(organization, "booking_waitlist")
        branch = _scoped(Branch.objects.filter(organization=organization), branch_id)
        service = _scoped(Service.objects.for_organization(organization), service_id)
        contact = _scoped(Contact.objects.for_organization(organization), contact_id)
        preferred_staff = None
        if preferred_staff_id:
            preferred_staff = _scoped(BookableStaffProfile.objects.for_organization(organization), preferred_staff_id)
        entry = WaitlistEntry(
            organization=organization,
            branch=branch,
            service=service,
            contact=contact,
            preferred_staff=preferred_staff,
            earliest_date=earliest_date,
            latest_date=latest_date,
            preferred_time_windows=preferred_time_windows or [],
        )
        entry.full_clean()
        entry.save()
        return entry

    @staticmethod
    def offer_next(*, appointment):
        entries = WaitlistEntry.objects.select_for_update().for_organization(appointment.organization).filter(
            branch=appointment.branch,
            service=appointment.service,
            status=WaitlistEntry.Status.ACTIVE,
            earliest_date__lte=appointment.starts_at.date(),
            latest_date__gte=appointment.starts_at.date(),
        ).order_by("created_at")
        return entries.first()


class BookingReminderProvider:
    def send(self, reminder: AppointmentReminder):
        appointment = reminder.appointment
        conversations = Conversation.objects.for_organization(appointment.organization).filter(
            contact=appointment.contact,
            status="open",
        ).select_related("channel_connection").order_by("-last_message_at", "-created_at")
        policy = resolve_policy(
            organization=appointment.organization, branch=appointment.branch, service=appointment.service
        )
        ordered_channels = [reminder.preferred_channel, *policy.reminder_fallback_channels]
        ordered_channels = [item for item in ordered_channels if item]
        if ordered_channels:
            conversations = sorted(
                conversations,
                key=lambda conversation: (
                    ordered_channels.index(conversation.channel_type)
                    if conversation.channel_type in ordered_channels
                    else len(ordered_channels)
                ),
            )
        body = (
            f"Reminder: {appointment.service_name_snapshot} at "
            f"{appointment.starts_at.astimezone(ZoneInfo(appointment.customer_timezone)).strftime('%Y-%m-%d %H:%M %Z')}. "
            f"Reference {appointment.public_reference}."
        )
        for conversation in conversations:
            if conversation.channel_type == "sms":
                from sms.services import conversation_policy

                if not conversation_policy(conversation).get("can_send"):
                    continue
            try:
                return send_outbound_message(
                    organization=appointment.organization,
                    conversation=conversation,
                    membership=None,
                    body=body,
                    client_message_id=f"booking-reminder:{reminder.id}",
                )[0]
            except ProviderUnavailable:
                continue
        raise BookingError("no_eligible_reminder_channel", "No consented reminder channel is available.")


class DeterministicFakeReminderProvider(BookingReminderProvider):
    def send(self, reminder):
        return None


def reminder_provider():
    if settings.BOOKING_REMINDER_PROVIDER == "fake":
        return DeterministicFakeReminderProvider()
    return BookingReminderProvider()


@transaction.atomic
def deliver_due_reminder(reminder_id, *, now=None):
    now = now or timezone.now()
    reminder = AppointmentReminder.objects.select_for_update().select_related(
        "appointment__organization", "appointment__branch", "appointment__service", "appointment__contact"
    ).get(pk=reminder_id)
    if reminder.status in {AppointmentReminder.Status.SENT, AppointmentReminder.Status.CANCELLED}:
        return reminder, False
    if reminder.scheduled_for > now:
        return reminder, False
    if reminder.appointment.status not in BLOCKING_APPOINTMENT_STATUSES:
        reminder.status = AppointmentReminder.Status.SKIPPED
        reminder.save(update_fields=["status", "updated_at"])
        return reminder, False
    try:
        _require_booking(reminder.organization, "appointment_reminders")
    except BookingError as exc:
        reminder.status = AppointmentReminder.Status.SKIPPED
        reminder.last_error_code = exc.code
        reminder.save(update_fields=["status", "last_error_code", "updated_at"])
        return reminder, False
    reminder.status = AppointmentReminder.Status.QUEUED
    reminder.attempt_count += 1
    reminder.save(update_fields=["status", "attempt_count", "updated_at"])
    try:
        message = reminder_provider().send(reminder)
    except BookingError as exc:
        reminder.status = AppointmentReminder.Status.FAILED
        reminder.last_error_code = exc.code
        reminder.save(update_fields=["status", "last_error_code", "updated_at"])
        _event(
            reminder.appointment,
            AppointmentEvent.EventType.REMINDER_FAILED,
            "Appointment reminder failed",
            actor_type="system",
            metadata={"code": exc.code},
        )
        return reminder, False
    reminder.status = AppointmentReminder.Status.SENT
    reminder.provider_message = message
    reminder.last_error_code = ""
    reminder.save(update_fields=["status", "provider_message", "last_error_code", "updated_at"])
    _event(
        reminder.appointment,
        AppointmentEvent.EventType.REMINDER_SENT,
        "Appointment reminder sent",
        actor_type="system",
    )
    record_usage(
        organization=reminder.organization,
        meter_key="booking_reminders_sent",
        quantity=1,
        unit="reminder",
        source_type="booking.reminder",
        source_id=str(reminder.id),
        idempotency_key=f"booking-reminder-sent:{reminder.id}",
        occurred_at=now,
    )
    return reminder, True


class BookingAuditService:
    @staticmethod
    def timeline(appointment):
        return list(
            appointment.events.order_by("created_at").values(
                "event_type", "actor_type", "summary", "metadata", "created_at"
            )
        )


def public_profile(public_key):
    if not settings.BOOKING_ENABLE or not settings.BOOKING_PUBLIC_PAGE_ENABLE:
        raise BookingError("booking_page_not_found", "Booking page was not found.", status_code=404)
    try:
        profile = PublicBookingProfile.objects.select_related("organization").get(
            public_key=public_key, enabled=True
        )
    except PublicBookingProfile.DoesNotExist as exc:
        raise BookingError("booking_page_not_found", "Booking page was not found.", status_code=404) from exc
    _require_booking(profile.organization)
    _require_booking(profile.organization, "public_booking_page")
    return profile
