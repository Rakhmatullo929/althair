from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from crm.models import (
    Contact,
    ContactTag,
    Conversation,
    FollowUpTask,
    FollowUpTaskStatus,
    Lead,
    LeadStatus,
    Tag,
)
from crm.services import add_internal_note, ensure_default_pipeline, record_activity
from organizations.models import Branch
from organizations.policies import role_allows


class ToolValidationError(Exception):
    pass


class ToolPermissionError(Exception):
    pass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    properties: dict
    required: tuple[str, ...]
    mutating: bool
    server_permission: str
    handler: Callable
    always_available: bool = False

    @property
    def default_execution_mode(self):
        if self.always_available or not self.mutating:
            return "automatic"
        return "require_approval"

    def provider_schema(self):
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": self.properties,
                "required": list(self.required),
                "additionalProperties": False,
            },
        }


def _string(description: str, max_length: int = 500):
    return {"type": "string", "description": description, "maxLength": max_length}


def _uuid(description: str):
    return {"type": "string", "description": description, "format": "uuid"}


def _company(ctx, args):
    profile = getattr(ctx.organization, "profile", None)
    return {
        "name": getattr(profile, "public_business_name", "") or ctx.organization.name,
        "description": getattr(profile, "short_description", ""),
        "timezone": ctx.organization.timezone,
    }


def _branches(ctx, args):
    branches = Branch.objects.filter(organization=ctx.organization, is_active=True).order_by("name")[:20]
    return {
        "branches": [
            {
                "id": str(branch.id),
                "name": branch.name,
                "address": branch.address,
                "timezone": branch.timezone,
            }
            for branch in branches
        ]
    }


def _branch_hours(ctx, args):
    branch = _scoped(Branch.objects.filter(organization=ctx.organization, is_active=True), args["branch_id"])
    return {"branch": branch.name, "timezone": branch.timezone, "working_hours": branch.working_hours}


def _contact(ctx, args):
    contact = _scoped(Contact.objects.for_organization(ctx.organization), args["contact_id"])
    return {
        "id": str(contact.id),
        "display_name": contact.display_name,
        "preferred_language": contact.preferred_language,
        "tags": list(contact.tags.values_list("name", flat=True)[:20]),
    }


def _recent_conversations(ctx, args):
    contact = _scoped(Contact.objects.for_organization(ctx.organization), args["contact_id"])
    conversations = (
        Conversation.objects.for_organization(ctx.organization)
        .filter(contact=contact)
        .order_by("-last_message_at")[:10]
    )
    return {
        "conversations": [
            {
                "id": str(conversation.id),
                "status": conversation.status,
                "channel_type": conversation.channel_type,
                "last_message_at": (
                    conversation.last_message_at.isoformat() if conversation.last_message_at else None
                ),
            }
            for conversation in conversations
        ]
    }


def _active_lead(ctx, args):
    contact = _scoped(Contact.objects.for_organization(ctx.organization), args["contact_id"])
    lead = (
        Lead.objects.for_organization(ctx.organization)
        .filter(contact=contact, status=LeadStatus.OPEN)
        .select_related("stage")
        .first()
    )
    return {"lead": None if not lead else {"id": str(lead.id), "title": lead.title, "stage": lead.stage.name}}


def _open_tasks(ctx, args):
    contact = _scoped(Contact.objects.for_organization(ctx.organization), args["contact_id"])
    tasks = (
        FollowUpTask.objects.for_organization(ctx.organization)
        .filter(related_contact=contact, status=FollowUpTaskStatus.OPEN)
        .order_by("due_at")[:20]
    )
    return {
        "tasks": [
            {
                "id": str(task.id),
                "title": task.title,
                "due_at": task.due_at.isoformat() if task.due_at else None,
            }
            for task in tasks
        ]
    }


def _update_contact_name(ctx, args):
    contact = ctx.conversation.contact
    contact.display_name = args["display_name"].strip()
    contact.updated_by = ctx.actor
    contact.full_clean()
    contact.save(update_fields=["display_name", "updated_by", "updated_at"])
    record_activity(
        organization=ctx.organization,
        actor_membership=ctx.actor,
        event_type="ai.contact_name_updated",
        summary="AI-approved contact name update",
        contact=contact,
        conversation=ctx.conversation,
    )
    return {"contact_id": str(contact.id), "display_name": contact.display_name}


