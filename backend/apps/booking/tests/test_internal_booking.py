import hashlib
from datetime import timedelta

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from booking.models import (
    Appointment,
    AppointmentHold,
    AppointmentReminder,
    Service,
    WaitlistEntry,
)
from control_plane.models import PlatformAccessStatus, PlatformRole, PlatformSession, PlatformStaffAccess
from crm.services import create_contact
from organizations.models import Branch
from organizations.services import create_organization
from users.models import User


@override_settings(CONTROL_PLANE_ENABLE=True, CONTROL_PLANE_COOKIE_NAME="booking-internal-session")
class InternalBookingTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user(username="tenant-internal", password="pw12345!")
        self.organization = create_organization(creator=owner, name="Internal Booking Tenant", slug="internal-booking")
        membership = self.organization.memberships.get(user=owner)
        branch = Branch.objects.create(organization=self.organization, name="Internal branch", timezone="UTC")
        service = Service.objects.create(
            organization=self.organization, name="Internal service", duration_minutes=30
        )
        contact = create_contact(
            organization=self.organization, membership=membership, display_name="Hidden Customer"
        )
        starts_at = timezone.now() + timedelta(days=2)
        self.appointment = Appointment.objects.create(
            organization=self.organization,
            branch=branch,
            service=service,
            service_name_snapshot=service.name,
            duration_snapshot_minutes=30,
            contact=contact,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=30),
            customer_timezone="UTC",
            created_by_type="employee",
            created_by_membership=membership,
            idempotency_key="internal-appointment",
            internal_notes="Never expose this note",
        )
        AppointmentHold.objects.create(
            organization=self.organization,
            branch=branch,
            service=service,
            contact=contact,
            starts_at=starts_at + timedelta(days=1),
            ends_at=starts_at + timedelta(days=1, minutes=30),
            expires_at=timezone.now() + timedelta(minutes=5),
            idempotency_key="internal-hold",
            created_by_type="employee",
        )
        WaitlistEntry.objects.create(
            organization=self.organization,
            branch=branch,
            service=service,
            contact=contact,
            earliest_date=timezone.localdate(),
            latest_date=timezone.localdate() + timedelta(days=7),
        )
        AppointmentReminder.objects.create(
            organization=self.organization,
            appointment=self.appointment,
            reminder_type="upcoming",
            scheduled_for=timezone.now(),
            status="failed",
            idempotency_key="internal-reminder",
            last_error_code="fake_failure",
        )
        platform_user = User.objects.create_user(username="platform-booking", password="pw12345!")
        access = PlatformStaffAccess.objects.create(
            user=platform_user, role=PlatformRole.SUPPORT, status=PlatformAccessStatus.ACTIVE
        )
        raw = "synthetic-internal-booking-session"
        PlatformSession.objects.create(
            access=access,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            last_seen_at=timezone.now(),
            expires_at=timezone.now() + timedelta(hours=1),
            mfa_verified_at=timezone.now(),
        )
        self.client = APIClient()
        self.client.cookies[settings.CONTROL_PLANE_COOKIE_NAME] = raw

    def test_internal_views_expose_aggregates_without_customer_content(self):
        overview = self.client.get("/api/v1/internal/booking/overview/")
        self.assertEqual(overview.status_code, 200, overview.data)
        serialized = str(overview.data)
        self.assertNotIn("Hidden Customer", serialized)
        self.assertNotIn("Never expose", serialized)
        row = next(item for item in overview.data["results"] if item["organization_id"] == str(self.organization.id))
        self.assertEqual(row["appointments"], 1)
        self.assertEqual(row["failed_reminders"], 1)
        detail = self.client.get(f"/api/v1/internal/booking/organizations/{self.organization.id}/")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["appointments_total"], 1)
        self.assertEqual(detail.data["active_holds"], 1)
        self.assertEqual(detail.data["active_waitlist"], 1)
        self.assertNotIn("Never expose", str(detail.data))

    def test_internal_views_require_separate_platform_session(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/v1/internal/booking/overview/").status_code, 401)
        self.assertEqual(
            self.client.get("/api/v1/internal/booking/organizations/00000000-0000-0000-0000-000000000000/").status_code,
            401,
        )
