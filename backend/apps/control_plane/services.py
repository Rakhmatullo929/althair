from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import APIException

from ai_runtime.models import AIHandoff, AIRun, AIRunStatus, AIUsageEvent, OrganizationAIRuntimeConfig
from channels.models import ChannelConnection
from control_plane.authentication import network_hash, request_ip
from control_plane.models import (
    ControlKind,
    DataRequestStatus,
    DataRequestType,
    OperationalControl,
    OperationalJob,
    OrganizationEntitlement,
    OrganizationOperationalState,
    PlanCatalog,
    PlatformAccessStatus,
    PlatformAuditEvent,
    PlatformDataRequest,
    PlatformIncident,
    PlatformRole,
    PlatformSession,
    PlatformStaffAccess,
)
from control_plane.permissions import mfa_is_fresh
from crm.models import Contact, Conversation, Message, MessageDirection
from organizations.models import Organization, OrganizationMembership, OrganizationStatus
from voice.models import VoiceCall, VoiceCallStatus


User = get_user_model()
REDACTED_KEYS = {
    "password", "secret", "token", "credential", "authorization", "cookie", "body",
    "message", "prompt", "transcript", "audio", "payload", "recovery", "totp", "phone",
}


class ControlPlaneConflict(APIException):
    status_code = 409
    default_detail = "The internal operation conflicts with the current state."
    default_code = "internal_operation_conflict"


class ControlPlaneDenied(APIException):
    status_code = 403
    default_detail = "The internal operation is not permitted."
    default_code = "internal_operation_denied"


def _safe_key(key: object) -> bool:
    lowered = str(key).lower()
    return not any(term in lowered for term in REDACTED_KEYS)