def _add_contact_tag(ctx, args):
    contact = ctx.conversation.contact
    tag, _ = Tag.objects.get_or_create(
        organization=ctx.organization,
        name=args["tag"].strip()[:60],
        defaults={"color_token": "green"},
    )
    ContactTag.objects.get_or_create(organization=ctx.organization, contact=contact, tag=tag)
    record_activity(
        organization=ctx.organization,
        actor_membership=ctx.actor,
        event_type="ai.contact_tag_added",
        summary="AI-approved contact tag added",
        contact=contact,
        conversation=ctx.conversation,
    )
    return {"contact_id": str(contact.id), "tag": tag.name}


def _create_lead(ctx, args):
    existing = Lead.objects.for_organization(ctx.organization).filter(
        contact=ctx.conversation.contact, status=LeadStatus.OPEN
    ).first()
    if existing:
        return {"lead_id": str(existing.id), "created": False, "title": existing.title}
    pipeline = ensure_default_pipeline(ctx.organization)
    stage = pipeline.stages.filter(stage_type="open", is_active=True).order_by("position").first()
    lead = Lead(
        organization=ctx.organization,
        contact=ctx.conversation.contact,
        source_conversation=ctx.conversation,
        source_channel_type=ctx.conversation.channel_type,
        pipeline=pipeline,
        stage=stage,
        title=args["title"].strip(),
        description=args["description"].strip(),
        created_by=ctx.actor,
        updated_by=ctx.actor,
    )
    lead.full_clean()
    lead.save()
    record_activity(
        organization=ctx.organization,
        actor_membership=ctx.actor,
        event_type="ai.lead_created",
        summary="AI-approved lead created",
        contact=ctx.conversation.contact,
        conversation=ctx.conversation,
        lead=lead,
    )
    return {"lead_id": str(lead.id), "created": True, "title": lead.title}


def _create_task(ctx, args):
    task = FollowUpTask(
        organization=ctx.organization,
        title=args["title"].strip(),
        due_at=timezone.now() + timedelta(hours=args["due_in_hours"]),
        related_contact=ctx.conversation.contact,
        related_conversation=ctx.conversation,
        assigned_membership=ctx.actor,
        created_by=ctx.actor,
    )
    task.full_clean()
    task.save()
    record_activity(
        organization=ctx.organization,
        actor_membership=ctx.actor,
        event_type="ai.task_created",
        summary="AI-approved follow-up task created",
        contact=ctx.conversation.contact,
        conversation=ctx.conversation,
        task=task,
    )
    return {"task_id": str(task.id), "created": True, "title": task.title, "due_at": task.due_at.isoformat()}


def _internal_note(ctx, args):
    note = add_internal_note(conversation=ctx.conversation, membership=ctx.actor, body=args["body"].strip())
    return {"note_id": str(note.id), "created": True}


def _handoff(ctx, args):
    from ai_runtime.services import create_handoff

    handoff = create_handoff(
        conversation=ctx.conversation,
        run=ctx.run,
        reason_code=args["reason_code"],
        safe_summary=args["safe_summary"],
        requested_by="ai",
    )
    return {"handoff_id": str(handoff.id), "status": handoff.status, "reason_code": handoff.reason_code}


def _require_booking_ai(ctx):
    from billing.services import EntitlementService

    EntitlementService(ctx.organization).require("booking_ai")


def _booking_operation_key(ctx, action, value):
    run_id = getattr(getattr(ctx, "run", None), "id", None)
    scope = run_id or ctx.conversation.id
    return f"ai:{scope}:{action}:{value}"


def _customer_appointment(ctx, reference):
    from booking.models import Appointment

    appointment = Appointment.objects.for_organization(ctx.organization).filter(
        contact=ctx.conversation.contact,
        public_reference=reference,
    ).first()
    if not appointment:
        raise ToolValidationError("appointment_not_found")
    return appointment


