from __future__ import annotations

from datetime import date, datetime, time, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from ai_runtime.tools import TOOL_REGISTRY
from billing.models import UsageAggregate
from booking.models import (
    Appointment,
    AppointmentEvent,
    AppointmentHold,
    AppointmentReminder,
    BookableResource,
    BookableStaffProfile,
    BookingPolicy,
    PublicBookingProfile,
    ScheduleBreak,
    ScheduleException,
    ScheduleOwnerType,
    Service,
    ServiceResourceRequirement,
    StaffBranchAssignment,
    StaffService,
    WaitlistEntry,
    WeeklyScheduleRule,
)
from booking.services import (
    AppointmentHoldService,
    AppointmentService,
    AvailabilityService,
    BookingError,
    DeterministicFakeReminderProvider,
    ScheduleService,
    WaitlistService,
    deliver_due_reminder,
    resolve_policy,
)
from crm.models import Contact
from crm.services import create_contact
from control_plane.models import OrganizationEntitlement
from organizations.models import (
    Branch,
    OrganizationMembership,
    OrganizationMembershipRole,
    OrganizationMembershipStatus,
    OrganizationStatus,
)
from organizations.services import create_organization
from users.models import User
from voice.context import VOICE_SAFE_TOOLS


@override_settings(
    DEBUG=True,
    BOOKING_ENABLE=True,
    BOOKING_REMINDER_PROVIDER="fake",
    BOOKING_REMINDERS_ENABLE=True,
    BOOKING_DEFAULT_SLOT_INTERVAL_MINUTES=30,
    BOOKING_HOLD_TTL_SECONDS=300,
    BOOKING_DEFAULT_REMINDER_MINUTES=(1440, 120),
    BILLING_ENABLE=True,
    BILLING_PROVIDER="fake",
    BILLING_FAKE_PROVIDER=True,
)
class BookingTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="booking-owner", email="booking-owner@example.test", password="test-only-password-123!"
        )
        self.organization = create_organization(
            creator=self.owner,
            name="Booking Studio",
            slug="booking-studio",
            default_language="en",
        )
        self.membership = OrganizationMembership.objects.get(organization=self.organization, user=self.owner)
        self.branch = Branch.objects.create(
            organization=self.organization,
            name="Center",
            timezone="Asia/Tashkent",
            working_hours={key: [{"open": "09:00", "close": "18:00"}] for key in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")},
        )
        self.service = Service.objects.create(
            organization=self.organization,
            name="Consultation",
            duration_minutes=60,
            minimum_notice_minutes=0,
            maximum_advance_days=180,
            cancellation_notice_minutes=0,
            price_minor=100000,
            currency="UZS",
        )
        self.staff = BookableStaffProfile.objects.create(
            organization=self.organization,
            membership=self.membership,
            display_name="Amina",
        )
        StaffBranchAssignment.objects.create(
            organization=self.organization, staff_profile=self.staff, branch=self.branch
        )
        StaffService.objects.create(
            organization=self.organization, staff_profile=self.staff, service=self.service
        )
        self.contact = create_contact(
            organization=self.organization,
            membership=self.membership,
            display_name="Customer",
            timezone="Asia/Tashkent",
        )
        self.day = timezone.localdate() + timedelta(days=7)
        self.now = datetime.combine(self.day - timedelta(days=1), time(8), tzinfo=ZoneInfo("Asia/Tashkent"))
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    @property
    def headers(self):
        return {"HTTP_X_ORGANIZATION_ID": str(self.organization.id)}

    def slot(self, index=0):
        slots = AvailabilityService(self.organization, now=self.now).slots(
            branch_id=self.branch.id,
            service_id=self.service.id,
            date_from=self.day,
            date_to=self.day,
        )
        self.assertGreater(len(slots), index)
        return slots[index]

    def hold(self, key="hold-one", slot=None):
        slot = slot or self.slot()
        return AppointmentHoldService.create(
            organization=self.organization,
            branch_id=self.branch.id,
            service_id=self.service.id,
            contact_id=self.contact.id,
            starts_at=slot.starts_at,
            staff_profile_id=self.staff.id,
            idempotency_key=key,
            now=self.now,
        )[0]

    def appointment(self, key="appointment-one", slot=None):
        hold = self.hold(f"hold-{key}", slot=slot)
        return AppointmentService.create_from_hold(
            organization=self.organization,
            hold_id=hold.id,
            idempotency_key=key,
            customer_timezone="Asia/Tashkent",
            created_by_membership=self.membership,
            now=self.now,
        )[0]

    def test_catalog_and_snapshot_validation(self):
        self.assertEqual(self.service.currency, "UZS")
        invalid = Service(
            organization=self.organization,
            name="Invalid",
            duration_minutes=30,
            price_minor=100,
            currency="",
        )
        with self.assertRaises(ValidationError):
            invalid.full_clean()

    def test_cross_tenant_relationship_is_rejected(self):
        other_user = User.objects.create_user(username="other", email="other@example.test", password="pw12345!")
        other = create_organization(creator=other_user, name="Other", slug="other-booking")
        other_branch = Branch.objects.create(organization=other, name="Other")
        resource = BookableResource(
            organization=self.organization,
            branch=other_branch,
            name="Room",
            resource_type="room",
        )
        with self.assertRaises(ValidationError):
            resource.save()

    def test_dst_gap_is_rejected_and_fold_is_explicit(self):
        self.assertEqual(
            ScheduleService.local_candidates(date(2026, 3, 8), time(2, 30), "America/New_York"),
            [],
        )
        folds = ScheduleService.local_candidates(date(2026, 11, 1), time(1, 30), "America/New_York")
        self.assertEqual(len(folds), 2)
        self.assertNotEqual(folds[0], folds[1])

    def test_availability_uses_branch_schedule_and_precise_timezone(self):
        slot = self.slot()
        self.assertEqual(slot.timezone, "Asia/Tashkent")
        self.assertEqual(slot.starts_at.tzinfo, ZoneInfo("UTC"))
        self.assertEqual(slot.local_start[11:16], "09:00")

    def test_availability_query_count_is_bounded_for_one_day(self):
        with CaptureQueriesContext(connection) as captured:
            slots = AvailabilityService(self.organization, now=self.now).slots(
                branch_id=self.branch.id,
                service_id=self.service.id,
                date_from=self.day,
                date_to=self.day,
            )
        self.assertTrue(slots)
        self.assertLess(len(captured), 250)

    def test_staff_schedule_intersection_and_break(self):
        WeeklyScheduleRule.objects.create(
            organization=self.organization,
            owner_type=ScheduleOwnerType.STAFF,
            owner_id=self.staff.id,
            weekday=self.day.weekday(),
            start_local_time=time(10),
            end_local_time=time(12),
        )
        ScheduleBreak.objects.create(
            organization=self.organization,
            owner_type=ScheduleOwnerType.STAFF,
            owner_id=self.staff.id,
            date=self.day,
            start_local_time=time(10),
            end_local_time=time(11),
        )
        slots = AvailabilityService(self.organization, now=self.now).slots(
            branch_id=self.branch.id, service_id=self.service.id, date_from=self.day, date_to=self.day
        )
        self.assertEqual([item.local_start[11:16] for item in slots], ["11:00"])

    def test_buffers_fit_inside_working_hours(self):
        self.service.buffer_before_minutes = 30
        self.service.buffer_after_minutes = 30
        self.service.save(update_fields=["buffer_before_minutes", "buffer_after_minutes"])
        local_times = [item.local_start[11:16] for item in AvailabilityService(
            self.organization, now=self.now
        ).slots(branch_id=self.branch.id, service_id=self.service.id, date_from=self.day, date_to=self.day)]
        self.assertNotIn("09:00", local_times)
        self.assertNotIn("17:00", local_times)
        self.assertIn("09:30", local_times)

    def test_staff_schedule_uses_explicit_timezone_override(self):
        self.staff.timezone_override = "UTC"
        self.staff.save(update_fields=["timezone_override"])
        WeeklyScheduleRule.objects.create(
            organization=self.organization,
            owner_type=ScheduleOwnerType.STAFF,
            owner_id=self.staff.id,
            weekday=self.day.weekday(),
            start_local_time=time(4),
            end_local_time=time(5),
        )
        slots = AvailabilityService(self.organization, now=self.now).slots(
            branch_id=self.branch.id, service_id=self.service.id, date_from=self.day, date_to=self.day
        )
        self.assertEqual([item.local_start[11:16] for item in slots], ["09:00"])

    def test_staff_with_explicit_weekly_schedule_is_closed_on_unscheduled_day(self):
        other_day = self.day + timedelta(days=1)
        WeeklyScheduleRule.objects.create(
            organization=self.organization,
            owner_type=ScheduleOwnerType.STAFF,
            owner_id=self.staff.id,
            weekday=self.day.weekday(),
            start_local_time=time(9),
            end_local_time=time(18),
        )
        self.assertEqual(
            AvailabilityService(self.organization, now=self.now).slots(
                branch_id=self.branch.id,
                service_id=self.service.id,
                date_from=other_day,
                date_to=other_day,
            ),
            [],
        )

    def test_resource_schedule_is_enforced(self):
        room = BookableResource.objects.create(
            organization=self.organization,
            branch=self.branch,
            name="Scheduled room",
            resource_type="room",
        )
        ServiceResourceRequirement.objects.create(
            organization=self.organization,
            service=self.service,
            specific_resource=room,
        )
        WeeklyScheduleRule.objects.create(
            organization=self.organization,
            owner_type=ScheduleOwnerType.RESOURCE,
            owner_id=room.id,
            weekday=self.day.weekday(),
            start_local_time=time(10),
            end_local_time=time(12),
        )
        slots = AvailabilityService(self.organization, now=self.now).slots(
            branch_id=self.branch.id, service_id=self.service.id, date_from=self.day, date_to=self.day
        )
        self.assertEqual([item.local_start[11:16] for item in slots], ["10:00", "10:30", "11:00"])

    def test_available_override_opens_extra_time_but_holiday_wins(self):
        zone = ZoneInfo("Asia/Tashkent")
        override = ScheduleException.objects.create(
            organization=self.organization,
            owner_type=ScheduleOwnerType.BRANCH,
            owner_id=self.branch.id,
            starts_at=datetime.combine(self.day, time(19), tzinfo=zone),
            ends_at=datetime.combine(self.day, time(21), tzinfo=zone),
            exception_type=ScheduleException.ExceptionType.AVAILABLE_OVERRIDE,
            created_by=self.membership,
        )
        slots = AvailabilityService(self.organization, now=self.now).slots(
            branch_id=self.branch.id, service_id=self.service.id, date_from=self.day, date_to=self.day
        )
        self.assertIn("19:00", [item.local_start[11:16] for item in slots])
        ScheduleException.objects.create(
            organization=self.organization,
            owner_type=ScheduleOwnerType.BRANCH,
            owner_id=self.branch.id,
            starts_at=override.starts_at,
            ends_at=override.ends_at,
            exception_type=ScheduleException.ExceptionType.HOLIDAY,
            created_by=self.membership,
        )
        slots = AvailabilityService(self.organization, now=self.now).slots(
            branch_id=self.branch.id, service_id=self.service.id, date_from=self.day, date_to=self.day
        )
        self.assertNotIn("19:00", [item.local_start[11:16] for item in slots])

    def test_overlapping_weekly_rule_is_rejected(self):
        WeeklyScheduleRule.objects.create(
            organization=self.organization,
            owner_type=ScheduleOwnerType.STAFF,
            owner_id=self.staff.id,
            weekday=self.day.weekday(),
            start_local_time=time(9),
            end_local_time=time(12),
        )
        overlapping = WeeklyScheduleRule(
            organization=self.organization,
            owner_type=ScheduleOwnerType.STAFF,
            owner_id=self.staff.id,
            weekday=self.day.weekday(),
            start_local_time=time(11),
            end_local_time=time(13),
        )
        with self.assertRaisesMessage(ValidationError, "cannot overlap"):
            overlapping.full_clean()

    def test_unavailable_exception_blocks_slots(self):
        ScheduleException.objects.create(
            organization=self.organization,
            owner_type=ScheduleOwnerType.BRANCH,
            owner_id=self.branch.id,
            starts_at=datetime.combine(self.day, time.min, tzinfo=ZoneInfo("Asia/Tashkent")),
            ends_at=datetime.combine(self.day + timedelta(days=1), time.min, tzinfo=ZoneInfo("Asia/Tashkent")),
            exception_type=ScheduleException.ExceptionType.HOLIDAY,
            created_by=self.membership,
        )
        self.assertEqual(
            AvailabilityService(self.organization, now=self.now).slots(
                branch_id=self.branch.id, service_id=self.service.id, date_from=self.day, date_to=self.day
            ),
            [],
        )

    def test_hold_is_idempotent_and_blocks_second_customer(self):
        slot = self.slot()
        first, created = AppointmentHoldService.create(
            organization=self.organization,
            branch_id=self.branch.id,
            service_id=self.service.id,
            contact_id=self.contact.id,
            starts_at=slot.starts_at,
            idempotency_key="same-hold",
            now=self.now,
        )
        duplicate, duplicate_created = AppointmentHoldService.create(
            organization=self.organization,
            branch_id=self.branch.id,
            service_id=self.service.id,
            contact_id=self.contact.id,
            starts_at=slot.starts_at,
            idempotency_key="same-hold",
            now=self.now,
        )
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(first.id, duplicate.id)
        with self.assertRaisesMessage(BookingError, "no longer available"):
            self.hold("different-hold", slot=slot)

    def test_expired_hold_does_not_block_and_cannot_convert(self):
        slot = self.slot()
        hold = self.hold("expiring", slot)
        hold.expires_at = self.now - timedelta(seconds=1)
        hold.save(update_fields=["expires_at"])
        replacement = self.hold("replacement", slot)
        self.assertNotEqual(hold.id, replacement.id)
        with self.assertRaisesMessage(BookingError, "expired"):
            AppointmentService.create_from_hold(
                organization=self.organization,
                hold_id=hold.id,
                idempotency_key="expired-appointment",
                customer_timezone="Asia/Tashkent",
                now=self.now,
            )

    def test_resource_capacity_is_reserved(self):
        room = BookableResource.objects.create(
            organization=self.organization,
            branch=self.branch,
            name="Room 1",
            resource_type="room",
            capacity=1,
        )
        ServiceResourceRequirement.objects.create(
            organization=self.organization,
            service=self.service,
            specific_resource=room,
            quantity=1,
        )
        hold = self.hold("resource-hold")
        self.assertEqual(hold.resource_links.get().resource, room)
        self.assertFalse(any(slot.starts_at == hold.starts_at for slot in AvailabilityService(
            self.organization, now=self.now
        ).slots(branch_id=self.branch.id, service_id=self.service.id, date_from=self.day, date_to=self.day)))

    def test_create_appointment_snapshots_and_audits(self):
        appointment = self.appointment()
        self.assertEqual(appointment.service_name_snapshot, "Consultation")
        self.assertEqual(appointment.price_snapshot_minor, 100000)
        self.assertEqual(appointment.events.get().event_type, AppointmentEvent.EventType.CREATED)
        self.assertTrue(self.organization.crm_crmactivitys.filter(event_type="booking.created").exists())
        self.assertEqual(UsageAggregate.objects.get(meter_key="booking_appointments").quantity, 1)

    def test_staff_duration_and_price_overrides_are_snapshotted(self):
        assignment = StaffService.objects.get(staff_profile=self.staff, service=self.service)
        assignment.duration_override_minutes = 30
        assignment.price_override_minor = 75000
        assignment.save(update_fields=["duration_override_minutes", "price_override_minor"])
        appointment = self.appointment("staff-override")
        self.assertEqual(appointment.duration_snapshot_minutes, 30)
        self.assertEqual(appointment.price_snapshot_minor, 75000)

    def test_billing_limit_blocks_new_write_but_history_remains_readable(self):
        entitlement = OrganizationEntitlement.objects.get(organization=self.organization)
        entitlement.limit_overrides = {**entitlement.limit_overrides, "monthly_booking_appointments": 1}
        entitlement.override_reason = "Synthetic Booking limit test"
        entitlement.override_expires_at = timezone.now() + timedelta(hours=1)
        entitlement.save()
        existing = self.appointment("billing-first", self.slot(0))
        second_hold = self.hold("billing-second-hold", self.slot(2))
        with self.assertRaisesMessage(BookingError, "not available"):
            AppointmentService.create_from_hold(
                organization=self.organization,
                hold_id=second_hold.id,
                idempotency_key="billing-second",
                customer_timezone="Asia/Tashkent",
                now=self.now,
            )
        self.assertTrue(Appointment.objects.for_organization(self.organization).filter(pk=existing.id).exists())

    def test_confirmation_mode_returns_one_time_token(self):
        self.service.booking_mode = Service.BookingMode.REQUIRE_CONFIRMATION
        self.service.save(update_fields=["booking_mode"])
        hold = self.hold("confirmation-hold")
        appointment, _, token = AppointmentService.create_from_hold(
            organization=self.organization,
            hold_id=hold.id,
            idempotency_key="confirmation-appointment",
            customer_timezone="Asia/Tashkent",
            now=self.now,
        )
        self.assertEqual(appointment.status, Appointment.Status.PENDING_CONFIRMATION)
        self.assertTrue(token)
        self.assertNotEqual(appointment.confirmation_token_hash, token)
        confirmed, changed = AppointmentService.confirm(
            organization=self.organization,
            appointment_id=appointment.id,
            actor_type="customer",
            token=token,
            now=self.now,
        )
        self.assertTrue(changed)
        self.assertEqual(confirmed.status, Appointment.Status.CONFIRMED)

    def test_invalid_confirmation_token_fails_closed(self):
        self.service.booking_mode = Service.BookingMode.REQUIRE_CONFIRMATION
        self.service.save(update_fields=["booking_mode"])
        hold = self.hold("bad-confirmation-hold")
        appointment, _, _ = AppointmentService.create_from_hold(
            organization=self.organization,
            hold_id=hold.id,
            idempotency_key="bad-confirmation-appointment",
            customer_timezone="Asia/Tashkent",
            now=self.now,
        )
        with self.assertRaisesMessage(BookingError, "invalid"):
            AppointmentService.confirm(
                organization=self.organization,
                appointment_id=appointment.id,
                actor_type="customer",
                token="wrong",
                now=self.now,
            )
        with self.assertRaisesMessage(BookingError, "invalid"):
            AppointmentService.confirm(
                organization=self.organization,
                appointment_id=appointment.id,
                actor_type="customer",
                now=self.now,
            )

    def test_manual_only_booking_stays_pending_for_employee_review(self):
        self.service.booking_mode = Service.BookingMode.MANUAL_ONLY
        self.service.save(update_fields=["booking_mode"])
        appointment = self.appointment("manual-only")
        self.assertEqual(appointment.status, Appointment.Status.PENDING_CONFIRMATION)
        self.assertFalse(appointment.confirmation_token_hash)
        with self.assertRaisesMessage(BookingError, "organization"):
            AppointmentService.confirm(
                organization=self.organization,
                appointment_id=appointment.id,
                actor_type="customer",
                now=self.now,
            )
        appointment, changed = AppointmentService.confirm(
            organization=self.organization,
            appointment_id=appointment.id,
            actor_type="employee",
            actor_membership=self.membership,
            now=self.now,
        )
        self.assertTrue(changed)
        self.assertEqual(appointment.status, Appointment.Status.CONFIRMED)

    def test_lifecycle_status_machine(self):
        appointment = self.appointment("lifecycle")
        for status in (
            Appointment.Status.CHECKED_IN,
            Appointment.Status.IN_PROGRESS,
            Appointment.Status.COMPLETED,
        ):
            appointment = AppointmentService.set_status(
                organization=self.organization,
                appointment_id=appointment.id,
                status=status,
                actor_membership=self.membership,
            )
        self.assertIsNotNone(appointment.completed_at)
        with self.assertRaises(BookingError):
            AppointmentService.set_status(
                organization=self.organization,
                appointment_id=appointment.id,
                status=Appointment.Status.CONFIRMED,
                actor_membership=self.membership,
            )

    def test_cancel_releases_availability_and_is_idempotent(self):
        appointment = self.appointment("cancelled")
        AppointmentService.cancel(
            organization=self.organization,
            appointment_id=appointment.id,
            reason="Changed plans",
            actor_type="employee",
            now=self.now,
        )
        duplicate, changed = AppointmentService.cancel(
            organization=self.organization,
            appointment_id=appointment.id,
            reason="Changed plans",
            actor_type="employee",
            now=self.now,
        )
        self.assertFalse(changed)
        self.assertEqual(duplicate.status, Appointment.Status.CANCELLED)
        self.assertTrue(any(slot.starts_at == appointment.starts_at for slot in AvailabilityService(
            self.organization, now=self.now
        ).slots(branch_id=self.branch.id, service_id=self.service.id, date_from=self.day, date_to=self.day)))

    def test_reschedule_audits_old_and_new_time(self):
        appointment = self.appointment("reschedule", self.slot(0))
        target = self.slot(2)
        changed = AppointmentService.reschedule(
            organization=self.organization,
            appointment_id=appointment.id,
            starts_at=target.starts_at,
            idempotency_key="move-one",
            actor_type="employee",
            actor_membership=self.membership,
            now=self.now,
        )
        self.assertEqual(changed.starts_at, target.starts_at)
        self.assertTrue(changed.events.filter(event_type=AppointmentEvent.EventType.RESCHEDULED).exists())

    def test_policy_precedence(self):
        default = BookingPolicy.objects.create(organization=self.organization, slot_interval_minutes=30)
        branch = BookingPolicy.objects.create(
            organization=self.organization, branch=self.branch, slot_interval_minutes=15
        )
        self.assertEqual(resolve_policy(organization=self.organization, branch=self.branch, service=self.service), branch)
        service = BookingPolicy.objects.create(
            organization=self.organization, branch=self.branch, service=self.service, slot_interval_minutes=10
        )
        self.assertEqual(resolve_policy(organization=self.organization, branch=self.branch, service=self.service), service)
        self.assertNotEqual(default, service)

    def test_waitlist_is_tenant_scoped_and_validated(self):
        entry = WaitlistService.create(
            organization=self.organization,
            branch_id=self.branch.id,
            service_id=self.service.id,
            contact_id=self.contact.id,
            earliest_date=self.day,
            latest_date=self.day + timedelta(days=2),
        )
        self.assertEqual(entry.status, WaitlistEntry.Status.ACTIVE)
        with self.assertRaises(ValidationError):
            WaitlistService.create(
                organization=self.organization,
                branch_id=self.branch.id,
                service_id=self.service.id,
                contact_id=self.contact.id,
                earliest_date=self.day,
                latest_date=self.day - timedelta(days=1),
            )

    def test_reminders_are_idempotent_and_fake_provider_is_deterministic(self):
        BookingPolicy.objects.create(
            organization=self.organization,
            reminder_schedule=[120],
        )
        appointment = self.appointment("reminder")
        reminder = appointment.reminders.get()
        reminder.scheduled_for = self.now
        reminder.save(update_fields=["scheduled_for"])
        delivered, sent = deliver_due_reminder(reminder.id, now=self.now)
        duplicate, sent_again = deliver_due_reminder(reminder.id, now=self.now)
        self.assertTrue(sent)
        self.assertFalse(sent_again)
        self.assertEqual(delivered.status, AppointmentReminder.Status.SENT)
        self.assertEqual(duplicate.id, delivered.id)
        self.assertIsNone(DeterministicFakeReminderProvider().send(reminder))

    def test_sms_opt_out_blocks_reminder_before_provider_send(self):
        from unittest.mock import patch

        from booking.services import BookingReminderProvider
        from channels.models import ChannelConnection, ChannelStatus, ChannelType
        from crm.models import Conversation

        connection_row = ChannelConnection.objects.create(
            organization=self.organization,
            branch=self.branch,
            type=ChannelType.SMS,
            provider="fake_twilio",
            display_name="Booking SMS",
            external_identifier="+998900000099",
            status=ChannelStatus.ACTIVE,
        )
        Conversation.objects.create(
            organization=self.organization,
            channel_connection=connection_row,
            channel_type=ChannelType.SMS,
            external_thread_id="booking-reminder-opt-out",
            contact=self.contact,
        )
        appointment = self.appointment("opt-out-reminder")
        reminder = AppointmentReminder.objects.create(
            organization=self.organization,
            appointment=appointment,
            reminder_type=AppointmentReminder.ReminderType.UPCOMING,
            scheduled_for=self.now,
            preferred_channel="sms",
            idempotency_key="opt-out-reminder",
        )
        with patch("sms.services.conversation_policy", return_value={"can_send": False}), patch(
            "booking.services.send_outbound_message"
        ) as sender:
            with self.assertRaisesMessage(BookingError, "No consented"):
                BookingReminderProvider().send(reminder)
            sender.assert_not_called()

    @override_settings(BOOKING_REMINDER_PROVIDER="provider")
    def test_reminder_failure_is_visible_and_does_not_claim_sent(self):
        appointment = self.appointment("failed-reminder")
        reminder = AppointmentReminder.objects.create(
            organization=self.organization,
            appointment=appointment,
            reminder_type=AppointmentReminder.ReminderType.UPCOMING,
            scheduled_for=self.now,
            idempotency_key="failed-reminder-due",
        )
        delivered, sent = deliver_due_reminder(reminder.id, now=self.now)
        self.assertFalse(sent)
        self.assertEqual(delivered.status, AppointmentReminder.Status.FAILED)
        self.assertEqual(delivered.last_error_code, "no_eligible_reminder_channel")
        self.assertTrue(appointment.events.filter(event_type="reminder_failed").exists())

    def test_background_tasks_expire_holds_and_deliver_due_reminders(self):
        from booking.tasks import deliver_booking_reminders, expire_booking_holds

        hold = self.hold("task-expiry")
        hold.expires_at = timezone.now() - timedelta(seconds=1)
        hold.save(update_fields=["expires_at"])
        self.assertEqual(expire_booking_holds(), 1)
        hold.refresh_from_db()
        self.assertEqual(hold.status, AppointmentHold.Status.EXPIRED)
        appointment = self.appointment("task-reminder", self.slot(2))
        reminder = AppointmentReminder.objects.create(
            organization=self.organization,
            appointment=appointment,
            reminder_type=AppointmentReminder.ReminderType.UPCOMING,
            scheduled_for=timezone.now() - timedelta(minutes=1),
            idempotency_key="task-reminder-due",
        )
        result = deliver_booking_reminders()
        self.assertGreaterEqual(result["processed"], 1)
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, AppointmentReminder.Status.SENT)

    @override_settings(BOOKING_REMINDERS_ENABLE=False)
    def test_reminder_task_is_safe_when_disabled(self):
        from booking.tasks import deliver_booking_reminders

        self.assertEqual(deliver_booking_reminders(), {"processed": 0, "sent": 0})

    def test_ai_and_voice_booking_tools_are_registered(self):
        for name in (
            "list_services",
            "get_service_details",
            "list_booking_branches",
            "list_bookable_staff",
            "get_available_slots",
            "get_appointment",
            "list_customer_appointments",
            "get_booking_policy",
            "create_appointment_hold",
            "confirm_appointment",
            "create_appointment",
            "reschedule_appointment",
            "cancel_appointment",
            "join_waitlist",
            "request_booking_handoff",
        ):
            self.assertIn(name, TOOL_REGISTRY)
            self.assertIn(name, VOICE_SAFE_TOOLS)
        self.assertTrue(TOOL_REGISTRY["create_appointment"].mutating)

    def test_ai_booking_tools_use_server_scoped_contact_and_real_availability(self):
        from types import SimpleNamespace
        from uuid import uuid4

        from ai_runtime.tools import ToolContext, ToolValidationError, validate_arguments
        from channels.models import ChannelConnection, ChannelStatus, ChannelType
        from crm.models import Conversation

        connection_row = ChannelConnection.objects.create(
            organization=self.organization,
            branch=self.branch,
            type=ChannelType.WEBCHAT,
            provider="internal_test",
            display_name="Booking AI",
            external_identifier="booking-ai",
            status=ChannelStatus.ACTIVE,
        )
        conversation = Conversation.objects.create(
            organization=self.organization,
            channel_connection=connection_row,
            channel_type=ChannelType.WEBCHAT,
            external_thread_id="booking-ai-thread",
            contact=self.contact,
        )
        context = ToolContext(
            organization=self.organization,
            conversation=conversation,
            run=SimpleNamespace(id=uuid4()),
            actor=self.membership,
        )
        self.assertEqual(len(TOOL_REGISTRY["list_services"].handler(context, {})["services"]), 1)
        details = TOOL_REGISTRY["get_service_details"].handler(
            context, {"service_id": str(self.service.id)}
        )
        self.assertEqual(details["name"], self.service.name)
        self.assertEqual(
            TOOL_REGISTRY["list_booking_branches"].handler(context, {})["branches"][0]["id"],
            str(self.branch.id),
        )
        self.assertEqual(len(TOOL_REGISTRY["list_bookable_staff"].handler(context, {
            "branch_id": str(self.branch.id), "service_id": str(self.service.id)
        })["staff"]), 1)
        available = TOOL_REGISTRY["get_available_slots"].handler(context, {
            "branch_id": str(self.branch.id),
            "service_id": str(self.service.id),
            "date_from": self.day.isoformat(),
            "date_to": self.day.isoformat(),
        })["slots"]
        hold = TOOL_REGISTRY["create_appointment_hold"].handler(context, {
            "branch_id": str(self.branch.id),
            "service_id": str(self.service.id),
            "staff_profile_id": str(self.staff.id),
            "starts_at": available[0]["starts_at"],
        })
        create_args = {
            "hold_id": hold["hold_id"],
            "customer_timezone": "Asia/Tashkent",
            "customer_identity": "Customer",
            "confirmation_summary": (
                f"Consultation, Center, {self.day.isoformat()} at "
                f"{available[0]['local_start'][11:16]} Asia/Tashkent confirmed."
            ),
        }
        validate_arguments(TOOL_REGISTRY["create_appointment"], create_args)
        with self.assertRaisesMessage(ToolValidationError, "customer_identity_not_confirmed"):
            TOOL_REGISTRY["create_appointment"].handler(
                context, {**create_args, "customer_identity": "Another person"}
            )
        with self.assertRaisesMessage(ToolValidationError, "booking_confirmation_incomplete"):
            TOOL_REGISTRY["create_appointment"].handler(
                context, {**create_args, "confirmation_summary": "Yes"}
            )
        created = TOOL_REGISTRY["create_appointment"].handler(context, create_args)
        self.assertTrue(created["created"])
        reference = created["public_reference"]
        self.assertEqual(
            TOOL_REGISTRY["get_appointment"].handler(
                context, {"public_reference": reference}
            )["status"],
            Appointment.Status.CONFIRMED,
        )
        self.assertEqual(
            len(TOOL_REGISTRY["list_customer_appointments"].handler(context, {})["appointments"]), 1
        )
        self.assertTrue(TOOL_REGISTRY["get_booking_policy"].handler(context, {
            "branch_id": str(self.branch.id), "service_id": str(self.service.id)
        })["allow_customer_cancel"])
        cancelled = TOOL_REGISTRY["cancel_appointment"].handler(
            context, {"public_reference": reference, "reason": "Customer confirmed cancellation"}
        )
        self.assertEqual(cancelled["status"], Appointment.Status.CANCELLED)
        waitlist = TOOL_REGISTRY["join_waitlist"].handler(context, {
            "branch_id": str(self.branch.id),
            "service_id": str(self.service.id),
            "earliest_date": self.day.isoformat(),
            "latest_date": (self.day + timedelta(days=2)).isoformat(),
        })
        self.assertEqual(waitlist["status"], WaitlistEntry.Status.ACTIVE)

    def test_tenant_api_requires_header_and_blocks_other_tenant(self):
        missing = self.client.get("/api/v1/booking/services/")
        self.assertEqual(missing.status_code, 400)
        other_user = User.objects.create_user(username="tenant2", email="tenant2@example.test", password="pw12345!")
        other = create_organization(creator=other_user, name="Tenant two", slug="tenant-two")
        denied = self.client.get(
            "/api/v1/booking/services/", HTTP_X_ORGANIZATION_ID=str(other.id)
        )
        self.assertEqual(denied.status_code, 403)

    def test_tenant_api_catalog_and_availability(self):
        services = self.client.get("/api/v1/booking/services/", **self.headers)
        self.assertEqual(services.status_code, 200)
        availability = self.client.get(
            "/api/v1/booking/availability/",
            {
                "branch_id": self.branch.id,
                "service_id": self.service.id,
                "date_from": self.day.isoformat(),
                "date_to": self.day.isoformat(),
            },
            **self.headers,
        )
        self.assertEqual(availability.status_code, 200, availability.data)
        self.assertGreater(len(availability.data["results"]), 0)

    def test_catalog_configuration_api_is_role_aware_and_tenant_scoped(self):
        category = self.client.post(
            "/api/v1/booking/categories/",
            {"name": "Wellness", "description": "Public-safe category", "position": 2},
            format="json",
            **self.headers,
        )
        self.assertEqual(category.status_code, 201, category.data)
        service = self.client.post(
            "/api/v1/booking/services/",
            {
                "category": category.data["id"],
                "name": "Massage",
                "duration_minutes": 45,
                "price_minor": 250000,
                "currency": "UZS",
            },
            format="json",
            **self.headers,
        )
        self.assertEqual(service.status_code, 201, service.data)
        agent_user = User.objects.create_user(username="booking-agent", password="pw12345!")
        agent = OrganizationMembership.objects.create(
            organization=self.organization,
            user=agent_user,
            role=OrganizationMembershipRole.AGENT,
            status=OrganizationMembershipStatus.ACTIVE,
        )
        staff = self.client.post(
            "/api/v1/booking/staff/",
            {"membership": str(agent.id), "display_name": "Booking Agent"},
            format="json",
            **self.headers,
        )
        self.assertEqual(staff.status_code, 201, staff.data)
        self.assertEqual(self.client.post(
            "/api/v1/booking/staff-branches/",
            {"staff_profile": staff.data["id"], "branch": str(self.branch.id)},
            format="json",
            **self.headers,
        ).status_code, 201)
        self.assertEqual(self.client.post(
            "/api/v1/booking/staff-services/",
            {"staff_profile": staff.data["id"], "service": service.data["id"], "active": True},
            format="json",
            **self.headers,
        ).status_code, 201)
        resource = self.client.post(
            "/api/v1/booking/resources/",
            {"branch": str(self.branch.id), "name": "Chair 2", "resource_type": "chair", "capacity": 1},
            format="json",
            **self.headers,
        )
        self.assertEqual(resource.status_code, 201, resource.data)
        self.assertEqual(self.client.post(
            "/api/v1/booking/resource-requirements/",
            {"service": service.data["id"], "specific_resource": resource.data["id"], "quantity": 1},
            format="json",
            **self.headers,
        ).status_code, 201)
        rule = self.client.post(
            "/api/v1/booking/schedule-rules/",
            {
                "owner_type": "staff", "owner_id": staff.data["id"], "weekday": self.day.weekday(),
                "start_local_time": "09:00", "end_local_time": "12:00", "active": True,
            },
            format="json",
            **self.headers,
        )
        self.assertEqual(rule.status_code, 201, rule.data)
        overlap = self.client.post(
            "/api/v1/booking/schedule-rules/",
            {
                "owner_type": "staff", "owner_id": staff.data["id"], "weekday": self.day.weekday(),
                "start_local_time": "11:00", "end_local_time": "13:00", "active": True,
            },
            format="json",
            **self.headers,
        )
        self.assertEqual(overlap.status_code, 400)
        exception = self.client.post(
            "/api/v1/booking/schedule-exceptions/",
            {
                "owner_type": "staff", "owner_id": staff.data["id"],
                "starts_at": (self.now + timedelta(days=2)).isoformat(),
                "ends_at": (self.now + timedelta(days=2, hours=1)).isoformat(),
                "exception_type": "time_off", "reason": "Approved leave",
            },
            format="json",
            **self.headers,
        )
        self.assertEqual(exception.status_code, 201, exception.data)
        self.client.force_authenticate(agent_user)
        self.assertEqual(self.client.get("/api/v1/booking/services/", **self.headers).status_code, 200)
        self.assertEqual(self.client.post(
            "/api/v1/booking/services/", {"name": "Denied", "duration_minutes": 30},
            format="json", **self.headers,
        ).status_code, 403)

    def test_cross_tenant_ids_are_404_and_superuser_has_no_bypass(self):
        other_user = User.objects.create_user(username="api-other", password="pw12345!")
        other = create_organization(creator=other_user, name="API other", slug="api-other")
        other_branch = Branch.objects.create(organization=other, name="Other branch")
        response = self.client.post(
            "/api/v1/booking/resources/",
            {"branch": str(other_branch.id), "name": "Leak", "resource_type": "room", "capacity": 1},
            format="json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            self.client.get(f"/api/v1/booking/services/{Service.objects.create(organization=other, name='Other service', duration_minutes=30).id}/", **self.headers).status_code,
            404,
        )
        superuser = User.objects.create_superuser(username="root-booking", password="pw12345!")
        self.client.force_authenticate(superuser)
        self.assertEqual(self.client.get("/api/v1/booking/services/", **self.headers).status_code, 403)

    def test_suspended_organization_keeps_history_read_only(self):
        self.organization.status = OrganizationStatus.SUSPENDED
        self.organization.save(update_fields=["status"])
        self.assertEqual(self.client.get("/api/v1/booking/services/", **self.headers).status_code, 200)
        self.assertEqual(self.client.post(
            "/api/v1/booking/services/", {"name": "Blocked", "duration_minutes": 30},
            format="json", **self.headers,
        ).status_code, 403)

    def test_tenant_appointment_api_is_idempotent_and_operational(self):
        slot = self.slot()
        hold_payload = {
            "branch_id": str(self.branch.id),
            "service_id": str(self.service.id),
            "contact_id": str(self.contact.id),
            "staff_profile_id": str(self.staff.id),
            "starts_at": slot.starts_at.isoformat(),
        }
        hold = self.client.post(
            "/api/v1/booking/holds/", hold_payload, format="json",
            HTTP_IDEMPOTENCY_KEY="api-hold", **self.headers,
        )
        self.assertEqual(hold.status_code, 201, hold.data)
        duplicate_hold = self.client.post(
            "/api/v1/booking/holds/", hold_payload, format="json",
            HTTP_IDEMPOTENCY_KEY="api-hold", **self.headers,
        )
        self.assertEqual(duplicate_hold.status_code, 200)
        appointment = self.client.post(
            "/api/v1/booking/appointments/",
            {"hold_id": hold.data["id"], "customer_timezone": "Asia/Tashkent"},
            format="json", HTTP_IDEMPOTENCY_KEY="api-appointment", **self.headers,
        )
        self.assertEqual(appointment.status_code, 201, appointment.data)
        duplicate = self.client.post(
            "/api/v1/booking/appointments/",
            {"hold_id": hold.data["id"], "customer_timezone": "Asia/Tashkent"},
            format="json", HTTP_IDEMPOTENCY_KEY="api-appointment", **self.headers,
        )
        self.assertEqual(duplicate.status_code, 200)
        detail_url = f"/api/v1/booking/appointments/{appointment.data['id']}/"
        self.assertEqual(self.client.get(detail_url, **self.headers).status_code, 200)
        checked_in = self.client.post(
            f"{detail_url}status/", {"status": "checked_in"}, format="json", **self.headers
        )
        self.assertEqual(checked_in.status_code, 200, checked_in.data)
        no_show = self.client.post(
            f"{detail_url}status/", {"status": "no_show"}, format="json", **self.headers
        )
        self.assertEqual(no_show.status_code, 200, no_show.data)

    def test_public_flow_requires_consent_and_uses_opaque_session(self):
        profile = PublicBookingProfile.objects.create(
            organization=self.organization,
            enabled=True,
            title="Book a visit",
        )
        public = APIClient()
        page = public.get(f"/api/v1/public/booking/{profile.public_key}/")
        self.assertEqual(page.status_code, 200)
        rejected = public.post(
            f"/api/v1/public/booking/{profile.public_key}/sessions/",
            {"display_name": "Public customer", "email": "public@example.test", "consent": False},
            format="json",
        )
        self.assertEqual(rejected.status_code, 400)
        session = public.post(
            f"/api/v1/public/booking/{profile.public_key}/sessions/",
            {"display_name": "Public customer", "email": "public@example.test", "consent": True},
            format="json",
        )
        self.assertEqual(session.status_code, 201, session.data)
        token = session.data["session_token"]
        self.assertNotIn(token, str(PublicBookingProfile.objects.get().public_key))
        forbidden = public.post(
            f"/api/v1/public/booking/{profile.public_key}/holds/",
            {},
            format="json",
        )
        self.assertEqual(forbidden.status_code, 401)

    def test_public_flow_is_session_scoped_idempotent_and_public_safe(self):
        profile = PublicBookingProfile.objects.create(
            organization=self.organization,
            enabled=True,
            title="Book safely",
        )
        public = APIClient()
        session_response = public.post(
            f"/api/v1/public/booking/{profile.public_key}/sessions/",
            {"display_name": "Public One", "email": "public-one@example.test", "consent": True},
            format="json",
        )
        self.assertEqual(session_response.status_code, 201, session_response.data)
        token = session_response.data["session_token"]
        headers = {"HTTP_X_BOOKING_SESSION": token}
        availability = public.get(
            f"/api/v1/public/booking/{profile.public_key}/availability/",
            {
                "branch_id": self.branch.id,
                "service_id": self.service.id,
                "date_from": self.day.isoformat(),
                "date_to": self.day.isoformat(),
            },
        )
        self.assertEqual(availability.status_code, 200, availability.data)
        slot = availability.data["results"][0]
        hold_payload = {
            "branch_id": str(self.branch.id),
            "service_id": str(self.service.id),
            "staff_profile_id": str(self.staff.id),
            "starts_at": slot["starts_at"],
        }
        hold = public.post(
            f"/api/v1/public/booking/{profile.public_key}/holds/",
            hold_payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="public-hold",
            **headers,
        )
        self.assertEqual(hold.status_code, 201, hold.data)
        self.assertEqual(public.post(
            f"/api/v1/public/booking/{profile.public_key}/holds/",
            hold_payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="public-hold",
            **headers,
        ).status_code, 200)
        appointment = public.post(
            f"/api/v1/public/booking/{profile.public_key}/appointments/",
            {"hold_id": hold.data["id"], "customer_timezone": "Asia/Tashkent", "customer_notes": "Doorbell"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="public-appointment",
            **headers,
        )
        self.assertEqual(appointment.status_code, 201, appointment.data)
        reference = appointment.data["public_reference"]
        detail_url = f"/api/v1/public/booking/{profile.public_key}/appointments/{reference}/"
        detail = public.get(detail_url, **headers)
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertNotIn("internal_notes", detail.data)
        self.assertNotIn("events", detail.data)
        other_session = public.post(
            f"/api/v1/public/booking/{profile.public_key}/sessions/",
            {"display_name": "Public Two", "email": "public-two@example.test", "consent": True},
            format="json",
        ).data["session_token"]
        self.assertEqual(public.get(
            detail_url, HTTP_X_BOOKING_SESSION=other_session
        ).status_code, 404)
        cancel_url = f"{detail_url}cancel/"
        self.assertEqual(public.post(
            cancel_url, {"reason": "Changed plans"}, format="json", **headers
        ).status_code, 200)
        self.assertEqual(public.post(
            cancel_url, {"reason": "Retry"}, format="json", **headers
        ).status_code, 200)

    def test_public_waitlist_never_auto_books(self):
        profile = PublicBookingProfile.objects.create(organization=self.organization, enabled=True)
        public = APIClient()
        token = public.post(
            f"/api/v1/public/booking/{profile.public_key}/sessions/",
            {"display_name": "Wait Customer", "phone": "+998901112233", "consent": True},
            format="json",
        ).data["session_token"]
        response = public.post(
            f"/api/v1/public/booking/{profile.public_key}/waitlist/",
            {
                "branch_id": str(self.branch.id),
                "service_id": str(self.service.id),
                "earliest_date": self.day.isoformat(),
                "latest_date": (self.day + timedelta(days=3)).isoformat(),
            },
            format="json",
            HTTP_X_BOOKING_SESSION=token,
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Appointment.objects.count(), 0)

    def test_public_disabled_profile_is_not_discoverable(self):
        profile = PublicBookingProfile.objects.create(organization=self.organization, enabled=False)
        response = APIClient().get(f"/api/v1/public/booking/{profile.public_key}/")
        self.assertEqual(response.status_code, 404)

    def test_appointment_event_is_append_only(self):
        appointment = self.appointment("immutable-event")
        event = appointment.events.get()
        event.summary = "Changed"
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()


@skipUnless(connection.vendor == "postgresql", "PostgreSQL advisory-lock concurrency test")
@override_settings(
    DEBUG=True,
    BOOKING_ENABLE=True,
    BOOKING_REMINDER_PROVIDER="fake",
    BILLING_ENABLE=True,
    BILLING_PROVIDER="fake",
    BILLING_FAKE_PROVIDER=True,
)
class BookingPostgresConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        owner = User.objects.create_user(username="race-owner", email="race@example.test", password="pw12345!")
        self.organization = create_organization(creator=owner, name="Race", slug="booking-race")
        membership = OrganizationMembership.objects.get(organization=self.organization, user=owner)
        self.branch = Branch.objects.create(
            organization=self.organization,
            name="Race branch",
            timezone="UTC",
            working_hours={key: [{"open": "00:00", "close": "23:59"}] for key in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")},
        )
        self.service = Service.objects.create(
            organization=self.organization,
            name="Race service",
            duration_minutes=30,
            minimum_notice_minutes=0,
            maximum_advance_days=30,
        )
        self.staff = BookableStaffProfile.objects.create(
            organization=self.organization,
            membership=membership,
            display_name="Race staff",
            maximum_concurrent_appointments=2,
        )
        StaffBranchAssignment.objects.create(
            organization=self.organization, staff_profile=self.staff, branch=self.branch
        )
        StaffService.objects.create(
            organization=self.organization, staff_profile=self.staff, service=self.service
        )
        resource = BookableResource.objects.create(
            organization=self.organization,
            branch=self.branch,
            name="Race room",
            resource_type="room",
            capacity=1,
        )
        ServiceResourceRequirement.objects.create(
            organization=self.organization,
            service=self.service,
            specific_resource=resource,
            quantity=1,
        )
        self.contacts = [
            create_contact(organization=self.organization, membership=membership, display_name=f"Race {index}")
            for index in (1, 2)
        ]
        day = timezone.localdate() + timedelta(days=2)
        self.starts_at = datetime.combine(day, time(9), tzinfo=ZoneInfo("UTC"))

    def test_two_simultaneous_holds_yield_exactly_one_winner(self):
        barrier = Barrier(2)

        def attempt(index):
            connections.close_all()
            barrier.wait()
            try:
                hold, _ = AppointmentHoldService.create(
                    organization=self.organization,
                    branch_id=self.branch.id,
                    service_id=self.service.id,
                    contact_id=self.contacts[index].id,
                    starts_at=self.starts_at,
                    staff_profile_id=self.staff.id,
                    idempotency_key=f"race-{index}",
                )
                return str(hold.id)
            except BookingError as exc:
                return exc.code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, (0, 1)))
        self.assertEqual(results.count("slot_unavailable"), 1)
        self.assertEqual(AppointmentHold.objects.filter(status=AppointmentHold.Status.ACTIVE).count(), 1)
