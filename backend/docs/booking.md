# Booking and scheduling architecture

Booking is one tenant-owned domain used by employees, Public Booking, Inbox,
AI Runtime, and Voice. Channel integrations never implement independent slot or
appointment logic. All writes pass through `AppointmentHoldService` and
`AppointmentService`.

## Catalog, people, and capacity

- `ServiceCategory` and `Service` define duration, buffers, price display,
  notice windows, booking mode, and active state. An appointment copies the
  service name, duration, price, and currency so historical bookings do not
  change with the catalog.
- `BookableStaffProfile`, `StaffBranchAssignment`, and `StaffService` bind an
  active organization member to supported branches and services. Capacity may
  be greater than one for explicitly concurrent work.
- `BookableResource` represents rooms, chairs, equipment, or another typed
  capacity unit. `ServiceResourceRequirement` selects either one exact resource
  or an eligible resource type and quantity.

Every related model carries `organization_id`; model validation and
tenant-scoped querysets reject cross-organization relationships. Customer APIs
do not contain a platform-superuser bypass.

## Schedules, timezones, and availability

Weekly rules may belong to a branch, staff profile, or resource. Date or weekly
breaks and UTC schedule exceptions add holidays, time off, unavailable spans,
or explicit availability overrides. Policy precedence is:

1. branch + service;
2. branch;
3. service;
4. organization default;
5. safe server defaults.

Canonical instants are stored in UTC. Availability is calculated in the
branch's validated IANA timezone and returns both UTC and local forms. Local
times are round-tripped through `zoneinfo`: nonexistent daylight-saving times
are rejected; both folds of a repeated local time are retained with an explicit
fold value.

Availability intersects branch hours, optional staff hours, breaks,
exceptions, staff concurrency, service buffers, active appointments,
unexpired holds, and resource capacity. Queries are limited to 31 days. An AI
or UI can only select an instant returned by this service.

## Holds and PostgreSQL locking

A short-lived `AppointmentHold` reserves a staff/time/resource allocation.
Creating a hold runs in one database transaction and takes deterministic
PostgreSQL transaction-scoped advisory locks for the organization, branch,
staff, and instant. Branch, staff, and selected resource rows are also read with
`SELECT ... FOR UPDATE`. Availability is recalculated after acquiring locks.
Idempotency keys are unique per organization.

Appointment creation locks and converts the hold in a second atomic operation.
Expired, released, or converted holds cannot be converted. PostgreSQL
concurrency tests start two independent database connections at the same
barrier and assert exactly one active hold wins.

## Lifecycle, events, reminders, and waitlist

Lifecycle states are pending confirmation, confirmed, checked in, in progress,
completed, cancelled, no-show, and rejected. Service and policy settings decide
whether confirmation is required. Public confirmation tokens are random,
time-bounded, compared by hash, and never stored in plaintext. Reschedule uses a
new hold and revalidation; cancellation enforces the configured notice window.

`AppointmentEvent` is append-only and records customer, employee, AI, and
system changes. Mirrored CRM activities link the appointment to its contact and
source conversation without copying message content. Appointment usage is
recorded as an idempotent Billing event.

Reminder rows have scheduled, queued, sent, failed, cancelled, and skipped
states plus retry counts and idempotency keys. The live provider chooses an
existing conversation and delegates to the same consent-aware CRM/channel send
path; SMS is rechecked with SMS consent immediately before sending. CI uses a
deterministic no-network provider. Waitlist entries are FIFO within the same
tenant, branch, service, and requested date window.

## AI, Voice, and clinic safety

AI Runtime exposes strict tools to list services, inspect service details,
list branches and bookable staff, check real availability, inspect customer
appointments and policy, create and convert a hold, create/reschedule/cancel an
appointment, join the waitlist, and request handoff. Mutations remain governed
by stored tool policy and server role checks. Creating a booking requires exact
service, branch, staff, timezone-aware instant, customer timezone, and a
server-scoped conversation identity. The final confirmation summary must match
the selected service, branch, local date/time, and timezone. A tool returns the
database status only after the transaction commits.

Voice exposes the same tools only when Voice policy allows them and instructs
the assistant to repeat exact details before a write. Medical diagnosis,
prescription, clinical advice, urgent symptoms, payments, refunds, arbitrary
transfers, and outbound calls remain prohibited and route to human handoff.

## Public Booking and operational visibility

The public profile uses a high-entropy opaque key. A visitor supplies consent,
name, and an email or phone identity to receive a short-lived opaque session;
only its SHA-256 hash is stored. Subsequent hold, create, lookup, and customer
actions are scoped to that session's organization and contact. Responses expose
public catalog details but never internal descriptions or cross-tenant IDs.
Requests are rate limited without logging personal content.

Internal Super Admin receives organization-level counts and reminder error
codes through separate internal authentication. It does not receive customer
identities, notes, messages, or appointment content and cannot use customer APIs
as a superuser.

## Entitlements, privacy, and operations

`booking`, `public_booking_page`, `appointment_reminders`, `booking_waitlist`,
`booking_ai`, `max_bookable_staff`, `max_services`, `max_resources`,
`monthly_booking_appointments`, and `monthly_booking_reminders` extend the
existing PlanCatalog and OrganizationEntitlement source of truth. Organization
suspension, provider controls, consent, and channel kill switches remain
stronger than entitlement.

Booking stores operational contact links and customer notes only when supplied.
It stores no credentials, payment data, medical record, transcript, or audio.
Retention/deletion must follow the organization's documented CRM policy; no
universal legal retention period is claimed.

Useful deterministic commands:

```bash
DEBUG=true USE_SQLITE=true python manage.py test booking.tests
DEBUG=true python evals/booking/run_evals.py
DEBUG=true E2E_TESTING=true BOOKING_REMINDER_PROVIDER=fake \
  python manage.py seed_booking_demo
```

## Known limitations

This stage does not include appointment payments/deposits, refunds, external
calendar synchronization, recurring appointments, multi-day stays,
telemedicine, EMR/EHR, medical workflows, commerce inventory/orders,
WhatsApp, or production deployment. Resource schedules are modeled, validated,
and editable alongside branch/staff schedule coverage in the tenant UI and API.