def _booking_services(ctx, args):
    from booking.models import Service

    _require_booking_ai(ctx)
    services = Service.objects.for_organization(ctx.organization).filter(active=True).order_by("name")[:50]
    return {
        "services": [
            {
                "id": str(service.id),
                "name": service.name,
                "duration_minutes": service.duration_minutes,
                "price_minor": service.price_minor,
                "currency": service.currency,
                "booking_mode": service.booking_mode,
            }
            for service in services
        ]
    }


def _booking_branches(ctx, args):
    _require_booking_ai(ctx)
    return _branches(ctx, args)


def _booking_service_details(ctx, args):
    from booking.models import Service

    _require_booking_ai(ctx)
    service = _scoped(
        Service.objects.for_organization(ctx.organization).filter(active=True),
        args["service_id"],
    )
    return {
        "id": str(service.id),
        "name": service.name,
        "public_description": service.public_description,
        "duration_minutes": service.duration_minutes,
        "price_minor": service.price_minor,
        "currency": service.currency,
        "booking_mode": service.booking_mode,
        "customer_can_choose_staff": service.customer_can_choose_staff,
        "minimum_notice_minutes": service.minimum_notice_minutes,
        "maximum_advance_days": service.maximum_advance_days,
        "cancellation_notice_minutes": service.cancellation_notice_minutes,
    }


def _booking_staff(ctx, args):
    from booking.models import BookableStaffProfile
    from organizations.models import OrganizationMembershipStatus

    _require_booking_ai(ctx)
    rows = BookableStaffProfile.objects.for_organization(ctx.organization).filter(
        active=True,
        accepts_online_booking=True,
        membership__status=OrganizationMembershipStatus.ACTIVE,
        branch_assignments__branch_id=args["branch_id"],
        supported_services__service_id=args["service_id"],
        supported_services__active=True,
    ).distinct().order_by("display_name", "id")[:50]
    return {"staff": [{"id": str(row.id), "display_name": row.display_name} for row in rows]}


def _booking_availability(ctx, args):
    from booking.services import AvailabilityService

    _require_booking_ai(ctx)
    try:
        starts = date.fromisoformat(args["date_from"])
        ends = date.fromisoformat(args["date_to"])
    except ValueError as exc:
        raise ToolValidationError("invalid_booking_date") from exc
    slots = AvailabilityService(ctx.organization).slots(
        branch_id=args["branch_id"],
        service_id=args["service_id"],
        date_from=starts,
        date_to=ends,
    )[:20]
    return {"slots": [slot.as_dict() for slot in slots]}


def _booking_hold(ctx, args):
    from booking.models import AppointmentHold
    from booking.services import AppointmentHoldService, BookingError

    _require_booking_ai(ctx)
    try:
        starts_at = datetime.fromisoformat(args["starts_at"])
        if starts_at.tzinfo is None:
            raise ValueError
        hold, created = AppointmentHoldService.create(
            organization=ctx.organization,
            branch_id=args["branch_id"],
            service_id=args["service_id"],
            contact_id=ctx.conversation.contact_id,
            starts_at=starts_at,
            staff_profile_id=args["staff_profile_id"],
            idempotency_key=_booking_operation_key(ctx, "hold", args["starts_at"]),
            created_by_type=AppointmentHold.CreatedByType.AI,
        )
    except (ValueError, BookingError) as exc:
        raise ToolValidationError(getattr(exc, "code", "invalid_booking_time")) from exc
    return {
        "hold_id": str(hold.id),
        "starts_at": hold.starts_at.isoformat(),
        "ends_at": hold.ends_at.isoformat(),
        "expires_at": hold.expires_at.isoformat(),
        "created": created,
    }


def _validate_confirmed_identity(ctx, supplied):
    normalized = supplied.strip().casefold()
    contact = ctx.conversation.contact
    values = {contact.display_name.strip().casefold()}
    values.update(
        value.strip().casefold()
        for value in contact.identities.values_list("raw_value", flat=True)
        if value.strip()
    )
    if normalized not in values:
        raise ToolValidationError("customer_identity_not_confirmed")


