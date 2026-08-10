from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

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