def safe_summary(value, *, depth=0):
    if depth > 4:
        return "[truncated]"
    if isinstance(value, dict):
        return {str(key)[:80]: safe_summary(item, depth=depth + 1) for key, item in value.items() if _safe_key(key)}
    if isinstance(value, (list, tuple)):
        return [safe_summary(item, depth=depth + 1) for item in value[:50]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


def require_reason(reason: object) -> str:
    clean = str(reason or "").strip()
    if len(clean) < 8:
        raise ControlPlaneConflict("A specific reason of at least 8 characters is required.")
    return clean[:1000]


def record_audit(
    request,
    *,
    action: str,
    target_type: str,
    target_id="",
    reason: str,
    organization=None,
    before=None,
    after=None,
    result="success",
):
    actor = getattr(request, "platform_access", None) if request else None
    event = PlatformAuditEvent.objects.create(
        actor=actor,
        platform_role=actor.role if actor else "",
        action=action[:120],
        target_type=target_type[:80],
        target_id=str(target_id)[:80],
        organization=organization,
        reason=require_reason(reason),
        before_summary=safe_summary(before or {}),
        after_summary=safe_summary(after or {}),
        request_id=str(getattr(request, "request_id", ""))[:100],
        network_hash=network_hash(request_ip(request)) if request else "",
        mfa_fresh=mfa_is_fresh(request) if request else False,
        result=result[:40],
    )
    if actor and mfa_is_fresh(request):
        PlatformStaffAccess.objects.filter(pk=actor.pk).update(last_privileged_action_at=timezone.now())
    return event


def ensure_default_entitlement(organization: Organization) -> OrganizationEntitlement:
    plan, _ = PlanCatalog.objects.get_or_create(
        key="manual",
        defaults={
            "display_name": "Manual",
            "feature_flags": {
                "channels": True, "ai": True, "voice": True, "branches": True, "seats": True, "usage": True,
            },
            "default_limits": {"seats": 25, "branches": 25},
            "internal_notes": "Safe default for organizations created before billing exists.",
        },
    )
    entitlement, _ = OrganizationEntitlement.objects.get_or_create(
        organization=organization, defaults={"plan": plan, "status": OrganizationEntitlement.Status.MANUAL}
    )
    return entitlement


def public_entitlement(organization: Organization) -> dict:
    entitlement = ensure_default_entitlement(organization)
    features = {
        key: value is True for key, value in entitlement.plan.feature_flags.items()
    }
    features.update({key: value is True for key, value in entitlement.feature_overrides.items()})
    return {
        "plan": entitlement.plan_id,
        "status": entitlement.status,
        "features": features,
        "limits": {**entitlement.plan.default_limits, **entitlement.limit_overrides},
    }


def public_operational_restrictions(organization: Organization) -> dict:
    state, _ = OrganizationOperationalState.objects.get_or_create(organization=organization)
    from control_plane.policies import blocking_control

    return {
        "restricted": organization.status == OrganizationStatus.SUSPENDED
        or state.provider_sends_disabled
        or state.ai_disabled
        or bool(blocking_control(organization=organization, ai=True))
        or bool(blocking_control(organization=organization, provider_type="all")),
        "ai_disabled": state.ai_disabled or bool(blocking_control(organization=organization, ai=True)),
        "provider_sends_disabled": state.provider_sends_disabled,
        "new_logins_disabled": state.new_logins_disabled,
        "message": "Some capabilities are temporarily unavailable due to an operational control.",
    }


def overview_data() -> dict:
    today = timezone.localdate()
    month = today.replace(day=1)
    org_counts = dict(Organization.objects.values_list("status").annotate(total=Count("id")))
    channel_counts = list(
        ChannelConnection.objects.values("type", "status").annotate(count=Count("id")).order_by("type", "status")
    )
    ai_runs = AIRun.objects.filter(created_at__date=today)
    ai_month = AIUsageEvent.objects.filter(month_bucket=month).aggregate(
        input_tokens=Sum("input_tokens"), output_tokens=Sum("output_tokens")
    )
    message_counts = dict(
        Message.objects.filter(occurred_at__date=today).values_list("direction").annotate(total=Count("id"))
    )
    return {
        "organizations": {
            "total": Organization.objects.count(),
            "active": org_counts.get(OrganizationStatus.ACTIVE, 0),
            "suspended": org_counts.get(OrganizationStatus.SUSPENDED, 0),
        },
        "active_customer_users": OrganizationMembership.objects.filter(status="active", user__is_active=True)
        .values("user_id").distinct().count(),
        "channels": channel_counts,
        "ai": {
            "runs_today": ai_runs.count(),
            "failed_today": ai_runs.filter(status=AIRunStatus.FAILED).count(),
            "handoffs_open": AIHandoff.objects.filter(status="open").count(),
            "input_tokens_month": ai_month["input_tokens"] or 0,
            "output_tokens_month": ai_month["output_tokens"] or 0,
        },
        "messages": {
            "inbound_today": message_counts.get(MessageDirection.INBOUND, 0),
            "outbound_today": message_counts.get(MessageDirection.OUTBOUND, 0),
        },
        "voice": {
            "active_calls": VoiceCall.objects.filter(status__in=[VoiceCallStatus.ACCEPTED, VoiceCallStatus.ACTIVE]).count()
        },
        "jobs": dict(OperationalJob.objects.values_list("status").annotate(total=Count("id"))),
        "data_requests": PlatformDataRequest.objects.exclude(
            status__in=[DataRequestStatus.COMPLETED, DataRequestStatus.REJECTED, DataRequestStatus.CANCELLED]
        ).count(),
        "incidents": PlatformIncident.objects.exclude(status=PlatformIncident.Status.RESOLVED).count(),
        "controls": list(
            OperationalControl.objects.filter(active=True).values("kind", "provider_type", "expires_at")[:100]
        ),
    }


def redact_email(value: str) -> str:
    local, separator, domain = str(value or "").partition("@")
    if not separator:
        return "redacted"
    return f"{local[:1]}***@{domain}"


def organization_detail(organization: Organization, *, support_redaction=True) -> dict:
    state, _ = OrganizationOperationalState.objects.get_or_create(organization=organization)
    entitlement = public_entitlement(organization)
    members = organization.memberships.select_related("user").order_by("created_at")
    channels = organization.channels_channelconnections.order_by("type", "display_name")
    runtime = OrganizationAIRuntimeConfig.objects.filter(organization=organization).first()
    return {
        "id": str(organization.id),
        "name": organization.name,
        "slug": organization.slug,
        "status": organization.status,
        "industry": organization.industry,
        "default_language": organization.default_language,
        "timezone": organization.timezone,
        "created_at": organization.created_at,
        "members": [
            {
                "id": str(item.id), "email": redact_email(item.user.email) if support_redaction else item.user.email,
                "role": item.role, "status": item.status,
            }
            for item in members
        ],
        "branches": organization.branches.values("id", "name", "is_active"),
        "channels": [
            {
                "id": str(item.id), "type": item.type, "provider": item.provider,
                "display_name": item.display_name, "status": item.status,
                "last_error_code": item.last_error_code, "last_synced_at": item.last_synced_at,
            }
            for item in channels
        ],
        "ai": {
            "enabled": bool(runtime and runtime.enabled),
            "provider": runtime.provider if runtime else "not_configured",
            "model_alias": runtime.model if runtime else "",
            "runs": AIRun.objects.filter(organization=organization).count(),
            "handoffs": AIHandoff.objects.filter(organization=organization).count(),
        },
        "crm": {
            "contacts": Contact.objects.filter(organization=organization).count(),
            "conversations": Conversation.objects.filter(organization=organization).count(),
            "messages": Message.objects.filter(organization=organization).count(),
        },
        "operations": {
            "marked_for_review": state.marked_for_review,
            "new_logins_disabled": state.new_logins_disabled,
            "provider_sends_disabled": state.provider_sends_disabled,
            "ai_disabled": state.ai_disabled,
        },
        "entitlement": entitlement,
    }


@transaction.atomic
def set_organization_lifecycle(request, organization: Organization, *, action: str, reason: str):
    reason = require_reason(reason)
    organization = Organization.objects.select_for_update().get(pk=organization.pk)
    state, _ = OrganizationOperationalState.objects.select_for_update().get_or_create(organization=organization)
    before = {"status": organization.status, "new_logins_disabled": state.new_logins_disabled}
    if action == "suspend":
        if organization.status != OrganizationStatus.SUSPENDED:
            state.previous_status = organization.status
            organization.status = OrganizationStatus.SUSPENDED
            organization.save(update_fields=["status", "updated_at"])
        state.new_logins_disabled = True
        state.provider_sends_disabled = True
        state.ai_disabled = True
    elif action == "reactivate":
        if organization.status == OrganizationStatus.SUSPENDED:
            organization.status = state.previous_status if state.previous_status in {
                OrganizationStatus.TRIAL, OrganizationStatus.ACTIVE
            } else OrganizationStatus.ACTIVE
            organization.save(update_fields=["status", "updated_at"])
        state.new_logins_disabled = False
        state.provider_sends_disabled = False
        state.ai_disabled = False
    elif action == "review":
        state.marked_for_review = True
    elif action == "disable_logins":
        state.new_logins_disabled = True
    elif action == "disable_provider_sends":
        state.provider_sends_disabled = True
    elif action == "disable_ai":
        state.ai_disabled = True
    else:
        raise ControlPlaneConflict("Unsupported organization action.")
    state.lifecycle_reason = reason
    state.updated_by = request.platform_access
    state.save()
    after = {"status": organization.status, "new_logins_disabled": state.new_logins_disabled,
             "provider_sends_disabled": state.provider_sends_disabled, "ai_disabled": state.ai_disabled,
             "marked_for_review": state.marked_for_review}
    record_audit(request, action=f"organization.{action}", target_type="organization", target_id=organization.id,
                 organization=organization, reason=reason, before=before, after=after)
    return organization, state


@transaction.atomic
def activate_control(request, *, kind, reason, organization=None, provider_type="", channel_connection=None, expires_at=None):
    reason = require_reason(reason)
    if kind in {
        ControlKind.ORGANIZATION_AI,
        ControlKind.ORGANIZATION_AI_TOOL,
        ControlKind.ORGANIZATION_PROVIDER,
    } and not organization:
        raise ControlPlaneConflict("An organization is required for this control.")
    if kind in {
        ControlKind.GLOBAL_AI_TOOL,
        ControlKind.ORGANIZATION_AI_TOOL,
        ControlKind.GLOBAL_PROVIDER,
        ControlKind.ORGANIZATION_PROVIDER,
    } and not provider_type:
        raise ControlPlaneConflict("A provider or tool identifier is required for this control.")
    if kind == ControlKind.CHANNEL_CONNECTION and not channel_connection:
        raise ControlPlaneConflict("A channel connection is required for this control.")
    query = OperationalControl.objects.select_for_update().filter(
        kind=kind, organization=organization, provider_type=provider_type,
        channel_connection=channel_connection, active=True,
    )
    existing = query.first()
    if existing:
        return existing, False
    control = OperationalControl.objects.create(
        kind=kind, organization=organization, provider_type=provider_type,
        channel_connection=channel_connection, reason=reason, expires_at=expires_at,
        activated_by=request.platform_access,
    )
    ai_control = kind in {
        ControlKind.GLOBAL_AI,
        ControlKind.ORGANIZATION_AI,
        ControlKind.EXTERNAL_AUTOPILOT,
    }
    if ai_control:
        active_runs = AIRun.objects.filter(
            status__in=[AIRunStatus.QUEUED, AIRunStatus.WAITING_FOR_APPROVAL]
        )
        if organization:
            active_runs = active_runs.filter(organization=organization)
        active_runs.update(
            status=AIRunStatus.SUPERSEDED,
            error_category="operational_control",
            error_code="control_activated",
            completed_at=timezone.now(),
        )
    if kind in {
        ControlKind.GLOBAL_PROVIDER,
        ControlKind.ORGANIZATION_PROVIDER,
        ControlKind.CHANNEL_CONNECTION,
        ControlKind.VOICE_GLOBAL,
    }:
        queued_jobs = OperationalJob.objects.filter(
            status__in=[OperationalJob.Status.QUEUED, OperationalJob.Status.RETRYING]
        )
        if organization:
            queued_jobs = queued_jobs.filter(organization=organization)
        if channel_connection:
            queued_jobs = queued_jobs.filter(channel_connection=channel_connection)
        if provider_type:
            queued_jobs = queued_jobs.filter(job_type__icontains=provider_type)
        queued_jobs.update(status=OperationalJob.Status.CANCELLED, safe_error_code="control_activated")
    record_audit(request, action="control.activate", target_type="operational_control", target_id=control.id,
                 organization=organization, reason=reason, after={"kind": kind, "provider_type": provider_type,
                 "channel_connection": str(channel_connection.id) if channel_connection else "", "active": True})
    return control, True


@transaction.atomic
def restore_control(request, control: OperationalControl, *, reason: str):
    reason = require_reason(reason)
    control = OperationalControl.objects.select_for_update().get(pk=control.pk)
    if control.active:
        control.active = False
        control.restored_by = request.platform_access
        control.restored_reason = reason
        control.restored_at = timezone.now()
        control.save(update_fields=["active", "restored_by", "restored_reason", "restored_at", "updated_at"])
        record_audit(request, action="control.restore", target_type="operational_control", target_id=control.id,
                     organization=control.organization, reason=reason, before={"active": True}, after={"active": False})
    return control


PROVIDER_SETTING_MAP = {
    "instagram": ("META_INSTAGRAM_ENABLE_LIVE", "META_INSTAGRAM_FAKE_PROVIDER"),
    "telegram": ("TELEGRAM_ENABLE_LIVE", "TELEGRAM_FAKE_PROVIDER"),
    "gmail": ("GOOGLE_GMAIL_ENABLE_LIVE", "GOOGLE_GMAIL_FAKE_PROVIDER"),
    "sms": ("SMS_ENABLE_LIVE", "SMS_FAKE_PROVIDER"),
    "voice": ("VOICE_ENABLE_LIVE", "VOICE_FAKE_PROVIDER"),
}


def provider_health_data(*, provider_type="") -> list[dict]:
    rows = ChannelConnection.objects.select_related("organization").order_by("type", "organization__name")
    if provider_type:
        rows = rows.filter(type=provider_type)
    result = []
    for row in rows[:500]:
        live_setting, fake_setting = PROVIDER_SETTING_MAP.get(row.type, ("", ""))
        mode = "live" if live_setting and getattr(settings, live_setting, False) else (
            "fake" if fake_setting and getattr(settings, fake_setting, False) else "disabled"
        )
        result.append({
            "id": str(row.id), "organization": str(row.organization_id), "organization_name": row.organization.name,
            "type": row.type, "provider": row.provider, "display_name": row.display_name,
            "status": row.status, "mode": mode, "last_event_at": row.last_synced_at,
            "safe_error_category": row.last_error_code or "", "configuration_ready": row.status == "active",
            "secrets_redacted": True,
        })
    if not provider_type or provider_type == "infrastructure":
        try:
            connection.ensure_connection()
            database_status = "healthy"
        except Exception:
            database_status = "unavailable"
        broker = str(settings.CELERY_BROKER_URL)
        from voice.services import integration_readiness

        voice = integration_readiness()
        result.extend([
            {
                "id": "infrastructure-database", "organization": None, "organization_name": "Platform",
                "type": "infrastructure", "provider": "database", "display_name": "Database",
                "status": database_status, "mode": connection.vendor, "last_event_at": None,
                "safe_error_category": "" if database_status == "healthy" else "database_unavailable",
                "configuration_ready": database_status == "healthy", "secrets_redacted": True,
            },
            {
                "id": "infrastructure-redis", "organization": None, "organization_name": "Platform",
                "type": "infrastructure", "provider": "redis", "display_name": "Redis broker",
                "status": "configured" if broker.startswith(("redis://", "rediss://")) else "external",
                "mode": "tls" if broker.startswith("rediss://") else "configured", "last_event_at": None,
                "safe_error_category": "", "configuration_ready": bool(broker), "secrets_redacted": True,
            },
            {
                "id": "infrastructure-celery", "organization": None, "organization_name": "Platform",
                "type": "infrastructure", "provider": "celery", "display_name": "Celery workers",
                "status": "eager" if settings.CELERY_TASK_ALWAYS_EAGER else "configured",
                "mode": "eager" if settings.CELERY_TASK_ALWAYS_EAGER else "worker",
                "last_event_at": None, "safe_error_category": "", "configuration_ready": True,
                "secrets_redacted": True,
            },
            {
                "id": "infrastructure-voice", "organization": None, "organization_name": "Platform",
                "type": "infrastructure", "provider": "voice_worker", "display_name": "Voice worker",
                "status": "healthy" if voice["worker_ready"] else "unavailable", "mode": voice["mode"],
                "last_event_at": None, "safe_error_category": "" if voice["worker_ready"] else "worker_unavailable",
                "configuration_ready": voice["enabled"], "secrets_redacted": True,
            },
            {
                "id": "infrastructure-openai", "organization": None, "organization_name": "Platform",
                "type": "infrastructure", "provider": "openai", "display_name": "OpenAI runtime",
                "status": "configured" if bool(settings.OPENAI_API_KEY) else "not_configured",
                "mode": "live" if bool(settings.OPENAI_API_KEY) else "fake",
                "last_event_at": None, "safe_error_category": "", "configuration_ready": bool(settings.OPENAI_API_KEY),
                "secrets_redacted": True,
            },
        ])
    return result


@transaction.atomic
def retry_job(request, job: OperationalJob, *, reason: str):
    reason = require_reason(reason)
    job = OperationalJob.objects.select_for_update().get(pk=job.pk)
    if not job.idempotent:
        raise ControlPlaneConflict("This job type is not declared idempotent.")
    if job.status not in {OperationalJob.Status.FAILED, OperationalJob.Status.DEAD_LETTER}:
        raise ControlPlaneConflict("Only failed or dead-letter jobs can be retried.")
    before = {"status": job.status, "attempts": job.attempts}
    job.status = OperationalJob.Status.RETRYING
    job.attempts += 1
    job.next_retry_at = timezone.now()
    job.safe_error_code = ""
    job.save(update_fields=["status", "attempts", "next_retry_at", "safe_error_code", "updated_at"])
    record_audit(request, action="job.retry", target_type="operational_job", target_id=job.id,
                 organization=job.organization, reason=reason, before=before,
                 after={"status": job.status, "attempts": job.attempts})
    return job


@transaction.atomic
def transition_job(request, job: OperationalJob, *, action: str, reason: str):
    reason = require_reason(reason)
    job = OperationalJob.objects.select_for_update().get(pk=job.pk)
    before = {"status": job.status}
    if action == "cancel" and job.status in {OperationalJob.Status.QUEUED, OperationalJob.Status.RETRYING}:
        job.status = OperationalJob.Status.CANCELLED
    elif action == "acknowledge" and job.status == OperationalJob.Status.DEAD_LETTER:
        job.status = OperationalJob.Status.ACKNOWLEDGED
    else:
        raise ControlPlaneConflict("The job cannot make that transition.")
    job.save(update_fields=["status", "updated_at"])
    record_audit(request, action=f"job.{action}", target_type="operational_job", target_id=job.id,
                 organization=job.organization, reason=reason, before=before, after={"status": job.status})
    return job


@transaction.atomic
def update_entitlement(request, organization: Organization, data: dict, *, reason: str):
    reason = require_reason(reason)
    entitlement = ensure_default_entitlement(organization)
    entitlement = OrganizationEntitlement.objects.select_for_update().get(pk=entitlement.pk)
    before = public_entitlement(organization)
    if "plan" in data:
        entitlement.plan = PlanCatalog.objects.get(pk=data["plan"], active=True)
    if "status" in data:
        entitlement.status = data["status"]
    if "feature_overrides" in data:
        entitlement.feature_overrides = safe_summary(data["feature_overrides"])
    if "limit_overrides" in data:
        entitlement.limit_overrides = safe_summary(data["limit_overrides"])
    entitlement.updated_by = request.platform_access
    entitlement.save()
    after = public_entitlement(organization)
    record_audit(request, action="entitlement.update", target_type="organization_entitlement",
                 target_id=organization.id, organization=organization, reason=reason, before=before, after=after)
    return entitlement


@transaction.atomic
def create_data_request(request, *, organization, request_type, reason, scope, idempotency_key):
    reason = require_reason(reason)
    digest = hashlib.sha256(str(idempotency_key).encode("utf-8")).hexdigest()
    existing = PlatformDataRequest.objects.filter(idempotency_key_hash=digest).first()
    if existing:
        return existing, False
    approvals = 2 if request_type in {DataRequestType.ANONYMIZE, DataRequestType.DELETE} else 1
    row = PlatformDataRequest.objects.create(
        organization=organization, request_type=request_type, requested_by_staff=request.platform_access,
        status=DataRequestStatus.IDENTITY_VERIFICATION, reason=reason, scope=safe_summary(scope),
        approval_required=approvals, idempotency_key_hash=digest,
    )
    record_audit(request, action="data_request.create", target_type="platform_data_request", target_id=row.id,
                 organization=organization, reason=reason,
                 after={"type": request_type, "status": row.status, "approval_required": approvals})
    return row, True


@transaction.atomic
def approve_data_request(request, row: PlatformDataRequest, *, reason: str):
    reason = require_reason(reason)
    row = PlatformDataRequest.objects.select_for_update().get(pk=row.pk)
    if row.status not in {DataRequestStatus.IDENTITY_VERIFICATION, DataRequestStatus.REQUESTED}:
        raise ControlPlaneConflict("This data request is not awaiting approval.")
    if not row.identity_verified_at:
        raise ControlPlaneConflict("Request identity must be verified before approval.")
    if row.approved_by.filter(pk=request.platform_access.pk).exists():
        return row
    if row.request_type in {DataRequestType.ANONYMIZE, DataRequestType.DELETE} and request.platform_access.role != PlatformRole.OWNER:
        raise ControlPlaneDenied("Only a platform owner can approve destructive privacy work.")
    row.approved_by.add(request.platform_access)
    if row.approved_by.count() >= row.approval_required:
        row.status = DataRequestStatus.APPROVED
        row.save(update_fields=["status"])
        OperationalJob.objects.create(
            job_type=f"data_request.{row.request_type}", organization=row.organization,
            status=OperationalJob.Status.QUEUED, idempotent=True,
            idempotency_reference=row.idempotency_key_hash, metadata={"data_request_id": str(row.id)},
        )
    record_audit(request, action="data_request.approve", target_type="platform_data_request", target_id=row.id,
                 organization=row.organization, reason=reason,
                 after={"status": row.status, "approvals": row.approved_by.count(), "required": row.approval_required})
    return row


@transaction.atomic
def reject_data_request(request, row: PlatformDataRequest, *, reason: str):
    reason = require_reason(reason)
    row = PlatformDataRequest.objects.select_for_update().get(pk=row.pk)
    if row.status in {DataRequestStatus.COMPLETED, DataRequestStatus.CANCELLED}:
        raise ControlPlaneConflict("This data request is already final.")
    row.status = DataRequestStatus.REJECTED
    row.save(update_fields=["status"])
    record_audit(request, action="data_request.reject", target_type="platform_data_request", target_id=row.id,
                 organization=row.organization, reason=reason, after={"status": row.status})
    return row


@transaction.atomic
def verify_data_request_identity(request, row: PlatformDataRequest, *, reason: str):
    reason = require_reason(reason)
    row = PlatformDataRequest.objects.select_for_update().get(pk=row.pk)
    if row.status not in {DataRequestStatus.REQUESTED, DataRequestStatus.IDENTITY_VERIFICATION}:
        raise ControlPlaneConflict("This data request cannot be identity-verified in its current state.")
    row.status = DataRequestStatus.IDENTITY_VERIFICATION
    row.identity_verified_at = row.identity_verified_at or timezone.now()
    row.save(update_fields=["status", "identity_verified_at"])
    record_audit(request, action="data_request.identity_verified", target_type="platform_data_request",
                 target_id=row.id, organization=row.organization, reason=reason,
                 after={"status": row.status, "identity_verified": True})
    return row


@transaction.atomic
def run_approved_data_request(request, row: PlatformDataRequest, *, reason: str):
    reason = require_reason(reason)
    row = PlatformDataRequest.objects.select_for_update().get(pk=row.pk)
    if row.status == DataRequestStatus.COMPLETED and row.request_type == DataRequestType.EXPORT:
        return row, None
    if row.status != DataRequestStatus.APPROVED:
        raise ControlPlaneConflict("Only an approved data request can run.")
    if row.request_type != DataRequestType.EXPORT:
        row.status = DataRequestStatus.RUNNING
        row.save(update_fields=["status"])
        record_audit(request, action="data_request.job_started", target_type="platform_data_request",
                     target_id=row.id, organization=row.organization, reason=reason,
                     after={"status": row.status, "destructive_execution": "separate_reviewed_worker_required"})
        return row, None
    raw_token = secrets.token_urlsafe(32)
    row.export_reference = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    row.expires_at = timezone.now() + timedelta(minutes=10)
    row.status = DataRequestStatus.COMPLETED
    row.completed_at = timezone.now()
    row.save(update_fields=["export_reference", "expires_at", "status", "completed_at"])
    OperationalJob.objects.filter(
        idempotency_reference=row.idempotency_key_hash,
        status__in=[OperationalJob.Status.QUEUED, OperationalJob.Status.RETRYING],
    ).update(status=OperationalJob.Status.COMPLETED)
    record_audit(request, action="data_request.export_completed", target_type="platform_data_request",
                 target_id=row.id, organization=row.organization, reason=reason,
                 after={"status": row.status, "download_expires_at": row.expires_at})
    return row, raw_token


def export_manifest(row: PlatformDataRequest, raw_token: str) -> dict:
    if (
        row.request_type != DataRequestType.EXPORT
        or row.status != DataRequestStatus.COMPLETED
        or not row.expires_at
        or row.expires_at <= timezone.now()
        or not secrets.compare_digest(
            row.export_reference, hashlib.sha256(str(raw_token).encode("utf-8")).hexdigest()
        )
    ):
        raise ControlPlaneDenied("The export link is invalid or expired.")
    return {
        "organization": str(row.organization_id),
        "generated_at": row.completed_at,
        "scope": safe_summary(row.scope),
        "counts": {
            "members": OrganizationMembership.objects.filter(organization=row.organization).count(),
            "contacts": Contact.objects.filter(organization=row.organization).count(),
            "conversations": Conversation.objects.filter(organization=row.organization).count(),
            "messages": Message.objects.filter(organization=row.organization).count(),
        },
        "content_included": False,
        "secrets_included": False,
    }


@transaction.atomic
def update_staff_access(request, access: PlatformStaffAccess, data: dict, *, reason: str):
    reason = require_reason(reason)
    access = PlatformStaffAccess.objects.select_for_update().get(pk=access.pk)
    before = {"role": access.role, "status": access.status, "mfa_required": access.mfa_required}
    for field in ("role", "status", "mfa_required"):
        if field in data:
            setattr(access, field, data[field])
    access.save()
    PlatformSession.objects.filter(access=access, revoked_at__isnull=True).update(revoked_at=timezone.now())
    record_audit(request, action="staff.update", target_type="platform_staff_access", target_id=access.id,
                 reason=reason, before=before,
                 after={"role": access.role, "status": access.status, "mfa_required": access.mfa_required})
    return access