def _booking_create_from_hold(ctx, args):
    from booking.models import AppointmentHold
    from booking.services import AppointmentService, BookingError

    _require_booking_ai(ctx)
    _validate_confirmed_identity(ctx, args["customer_identity"])
    try:
        hold = _scoped(
            AppointmentHold.objects.for_organization(ctx.organization).filter(
                contact=ctx.conversation.contact
            ).select_related("branch", "service"),
            args["hold_id"],
        )
        local_start = hold.starts_at.astimezone(ZoneInfo(args["customer_timezone"]))
        summary = args["confirmation_summary"].casefold()
        required_confirmation = (
            hold.service.name.casefold(),
            hold.branch.name.casefold(),
            local_start.date().isoformat(),
            local_start.strftime("%H:%M"),
            args["customer_timezone"].casefold(),
        )
        if not all(item in summary for item in required_confirmation):
            raise ToolValidationError("booking_confirmation_incomplete")
        appointment, created, _ = AppointmentService.create_from_hold(
            organization=ctx.organization,
            hold_id=args["hold_id"],
            idempotency_key=_booking_operation_key(ctx, "appointment", args["hold_id"]),
            customer_timezone=args["customer_timezone"],
            created_by_membership=ctx.actor,
            source_conversation=ctx.conversation,
        )
    except BookingError as exc:
        raise ToolValidationError(exc.code) from exc
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ToolValidationError("invalid_customer_timezone") from exc
    return {
        "appointment_id": str(appointment.id),
        "public_reference": appointment.public_reference,
        "status": appointment.status,
        "starts_at": appointment.starts_at.isoformat(),
        "created": created,
    }


def _booking_status(ctx, args):
    _require_booking_ai(ctx)
    appointment = _customer_appointment(ctx, args["public_reference"])
    return {
        "public_reference": appointment.public_reference,
        "status": appointment.status,
        "starts_at": appointment.starts_at.isoformat(),
        "ends_at": appointment.ends_at.isoformat(),
    }


def _booking_customer_appointments(ctx, args):
    from booking.models import Appointment

    _require_booking_ai(ctx)
    appointments = Appointment.objects.for_organization(ctx.organization).filter(
        contact=ctx.conversation.contact,
    ).order_by("-starts_at")[:20]
    return {
        "appointments": [
            {
                "public_reference": appointment.public_reference,
                "service_name": appointment.service_name_snapshot,
                "starts_at": appointment.starts_at.isoformat(),
                "ends_at": appointment.ends_at.isoformat(),
                "status": appointment.status,
            }
            for appointment in appointments
        ]
    }


def _booking_policy(ctx, args):
    from booking.models import Service
    from booking.services import resolve_policy

    _require_booking_ai(ctx)
    branch = _scoped(
        Branch.objects.filter(organization=ctx.organization, is_active=True),
        args["branch_id"],
    )
    service = _scoped(
        Service.objects.for_organization(ctx.organization).filter(active=True),
        args["service_id"],
    )
    policy = resolve_policy(organization=ctx.organization, branch=branch, service=service)
    return {
        "allow_customer_reschedule": policy.allow_customer_reschedule,
        "allow_customer_cancel": policy.allow_customer_cancel,
        "minimum_notice_minutes": service.minimum_notice_minutes or policy.default_minimum_notice_minutes,
        "maximum_advance_days": service.maximum_advance_days or policy.default_maximum_advance_days,
        "cancellation_notice_minutes": (
            service.cancellation_notice_minutes or policy.default_cancellation_notice_minutes
        ),
        "cancellation_policy_text": policy.cancellation_policy_text,
        "no_show_policy_text": policy.no_show_policy_text,
    }


def _booking_confirm(ctx, args):
    from booking.services import AppointmentService, BookingError

    _require_booking_ai(ctx)
    appointment = _customer_appointment(ctx, args["public_reference"])
    try:
        appointment, changed = AppointmentService.confirm(
            organization=ctx.organization,
            appointment_id=appointment.id,
            actor_type="customer",
            actor_membership=ctx.actor,
            token=args["confirmation_token"],
        )
    except BookingError as exc:
        raise ToolValidationError(exc.code) from exc
    return {"public_reference": appointment.public_reference, "status": appointment.status, "changed": changed}


