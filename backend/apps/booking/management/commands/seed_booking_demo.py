from datetime import time, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from booking.models import (
    AppointmentEvent,
    AppointmentHold,
    AppointmentReminder,
    BookableResource,
    BookableStaffProfile,
    BookingPolicy,
    PublicBookingProfile,
    Service,
    ServiceCategory,
    StaffBranchAssignment,
    StaffService,
    WaitlistEntry,
    WeeklyScheduleRule,
)
from booking.services import AppointmentHoldService, AppointmentService, AvailabilityService
from crm.models import Contact
from organizations.models import Organization, OrganizationMembership, OrganizationMembershipStatus


class Command(BaseCommand):
    help = "Seed deterministic Booking catalog and schedule data for local E2E."

    def handle(self, *args, **options):
        if not (settings.DEBUG and settings.TESTING):
            raise CommandError("seed_booking_demo requires DEBUG and E2E_TESTING.")
        organization = Organization.objects.filter(slug="mehr-clinic").first() or Organization.objects.first()
        if not organization:
            raise CommandError("Run seed_client_portal first.")
        branch = organization.branches.filter(is_active=True).first()
        membership = OrganizationMembership.objects.filter(
            organization=organization, status=OrganizationMembershipStatus.ACTIVE
        ).order_by("created_at").first()
        if not branch or not membership:
            raise CommandError("Booking demo needs an active branch and member.")
        category, _ = ServiceCategory.objects.get_or_create(
            organization=organization, name="Consultations", defaults={"position": 1}
        )
        service, _ = Service.objects.get_or_create(
            organization=organization,
            name="Initial consultation",
            defaults={
                "category": category,
                "public_description": "A focused 45-minute consultation.",
                "duration_minutes": 45,
                "buffer_after_minutes": 15,
                "price_minor": 15000000,
                "currency": "UZS",
                "minimum_notice_minutes": 60,
            },
        )
        staff, _ = BookableStaffProfile.objects.get_or_create(
            organization=organization,
            membership=membership,
            defaults={"display_name": membership.user.get_full_name() or "Amina Karimova"},
        )
        StaffBranchAssignment.objects.get_or_create(
            organization=organization, staff_profile=staff, branch=branch
        )
        StaffService.objects.get_or_create(
            organization=organization, staff_profile=staff, service=service
        )
        BookableResource.objects.get_or_create(
            organization=organization,
            branch=branch,
            name="Consultation room 1",
            defaults={"resource_type": "room", "capacity": 1},
        )
        for weekday in range(7):
            WeeklyScheduleRule.objects.get_or_create(
                organization=organization,
                owner_type="branch",
                owner_id=branch.id,
                weekday=weekday,
                start_local_time=time(9),
                end_local_time=time(18),
            )
            WeeklyScheduleRule.objects.get_or_create(
                organization=organization,
                owner_type="staff",
                owner_id=staff.id,
                weekday=weekday,
                start_local_time=time(9),
                end_local_time=time(17),
            )
        BookingPolicy.objects.get_or_create(
            organization=organization,
            defaults={"reminder_schedule": [1440, 120], "reminder_fallback_channels": ["sms", "gmail", "telegram"]},
        )
        PublicBookingProfile.objects.update_or_create(
            organization=organization,
            defaults={"enabled": True, "title": "Book with Mehr Clinic", "intro_text": "Choose a service and an available time."},
        )
        contact = Contact.objects.for_organization(organization).order_by("created_at").first()
        if not contact:
            contact = Contact.objects.create(
                organization=organization,
                display_name="E2E Booking Customer",
                preferred_language="en",
                timezone=branch.timezone,
                created_by=membership,
                updated_by=membership,
            )
        today = timezone.localdate()
        slots = AvailabilityService(organization).slots(
            branch_id=branch.id,
            service_id=service.id,
            date_from=today + timedelta(days=1),
            date_to=today + timedelta(days=7),
        )
        if slots:
            hold, _ = AppointmentHoldService.create(
                organization=organization,
                branch_id=branch.id,
                service_id=service.id,
                contact_id=contact.id,
                starts_at=slots[0].starts_at,
                staff_profile_id=slots[0].staff_profile_id,
                idempotency_key="e2e-booking-demo-hold",
                created_by_type=AppointmentHold.CreatedByType.EMPLOYEE,
            )
            appointment, _, _ = AppointmentService.create_from_hold(
                organization=organization,
                hold_id=hold.id,
                idempotency_key="e2e-booking-demo-appointment",
                customer_timezone=branch.timezone,
                created_by_membership=membership,
            )
            reminder = appointment.reminders.order_by("scheduled_for").first()
            if reminder:
                reminder.status = AppointmentReminder.Status.FAILED
                reminder.attempt_count = 2
                reminder.last_error_code = "fake_provider_failure"
                reminder.save(update_fields=["status", "attempt_count", "last_error_code", "updated_at"])
                AppointmentEvent.objects.get_or_create(
                    organization=organization,
                    appointment=appointment,
                    event_type=AppointmentEvent.EventType.REMINDER_FAILED,
                    defaults={
                        "actor_type": AppointmentHold.CreatedByType.SYSTEM,
                        "summary": "Deterministic reminder delivery failed",
                        "metadata": {"error_code": "fake_provider_failure"},
                    },
                )
        WaitlistEntry.objects.get_or_create(
            organization=organization,
            branch=branch,
            service=service,
            contact=contact,
            earliest_date=today + timedelta(days=8),
            latest_date=today + timedelta(days=14),
            defaults={"preferred_staff": staff, "preferred_time_windows": ["morning"]},
        )
        self.stdout.write(self.style.SUCCESS("Deterministic Booking demo is ready."))