def _booking_cancel(ctx, args):
    from booking.services import AppointmentService, BookingError

    _require_booking_ai(ctx)
    appointment = _customer_appointment(ctx, args["public_reference"])
    try:
        appointment, changed = AppointmentService.cancel(
            organization=ctx.organization,
            appointment_id=appointment.id,
            reason=args["reason"],
            actor_type="ai",
            actor_membership=ctx.actor,
            customer=True,
        )
    except BookingError as exc:
        raise ToolValidationError(exc.code) from exc
    return {"public_reference": appointment.public_reference, "status": appointment.status, "changed": changed}


def _booking_reschedule(ctx, args):
    from booking.services import AppointmentService, BookingError

    _require_booking_ai(ctx)
    appointment = _customer_appointment(ctx, args["public_reference"])
    try:
        starts_at = datetime.fromisoformat(args["starts_at"])
        if starts_at.tzinfo is None:
            raise ValueError
        appointment = AppointmentService.reschedule(
            organization=ctx.organization,
            appointment_id=appointment.id,
            starts_at=starts_at,
            idempotency_key=_booking_operation_key(ctx, "reschedule", args["starts_at"]),
            actor_type="ai",
            actor_membership=ctx.actor,
            customer=True,
        )
    except (ValueError, BookingError) as exc:
        raise ToolValidationError(getattr(exc, "code", "invalid_booking_time")) from exc
    return {
        "public_reference": appointment.public_reference,
        "status": appointment.status,
        "starts_at": appointment.starts_at.isoformat(),
    }


def _booking_join_waitlist(ctx, args):
    from booking.services import BookingError, WaitlistService

    _require_booking_ai(ctx)
    try:
        entry = WaitlistService.create(
            organization=ctx.organization,
            branch_id=args["branch_id"],
            service_id=args["service_id"],
            contact_id=ctx.conversation.contact_id,
            earliest_date=date.fromisoformat(args["earliest_date"]),
            latest_date=date.fromisoformat(args["latest_date"]),
            preferred_staff_id=args.get("preferred_staff_id"),
            preferred_time_windows=[],
        )
    except (ValueError, BookingError) as exc:
        raise ToolValidationError(getattr(exc, "code", "invalid_waitlist_date")) from exc
    return {"waitlist_entry_id": str(entry.id), "status": entry.status}


def _booking_handoff(ctx, args):
    return _handoff(ctx, {"reason_code": "booking_requires_human", "safe_summary": args["safe_summary"]})


TOOL_REGISTRY = {
    spec.name: spec
    for spec in (
        ToolSpec("get_company_profile", "Read the organization's public company profile.", {}, (), False, "read", _company),
        ToolSpec("list_active_branches", "List active public branches.", {}, (), False, "read", _branches),
        ToolSpec("get_branch_hours", "Read working hours for an active branch.", {"branch_id": _uuid("Active branch ID from context.")}, ("branch_id",), False, "read", _branch_hours),
        ToolSpec("get_contact", "Read a tenant-scoped CRM contact.", {"contact_id": _uuid("Contact ID from context.")}, ("contact_id",), False, "read", _contact),
        ToolSpec("list_recent_conversations", "List recent tenant-scoped conversations for a contact.", {"contact_id": _uuid("Contact ID from context.")}, ("contact_id",), False, "read", _recent_conversations),
        ToolSpec("get_active_lead", "Read the active lead for a contact.", {"contact_id": _uuid("Contact ID from context.")}, ("contact_id",), False, "read", _active_lead),
        ToolSpec("list_open_follow_up_tasks", "List open follow-up tasks for a contact.", {"contact_id": _uuid("Contact ID from context.")}, ("contact_id",), False, "read", _open_tasks),
        ToolSpec("update_contact_name", "Update the current conversation contact's display name.", {"display_name": _string("New plain-text customer display name.", 200)}, ("display_name",), True, "manage_crm", _update_contact_name),
        ToolSpec("add_contact_tag", "Add a plain-text tag to the current contact.", {"tag": _string("Tag name.", 60)}, ("tag",), True, "manage_crm", _add_contact_tag),
        ToolSpec("create_lead", "Create a CRM lead for the current contact and conversation.", {"title": _string("Lead title.", 200), "description": _string("Short factual lead description.", 1000)}, ("title", "description"), True, "manage_crm", _create_lead),
        ToolSpec("create_follow_up_task", "Create a follow-up task for the current contact.", {"title": _string("Task title.", 200), "due_in_hours": {"type": "integer", "minimum": 1, "maximum": 720, "description": "Hours until due."}}, ("title", "due_in_hours"), True, "manage_crm", _create_task),
        ToolSpec("add_internal_ai_note", "Add an AI-labeled internal note to this conversation.", {"body": _string("Plain-text factual internal note.", 2000)}, ("body",), True, "manage_crm", _internal_note),
        ToolSpec("request_human_handoff", "Pause AI and request a human for this conversation.", {"reason_code": _string("Stable safe reason code.", 80), "safe_summary": _string("Short factual summary without reasoning.", 1000)}, ("reason_code", "safe_summary"), True, "read", _handoff, True),
        ToolSpec("list_services", "List active tenant booking services and their public terms.", {}, (), False, "read", _booking_services),
        ToolSpec("get_service_details", "Read public booking details for one service from the tenant catalog.", {"service_id": _uuid("Service ID returned by list_services.")}, ("service_id",), False, "read", _booking_service_details),
        ToolSpec("list_booking_branches", "List active branches available for booking.", {}, (), False, "read", _booking_branches),
        ToolSpec("list_bookable_staff", "List public-safe staff choices for one service and branch.", {"branch_id": _uuid("Branch ID."), "service_id": _uuid("Service ID.")}, ("branch_id", "service_id"), False, "read", _booking_staff),
        ToolSpec("get_available_slots", "Read exact server-computed appointment slots. Dates use YYYY-MM-DD.", {"branch_id": _uuid("Branch ID."), "service_id": _uuid("Service ID."), "date_from": _string("First local date in YYYY-MM-DD.", 10), "date_to": _string("Last local date in YYYY-MM-DD.", 10)}, ("branch_id", "service_id", "date_from", "date_to"), False, "read", _booking_availability),
        ToolSpec("get_appointment", "Read one appointment belonging to the current customer.", {"public_reference": _string("Customer-visible booking reference.", 40)}, ("public_reference",), False, "read", _booking_status),
        ToolSpec("list_customer_appointments", "List recent appointments belonging to the current customer only.", {}, (), False, "read", _booking_customer_appointments),
        ToolSpec("get_booking_policy", "Read public cancellation, notice, and reschedule policy for a service and branch.", {"branch_id": _uuid("Branch ID."), "service_id": _uuid("Service ID.")}, ("branch_id", "service_id"), False, "read", _booking_policy),
        ToolSpec("create_appointment_hold", "Temporarily hold one exact slot returned by get_available_slots.", {"branch_id": _uuid("Confirmed branch ID."), "service_id": _uuid("Confirmed service ID."), "staff_profile_id": _uuid("Staff profile ID returned by availability."), "starts_at": _string("Timezone-aware ISO 8601 start instant from availability.", 64)}, ("branch_id", "service_id", "staff_profile_id", "starts_at"), True, "manage_crm", _booking_hold),
        ToolSpec("confirm_appointment", "Confirm a pending customer appointment using its opaque one-time confirmation token.", {"public_reference": _string("Customer-visible booking reference.", 40), "confirmation_token": _string("Opaque confirmation token supplied by the customer.", 200)}, ("public_reference", "confirmation_token"), True, "manage_crm", _booking_confirm),
        ToolSpec("create_appointment", "Convert an exact live hold into an appointment only after explicit confirmation of service, branch, local date/time, timezone, and customer identity.", {"hold_id": _uuid("Live hold ID."), "customer_timezone": _string("Confirmed IANA customer timezone.", 64), "customer_identity": _string("Exact customer name, phone, or email explicitly confirmed in the conversation.", 320), "confirmation_summary": _string("Short explicit confirmation including service, branch, local date, local time, and timezone.", 500)}, ("hold_id", "customer_timezone", "customer_identity", "confirmation_summary"), True, "manage_crm", _booking_create_from_hold),
        ToolSpec("reschedule_appointment", "Reschedule the current customer's appointment to an exact server-returned slot.", {"public_reference": _string("Customer-visible booking reference.", 40), "starts_at": _string("Confirmed timezone-aware ISO 8601 start instant.", 64)}, ("public_reference", "starts_at"), True, "manage_crm", _booking_reschedule),
        ToolSpec("cancel_appointment", "Cancel the current customer's appointment subject to policy.", {"public_reference": _string("Customer-visible booking reference.", 40), "reason": _string("Customer-provided cancellation reason.", 1000)}, ("public_reference", "reason"), True, "manage_crm", _booking_cancel),
        ToolSpec("join_waitlist", "Join the current customer to the booking waitlist without auto-booking.", {"branch_id": _uuid("Branch ID."), "service_id": _uuid("Service ID."), "earliest_date": _string("Earliest local date in YYYY-MM-DD.", 10), "latest_date": _string("Latest local date in YYYY-MM-DD.", 10)}, ("branch_id", "service_id", "earliest_date", "latest_date"), True, "manage_crm", _booking_join_waitlist),
        ToolSpec("request_booking_handoff", "Pause automation and request a human for a clinical, urgent, unsupported, or policy-blocked booking request.", {"safe_summary": _string("Short factual booking summary without diagnosis or hidden reasoning.", 1000)}, ("safe_summary",), True, "read", _booking_handoff, True),
    )
}


@dataclass
class ToolContext:
    organization: object
    conversation: Conversation
    run: object
    actor: object | None


def validate_arguments(spec: ToolSpec, arguments):
    if not isinstance(arguments, dict):
        raise ToolValidationError("arguments_must_be_object")
    if set(arguments) != set(spec.required):
        raise ToolValidationError("strict_schema_mismatch")
    for name, schema in spec.properties.items():
        value = arguments[name]
        if schema["type"] == "string":
            if not isinstance(value, str) or not value.strip() or len(value) > schema.get("maxLength", 10000):
                raise ToolValidationError(f"invalid_{name}")
            if "<" in value or ">" in value:
                raise ToolValidationError(f"unsafe_{name}")
        elif schema["type"] == "integer":
            if isinstance(value, bool) or not isinstance(value, int) or value < schema["minimum"] or value > schema["maximum"]:
                raise ToolValidationError(f"invalid_{name}")
    return arguments


def provider_tools_for(policies):
    by_name = {item.tool_name: item for item in policies}
    result = []
    for name, spec in TOOL_REGISTRY.items():
        policy = by_name.get(name)
        if spec.always_available or (policy and policy.enabled and policy.execution_mode != "disabled"):
            result.append(spec.provider_schema())
    return result


@transaction.atomic
def execute_tool(*, call, actor=None):
    # Lock and re-read the persisted state so Celery/provider replay cannot
    # execute a mutating handler twice for the same idempotency key.
    call = call.__class__.objects.select_for_update().select_related(
        "organization", "run__conversation__contact"
    ).get(pk=call.pk)
    if call.status == "succeeded":
        return call.output_redacted
    if call.status == "running":
        raise ToolValidationError("tool_execution_in_progress")
    spec = TOOL_REGISTRY.get(call.tool_name)
    if not spec:
        raise ToolValidationError("unknown_tool")
    arguments = validate_arguments(spec, call.input_redacted)
    if spec.mutating and not spec.always_available:
        if not actor or not role_allows(actor.role, spec.server_permission):
            raise ToolPermissionError("server_permission_denied")
    started = time.monotonic()
    context = ToolContext(
        organization=call.organization,
        conversation=call.run.conversation,
        run=call.run,
        actor=actor,
    )
    call.status = "running"
    call.save(update_fields=["status"])
    result = spec.handler(context, arguments)
    if not isinstance(result, dict):
        raise ToolValidationError("unsafe_tool_result")
    call.output_redacted = result
    call.status = "succeeded"
    call.duration_ms = max(1, int((time.monotonic() - started) * 1000))
    call.completed_at = timezone.now()
    call.save(update_fields=["output_redacted", "status", "duration_ms", "completed_at"])
    return result


def _scoped(queryset, object_id):
    try:
        return queryset.get(pk=object_id)
    except (queryset.model.DoesNotExist, ValidationError, ValueError) as exc:
        raise ToolValidationError("resource_not_found") from exc
