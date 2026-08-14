from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from ai_runtime.models import AIHandoff, AIToolPolicy, HandoffRequestedBy
from ai_runtime.tools import TOOL_REGISTRY, ToolContext, ToolPermissionError, ToolValidationError, validate_arguments
from channels.models import ChannelConnection, ChannelStatus, ChannelType
from crm.models import (
    Contact,
    ContactIdentity,
    ContactIdentityType,
    Conversation,
    ConversationAIState,
    ConversationStatus,
    FollowUpTask,
)
from crm.services import add_identity, create_contact, open_or_find_conversation, record_activity, resolve_contact_identity
from organizations.models import OrganizationStatus
from organizations.policies import role_allows
from voice.context import VOICE_SAFE_TOOLS, VoiceSessionBuilder
from voice.models import (
    VoiceAIMode,
    VoiceAuditEvent,
    VoiceCall,
    VoiceCallStatus,
    VoiceCarrierStatusEvent,
    VoiceConnection,
    VoiceConnectionStatus,
    VoiceControllerJob,
    VoiceDisclosureMode,
    VoiceOwnershipMode,
    VoiceToolCall,
    VoiceTranscriptRetentionMode,
    VoiceTranscriptSegment,
    VoiceTransferAttempt,
    VoiceTransferDestination,
    VoiceUsageEvent,
    VoiceWebhookEnvelope,
)
from voice.providers import VoiceProviderError, carrier_provider_for, realtime_provider_for
from control_plane.policies import blocking_control, operation_allowed


class VoiceError(Exception):
    def __init__(self, code: str, *, status_code: int = 400, details: dict | None = None):
        self.code = code
        self.status_code = status_code
        self.details = details or {}
        super().__init__(code)


ACTIVE_ORGANIZATIONS = {OrganizationStatus.TRIAL, OrganizationStatus.ACTIVE}
ROUTABLE_CONNECTIONS = {VoiceConnectionStatus.CONNECTED, VoiceConnectionStatus.DEGRADED}
ACTIVE_CALL_STATUSES = {
    VoiceCallStatus.INCOMING,
    VoiceCallStatus.ROUTING,
    VoiceCallStatus.RINGING,
    VoiceCallStatus.ACCEPTED,
    VoiceCallStatus.ACTIVE,
    VoiceCallStatus.TRANSFER_REQUESTED,
}


def safe_code(value: str, fallback: str = "voice_error") -> str:
    candidate = str(value or "")[:80]
    return candidate if re.fullmatch(r"[A-Za-z0-9_.:-]+", candidate) else fallback


def normalize_phone(value: str, *, allow_empty=False) -> str:
    raw = str(value or "").strip()
    if not raw and allow_empty:
        return ""
    match = re.search(r"\+([1-9]\d{6,14})", raw)
    if not match:
        raise VoiceError("invalid_phone_number")
    return f"+{match.group(1)}"


def sip_headers(payload: dict) -> dict[str, str]:
    rows = payload.get("sip_headers") or payload.get("sipHeaders") or []
    result = {}
    if isinstance(rows, list):
        for row in rows[:100]:
            if isinstance(row, dict):
                name = str(row.get("name") or "").strip().lower()
                value = str(row.get("value") or "").strip()[:500]
                if name and name not in result:
                    result[name] = value
    return result


def parse_incoming_event(event: dict) -> dict:
    if str(event.get("type")) != "realtime.call.incoming":
        raise VoiceError("unsupported_webhook_event")
    data = event.get("data")
    if not isinstance(data, dict):
        raise VoiceError("invalid_webhook_payload")
    call_id = str(data.get("call_id") or "").strip()
    if not call_id or len(call_id) > 160 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", call_id):
        raise VoiceError("invalid_call_id")
    headers = sip_headers(data)
    called = normalize_phone(headers.get("to", ""))
    caller = normalize_phone(headers.get("from", ""), allow_empty=True)
    return {
        "event_id": str(event.get("id") or "")[:160],
        "call_id": call_id,
        "called": called,
        "caller": caller,
        "sip_call_id": str(headers.get("call-id") or "")[:80],
    }


def voice_public_base() -> str:
    value = str(settings.TWILIO_VOICE_PUBLIC_BASE_URL or "").rstrip("/")
    if value:
        return value
    backend = str(getattr(settings, "BACKEND_DOMAIN", "") or "").rstrip("/")
    if backend:
        return backend if "://" in backend else f"https://{backend}"
    return "http://localhost:8000"


def carrier_status_url(connection: VoiceConnection) -> str:
    return f"{voice_public_base()}/api/v1/webhooks/twilio/voice/{connection.webhook_public_key}/status/"


def integration_readiness() -> dict:
    missing = []
    for name in (
        "TWILIO_VOICE_ACCOUNT_SID",
        "TWILIO_VOICE_AUTH_TOKEN",
        "OPENAI_API_KEY",
        "OPENAI_WEBHOOK_SECRET",
        "OPENAI_PROJECT_ID",
        "OPENAI_REALTIME_MODEL",
    ):
        if not getattr(settings, name, ""):
            missing.append(name)
    if not voice_public_base().startswith("https://"):
        missing.append("TWILIO_VOICE_PUBLIC_BASE_URL_HTTPS")
    worker = cache.get("voice:worker:heartbeat")
    return {
        "mode": "live" if settings.VOICE_ENABLE_LIVE else "development",
        "enabled": bool(settings.VOICE_FAKE_PROVIDER or settings.VOICE_ENABLE_LIVE),
        "carrier_provider": settings.VOICE_CARRIER_PROVIDER,
        "realtime_provider": settings.VOICE_REALTIME_PROVIDER,
        "fake_provider": bool(settings.VOICE_FAKE_PROVIDER),
        "global_kill_switch": bool(settings.VOICE_GLOBAL_KILL_SWITCH),
        "live_ready": bool(settings.VOICE_ENABLE_LIVE and not missing),
        "worker_ready": bool(worker or settings.TESTING),
        "missing_live_configuration": missing,
        "defaults": {
            "max_call_seconds": settings.VOICE_MAX_CALL_SECONDS,
            "max_concurrent_calls": settings.VOICE_MAX_CONCURRENT_CALLS,
            "daily_minute_limit": settings.VOICE_DAILY_MINUTE_LIMIT,
            "monthly_minute_limit": settings.VOICE_MONTHLY_MINUTE_LIMIT,
            "transcript_retention_days": settings.VOICE_TRANSCRIPT_RETENTION_DAYS,
            "recording": "disabled",
        },
    }


def webhook_auth_token(connection: VoiceConnection) -> str:
    if connection.ownership_mode == VoiceOwnershipMode.PLATFORM_MANAGED:
        return str(settings.TWILIO_VOICE_AUTH_TOKEN or "")
    return str(connection.carrier_auth_token_encrypted or "")


@transaction.atomic
def create_connection(*, organization, membership, data: dict) -> VoiceConnection:
    carrier = str(data.get("carrier") or "fake")
    ownership = str(data.get("ownership_mode") or VoiceOwnershipMode.PLATFORM_MANAGED)
    if carrier not in {"fake", "twilio_sip"} or ownership not in VoiceOwnershipMode.values:
        raise VoiceError("invalid_connection_mode")
    if carrier == "fake" and not settings.VOICE_FAKE_PROVIDER:
        raise VoiceError("fake_provider_disabled", status_code=409)
    if carrier == "twilio_sip" and not settings.VOICE_ENABLE_LIVE:
        raise VoiceError("live_voice_disabled", status_code=409)
    phone = normalize_phone(str(data.get("phone_number_e164") or "+15550107777"))
    channel = ChannelConnection(
        organization=organization,
        branch_id=data.get("branch") or None,
        type=ChannelType.VOICE,
        provider="twilio_sip" if carrier == "twilio_sip" else "fake_voice",
        display_name=str(data.get("display_name") or f"Voice {phone}")[:160],
        external_identifier=phone,
        status=ChannelStatus.ACTIVE,
        configuration={"ownership_mode": ownership, "inbound_only": True, "recording": "disabled"},
    )
    channel.full_clean()
    channel.save()
    connection = VoiceConnection(
        organization=organization,
        channel_connection=channel,
        carrier=carrier,
        ownership_mode=ownership,
        status=VoiceConnectionStatus.CONNECTED,
        phone_number_e164=phone,
        phone_number_sid=str(data.get("phone_number_sid") or "")[:64],
        sip_trunk_sid=str(data.get("sip_trunk_sid") or "")[:64],
        carrier_account_sid=str(data.get("carrier_account_sid") or "")[:64],
        carrier_api_key_sid=str(data.get("carrier_api_key_sid") or "")[:64],
        carrier_api_key_secret_encrypted=str(data.get("carrier_api_key_secret") or ""),
        carrier_auth_token_encrypted=str(data.get("carrier_auth_token") or ""),
        openai_project_id=str(data.get("openai_project_id") or "")[:120],
        sip_destination=str(data.get("sip_destination") or "")[:255],
        default_language=str(data.get("default_language") or organization.default_language),
        supported_languages=data.get("supported_languages") or ["ru", "uz", "en"],
        ai_mode=str(data.get("ai_mode") or VoiceAIMode.AUTOPILOT),
        realtime_model_alias=str(data.get("realtime_model_alias") or settings.OPENAI_REALTIME_MODEL)[:120],
        voice_name=str(data.get("voice_name") or settings.OPENAI_REALTIME_VOICE)[:80],
        reasoning_effort=str(data.get("reasoning_effort") or settings.OPENAI_REALTIME_REASONING_EFFORT),
        greeting=str(data.get("greeting") or "How may I help you today?")[:1000],
        business_hours_behavior=str(data.get("business_hours_behavior") or "callback"),
        business_hours=data.get("business_hours") or {},
        after_hours_message=str(data.get("after_hours_message") or "Our team can call you back.")[:1000],
        disclosure_mode=str(data.get("disclosure_mode") or VoiceDisclosureMode.AI_AND_TRANSCRIPT),
        transcript_retention_mode=str(data.get("transcript_retention_mode") or VoiceTranscriptRetentionMode.THIRTY_DAYS),
        max_call_seconds=int(data.get("max_call_seconds") or settings.VOICE_MAX_CALL_SECONDS),
        max_concurrent_calls=int(data.get("max_concurrent_calls") or settings.VOICE_MAX_CONCURRENT_CALLS),
        daily_minute_limit=int(data.get("daily_minute_limit") or settings.VOICE_DAILY_MINUTE_LIMIT),
        monthly_minute_limit=int(data.get("monthly_minute_limit") or settings.VOICE_MONTHLY_MINUTE_LIMIT),
        connected_by=membership,
        connected_at=timezone.now(),
    )
    if carrier == "twilio_sip" and ownership == VoiceOwnershipMode.CUSTOMER_OWNED:
        if not connection.carrier_account_sid or not connection.carrier_auth_token_encrypted:
            raise VoiceError("customer_credentials_required")
    connection.full_clean()
    try:
        connection.save()
    except IntegrityError as exc:
        raise VoiceError("called_number_already_connected", status_code=409) from exc
    if carrier == "twilio_sip":
        carrier_provider_for(connection).health(connection)
    VoiceAuditEvent.objects.create(
        organization=organization,
        connection=connection,
        actor_membership=membership,
        event_type="voice.connection_created",
        metadata={"carrier": carrier, "ownership_mode": ownership, "inbound_only": True},
    )
    return connection


@transaction.atomic
def update_connection(connection: VoiceConnection, *, membership, data: dict) -> VoiceConnection:
    allowed = {
        "default_language", "supported_languages", "ai_mode", "realtime_model_alias", "voice_name",
        "reasoning_effort", "greeting", "business_hours_behavior", "business_hours", "after_hours_message",
        "disclosure_mode", "transcript_retention_mode", "max_call_seconds", "max_concurrent_calls",
        "daily_minute_limit", "monthly_minute_limit", "max_tools_per_call", "max_transfer_attempts",
    }
    for key in allowed:
        if key in data:
            setattr(connection, key, data[key])
    connection.full_clean()
    connection.save()
    VoiceAuditEvent.objects.create(
        organization=connection.organization, connection=connection, actor_membership=membership,
        event_type="voice.connection_updated", metadata={"fields": sorted(set(data) & allowed)},
    )
    return connection


@transaction.atomic
def rotate_credentials(connection: VoiceConnection, *, membership, data: dict) -> VoiceConnection:
    if "carrier_api_key_sid" in data:
        connection.carrier_api_key_sid = str(data["carrier_api_key_sid"] or "")[:64]
    if "carrier_api_key_secret" in data:
        connection.carrier_api_key_secret_encrypted = str(data["carrier_api_key_secret"] or "")
    if "carrier_auth_token" in data:
        connection.carrier_auth_token_encrypted = str(data["carrier_auth_token"] or "")
    connection.save()
    VoiceAuditEvent.objects.create(
        organization=connection.organization, connection=connection, actor_membership=membership,
        event_type="voice.credentials_rotated", metadata={"credential_fields": sorted(data)},
    )
    return connection


@transaction.atomic
def set_connection_state(connection: VoiceConnection, *, membership, action: str) -> VoiceConnection:
    states = {
        "pause": VoiceConnectionStatus.PAUSED,
        "activate": VoiceConnectionStatus.CONNECTED,
        "disconnect": VoiceConnectionStatus.DISCONNECTED,
    }
    if action not in states:
        raise VoiceError("invalid_connection_action")
    connection.status = states[action]
    connection.disconnected_at = timezone.now() if action == "disconnect" else None
    connection.channel_connection.status = ChannelStatus.DISCONNECTED if action == "disconnect" else (
        ChannelStatus.DRAFT if action == "pause" else ChannelStatus.ACTIVE
    )
    connection.channel_connection.save(update_fields=["status", "updated_at"])
    connection.save(update_fields=["status", "disconnected_at", "updated_at"])
    VoiceAuditEvent.objects.create(
        organization=connection.organization, connection=connection, actor_membership=membership,
        event_type=f"voice.connection_{action}", metadata={},
    )
    return connection


def connection_health(connection: VoiceConnection, *, run_provider=False) -> dict:
    provider = {"carrier_reachable": None, "number_voice_capable": None, "sip_trunk_ready": None}
    error = connection.last_error_code
    if run_provider:
        try:
            provider.update(carrier_provider_for(connection).health(connection))
            error = ""
            connection.last_error_code = ""
            connection.failure_count = 0
        except VoiceProviderError as exc:
            error = safe_code(exc.code)
            connection.last_error_code = error
            connection.failure_count = min(connection.failure_count + 1, 100)
        connection.last_health_check_at = timezone.now()
        connection.save(update_fields=["last_error_code", "failure_count", "last_health_check_at", "updated_at"])
    readiness = integration_readiness()
    return {
        "status": connection.status,
        **provider,
        "realtime_ready": bool(connection.carrier == "fake" or readiness["live_ready"]),
        "worker_ready": readiness["worker_ready"],
        "carrier_status_callback_url": carrier_status_url(connection),
        "public_https_ready": carrier_status_url(connection).startswith("https://"),
        "recording": "disabled",
        "last_call_at": connection.last_call_at,
        "last_health_check_at": connection.last_health_check_at,
        "last_error_code": error,
        "active_calls": connection.calls.filter(status__in=ACTIVE_CALL_STATUSES).count(),
        "limits": {
            "max_call_seconds": connection.max_call_seconds,
            "max_concurrent_calls": connection.max_concurrent_calls,
            "daily_minute_limit": connection.daily_minute_limit,
            "monthly_minute_limit": connection.monthly_minute_limit,
        },
    }


@transaction.atomic
def create_transfer_destination(*, connection, membership, data: dict) -> VoiceTransferDestination:
    destination = VoiceTransferDestination(
        organization=connection.organization,
        voice_connection=connection,
        key=str(data.get("key") or "")[:80],
        display_name=str(data.get("display_name") or "")[:160],
        destination_type=str(data.get("destination_type") or "phone"),
        destination_encrypted=str(data.get("destination") or ""),
        branch_id=data.get("branch") or None,
        priority=int(data.get("priority") or 100),
        active=bool(data.get("active", True)),
        business_hours=data.get("business_hours") or {},
        fallback_behavior=str(data.get("fallback_behavior") or "callback_task"),
    )
    destination.full_clean()
    destination.save()
    VoiceAuditEvent.objects.create(
        organization=connection.organization, connection=connection, actor_membership=membership,
        event_type="voice.transfer_destination_created", metadata={"destination_key": destination.key},
    )
    return destination


@transaction.atomic
def update_transfer_destination(destination, *, membership, data: dict):
    allowed = {"display_name", "branch", "priority", "active", "business_hours", "fallback_behavior"}
    for key in allowed:
        if key in data:
            value = (data[key] or None) if key == "branch" else data[key]
            setattr(destination, "branch_id" if key == "branch" else key, value)
    if "destination" in data:
        destination.destination_encrypted = str(data["destination"] or "")
    destination.full_clean()
    destination.save()
    VoiceAuditEvent.objects.create(
        organization=destination.organization, connection=destination.voice_connection, actor_membership=membership,
        event_type="voice.transfer_destination_updated", metadata={"destination_key": destination.key},
    )
    return destination


def _usage_limit_reason(connection: VoiceConnection) -> str:
    now = timezone.now()
    calls = VoiceCall.objects.for_organization(connection.organization).filter(voice_connection=connection)
    active = calls.filter(status__in=ACTIVE_CALL_STATUSES).count()
    org_active = VoiceCall.objects.for_organization(connection.organization).filter(status__in=ACTIVE_CALL_STATUSES).count()
    if active >= connection.max_concurrent_calls or org_active >= settings.VOICE_MAX_CONCURRENT_CALLS:
        return "concurrent_call_limit"
    daily = calls.filter(created_at__date=now.date()).aggregate(total=Sum("duration_seconds"))["total"] or 0
    monthly = calls.filter(created_at__year=now.year, created_at__month=now.month).aggregate(total=Sum("duration_seconds"))["total"] or 0
    if daily >= connection.daily_minute_limit * 60:
        return "daily_minute_limit"
    if monthly >= connection.monthly_minute_limit * 60:
        return "monthly_minute_limit"
    return ""


def resolve_called_connection(called: str) -> VoiceConnection:
    try:
        return VoiceConnection.objects.select_related(
            "organization", "channel_connection", "connected_by", "connected_by__user"
        ).get(phone_number_e164=called, status__in=ROUTABLE_CONNECTIONS)
    except (VoiceConnection.DoesNotExist, VoiceConnection.MultipleObjectsReturned) as exc:
        raise VoiceError("unknown_called_number", status_code=404) from exc


def _contact_for_call(connection: VoiceConnection, *, caller: str, call_id: str):
    identity_type = ContactIdentityType.PHONE if caller else ContactIdentityType.EXTERNAL
    raw = caller or f"withheld-{call_id}"
    identity = resolve_contact_identity(
        organization=connection.organization,
        channel_connection=connection.channel_connection,
        identity_type=identity_type,
        raw_value=raw,
    )
    if identity:
        return identity.contact
    contact = create_contact(
        organization=connection.organization,
        membership=None,
        display_name=caller or "Withheld caller",
        preferred_language=connection.default_language,
    )
    add_identity(
        organization=connection.organization,
        contact=contact,
        identity_type=identity_type,
        raw_value=raw,
        channel_connection=connection.channel_connection,
        external_user_id=caller,
        metadata={"withheld": not bool(caller)},
    )
    return contact


@dataclass(frozen=True)
class IncomingCallResult:
    call: VoiceCall
    created: bool
    accepted: bool
    rejection_reason: str = ""


@transaction.atomic
def route_verified_incoming_call(event: dict) -> IncomingCallResult:
    parsed = parse_incoming_event(event)
    existing = VoiceCall.objects.filter(provider_call_id=parsed["call_id"]).first()
    if existing:
        return IncomingCallResult(existing, False, existing.status not in {VoiceCallStatus.REJECTED, VoiceCallStatus.FAILED}, existing.rejection_reason)
    connection = resolve_called_connection(parsed["called"])
    event_id = parsed["event_id"] or f"evt-{hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()}"
    existing_envelope = VoiceWebhookEnvelope.objects.filter(provider_event_id=event_id).select_related("call").first()
    if existing_envelope and existing_envelope.call:
        call = existing_envelope.call
        return IncomingCallResult(call, False, call.status != VoiceCallStatus.REJECTED, call.rejection_reason)
    rejection = ""
    if settings.VOICE_GLOBAL_KILL_SWITCH:
        rejection = "global_kill_switch"
    elif code := blocking_control(
        organization=connection.organization,
        provider_type="voice",
        channel_connection=connection.channel_connection,
        ai=True,
        voice=True,
        autopilot=True,
    ):
        rejection = code
    elif connection.organization.status not in ACTIVE_ORGANIZATIONS:
        rejection = "organization_read_only"
    elif connection.status not in ROUTABLE_CONNECTIONS:
        rejection = "connection_unavailable"
    elif connection.circuit_open_until and connection.circuit_open_until > timezone.now():
        rejection = "circuit_open"
    else:
        rejection = _usage_limit_reason(connection)
    contact = _contact_for_call(connection, caller=parsed["caller"], call_id=parsed["call_id"])
    conversation = open_or_find_conversation(
        organization=connection.organization,
        channel_connection=connection.channel_connection,
        contact=contact,
        external_thread_id=parsed["call_id"],
    )
    conversation.ai_state = ConversationAIState.OFF
    conversation.status = ConversationStatus.OPEN
    conversation.save(update_fields=["ai_state", "status", "updated_at"])
    explicit = connection.disclosure_mode == VoiceDisclosureMode.EXPLICIT_TRANSCRIPT
    stores = connection.transcript_retention_mode != VoiceTranscriptRetentionMode.DISABLED
    call = VoiceCall(
        organization=connection.organization,
        voice_connection=connection,
        conversation=conversation,
        contact=contact,
        provider_call_id=parsed["call_id"],
        carrier_call_id=parsed["sip_call_id"],
        caller_e164=parsed["caller"],
        caller_display=parsed["caller"] or "Withheld",
        called_e164=parsed["called"],
        status=VoiceCallStatus.REJECTED if rejection else VoiceCallStatus.ROUTING,
        rejection_reason=rejection,
        selected_language=connection.default_language,
        ai_mode=connection.ai_mode,
        realtime_provider=settings.VOICE_REALTIME_PROVIDER,
        realtime_model=connection.realtime_model_alias or settings.OPENAI_REALTIME_MODEL,
        voice_name=connection.voice_name,
        started_at=timezone.now(),
        ended_at=timezone.now() if rejection else None,
        consent_state="pending" if explicit else "not_required",
        transcript_storage_allowed=bool(stores and not explicit),
        outcome="rejected" if rejection else "",
    )
    call.full_clean()
    call.save()
    envelope = VoiceWebhookEnvelope.objects.create(
        organization=connection.organization,
        connection=connection,
        call=call,
        provider_event_id=event_id,
        event_type="realtime.call.incoming",
        processing_status="processed",
        safe_metadata={"called_connection": str(connection.id), "accepted": not bool(rejection)},
        processed_at=timezone.now(),
    )
    connection.last_inbound_at = timezone.now()
    connection.last_call_at = timezone.now()
    connection.save(update_fields=["last_inbound_at", "last_call_at", "updated_at"])
    VoiceAuditEvent.objects.create(
        organization=connection.organization, connection=connection, call=call,
        event_type="voice.call_routed" if not rejection else "voice.call_rejected",
        metadata={"reason": rejection} if rejection else {"webhook_envelope": str(envelope.id)},
    )
    if not rejection:
        VoiceControllerJob.objects.create(
            organization=connection.organization, call=call, available_at=timezone.now()
        )
    return IncomingCallResult(call, True, not bool(rejection), rejection)


def accept_or_reject_routed_call(result: IncomingCallResult) -> None:
    provider = realtime_provider_for(result.call.voice_connection)
    if not result.accepted:
        provider.reject(call_id=result.call.provider_call_id, status_code=486 if result.rejection_reason == "concurrent_call_limit" else 603)
        return
    if not operation_allowed(
        organization=result.call.organization,
        provider_type="voice",
        channel_connection=result.call.voice_connection.channel_connection,
        ai=True,
        voice=True,
        autopilot=True,
    ):
        provider.reject(call_id=result.call.provider_call_id, status_code=603)
        VoiceCall.objects.filter(pk=result.call.pk).update(
            status=VoiceCallStatus.REJECTED,
            rejection_reason="operational_control_active",
            ended_at=timezone.now(),
            outcome="rejected",
        )
        return
    session = VoiceSessionBuilder().build(call=result.call)
    safety_identifier = hashlib.sha256(
        f"{settings.SECRET_KEY}|{result.call.organization_id}|{result.call.contact_id}".encode()
    ).hexdigest()
    provider.accept(call_id=result.call.provider_call_id, session=session, safety_identifier=safety_identifier)
    VoiceCall.objects.filter(pk=result.call.pk, status=VoiceCallStatus.ROUTING).update(
        status=VoiceCallStatus.ACCEPTED, answered_at=timezone.now()
    )


@transaction.atomic
def store_final_transcript(*, call: VoiceCall, speaker: str, text: str, language: str = "", start_ms=None, end_ms=None):
    call = VoiceCall.objects.select_for_update().get(pk=call.pk)
    clean_text = str(text or "").strip()[:4000]
    if not clean_text or not call.transcript_storage_allowed:
        return None
    if call.transcript_segments.count() >= settings.VOICE_MAX_TRANSCRIPT_SEGMENTS:
        raise VoiceError("transcript_segment_limit")
    from django.db.models import Max

    sequence = (call.transcript_segments.aggregate(last=Max("sequence"))["last"] or 0) + 1
    segment = VoiceTranscriptSegment(
        organization=call.organization,
        call=call,
        sequence=sequence,
        speaker=speaker,
        text=clean_text,
        language=language[:2],
        start_ms=start_ms,
        end_ms=end_ms,
        final=True,
    )
    segment.full_clean()
    segment.save()
    return segment


@transaction.atomic
def record_transcript_consent(call: VoiceCall, *, granted: bool):
    call = VoiceCall.objects.select_for_update().get(pk=call.pk)
    call.consent_state = "granted" if granted else "declined"
    call.consent_recorded_at = timezone.now()
    call.transcript_storage_allowed = bool(
        granted and call.voice_connection.transcript_retention_mode != VoiceTranscriptRetentionMode.DISABLED
    )
    call.save(update_fields=["consent_state", "consent_recorded_at", "transcript_storage_allowed", "updated_at"])
    VoiceAuditEvent.objects.create(
        organization=call.organization, connection=call.voice_connection, call=call,
        event_type="voice.transcript_consent_recorded", metadata={"state": call.consent_state},
    )
    return call


def _voice_tool_allowed(call: VoiceCall, name: str):
    if name not in VOICE_SAFE_TOOLS:
        return False
    spec = TOOL_REGISTRY[name]
    if spec.always_available or not spec.mutating:
        return True
    policy = AIToolPolicy.objects.for_organization(call.organization).filter(tool_name=name).first()
    return bool(
        policy and policy.enabled and policy.execution_mode == "automatic" and policy.configuration.get("voice_allowed")
    )


@transaction.atomic
def execute_voice_tool(*, call: VoiceCall, provider_call_id: str, tool_name: str, arguments: dict, confirmation_marker: str = "") -> dict:
    call = VoiceCall.objects.select_for_update().select_related(
        "organization", "conversation__contact", "voice_connection__connected_by"
    ).get(pk=call.pk)
    if call.tool_calls.count() >= call.voice_connection.max_tools_per_call:
        raise VoiceError("voice_tool_limit")
    key = f"voice:{call.id}:{provider_call_id}"
    existing = VoiceToolCall.objects.filter(idempotency_key=key).first()
    if existing:
        return existing.output_redacted if existing.status == "succeeded" else {"status": existing.status}
    if tool_name == "request_voice_transfer":
        destination_key = str(arguments.get("destination_key") or "")
        return request_voice_transfer(call=call, destination_key=destination_key, idempotency_key=key)
    spec = TOOL_REGISTRY.get(tool_name)
    if not spec or not _voice_tool_allowed(call, tool_name):
        VoiceToolCall.objects.create(
            organization=call.organization, call=call, tool_name=tool_name[:80], provider_call_id=provider_call_id[:160],
            input_redacted={}, status="rejected", idempotency_key=key, error_category="tool_not_voice_allowed",
        )
        raise VoiceError("tool_not_voice_allowed")
    try:
        clean_args = validate_arguments(spec, arguments)
    except ToolValidationError as exc:
        raise VoiceError("invalid_tool_arguments") from exc
    if spec.mutating and not spec.always_available and not confirmation_marker:
        raise VoiceError("caller_confirmation_required")
    record = VoiceToolCall.objects.create(
        organization=call.organization, call=call, tool_name=tool_name, provider_call_id=provider_call_id[:160],
        input_redacted=clean_args, status="running", confirmation_marker=confirmation_marker[:160], idempotency_key=key,
    )
    started = time.monotonic()
    actor = call.voice_connection.connected_by
    if spec.mutating and not spec.always_available and not role_allows(actor.role, spec.server_permission):
        record.status = "rejected"
        record.error_category = "server_permission_denied"
        record.save(update_fields=["status", "error_category"])
        raise VoiceError("server_permission_denied")
    try:
        output = spec.handler(
            ToolContext(organization=call.organization, conversation=call.conversation, run=None, actor=actor),
            clean_args,
        )
        if not isinstance(output, dict):
            raise VoiceError("unsafe_tool_output")
        record.output_redacted = output
        record.status = "succeeded"
        record.duration_ms = max(1, int((time.monotonic() - started) * 1000))
        record.completed_at = timezone.now()
        record.save(update_fields=["output_redacted", "status", "duration_ms", "completed_at"])
        return output
    except Exception as exc:
        record.status = "failed"
        record.error_category = safe_code(getattr(exc, "code", type(exc).__name__), "tool_failed")
        record.completed_at = timezone.now()
        record.save(update_fields=["status", "error_category", "completed_at"])
        raise


def create_callback_handoff(call: VoiceCall, reason: str):
    due = timezone.now() + timedelta(hours=2)
    task = FollowUpTask(
        organization=call.organization,
        title="Return Voice caller request",
        due_at=due,
        assigned_membership=call.voice_connection.connected_by,
        related_contact=call.contact,
        related_conversation=call.conversation,
        created_by=call.voice_connection.connected_by,
    )
    task.full_clean()
    task.save()
    AIHandoff.objects.create(
        organization=call.organization,
        conversation=call.conversation,
        reason_code=reason[:80],
        safe_summary="Caller needs a human callback after an inbound Voice call.",
        requested_by=HandoffRequestedBy.AI,
        assigned_membership=call.voice_connection.connected_by,
    )
    record_activity(
        organization=call.organization, event_type="voice.callback_requested",
        summary="Voice callback requested", contact=call.contact, conversation=call.conversation, task=task,
        metadata={"call_id": str(call.id), "reason": reason[:80]},
    )
    return task


@transaction.atomic
def request_voice_transfer(*, call: VoiceCall, destination_key: str, idempotency_key: str) -> dict:
    call = VoiceCall.objects.select_for_update().select_related("voice_connection", "organization", "conversation", "contact").get(pk=call.pk)
    existing = VoiceTransferAttempt.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return {"status": existing.status, "destination_key": existing.destination.key}
    if call.transfer_attempts.count() >= call.voice_connection.max_transfer_attempts:
        raise VoiceError("transfer_attempt_limit")
    destination = call.voice_connection.transfer_destinations.filter(key=destination_key, active=True).first()
    if not destination:
        raise VoiceError("configured_transfer_destination_not_found")
    attempt = VoiceTransferAttempt.objects.create(
        organization=call.organization, call=call, destination=destination,
        idempotency_key=idempotency_key, status="requested",
    )
    call.status = VoiceCallStatus.TRANSFER_REQUESTED
    call.transfer_destination_key = destination.key
    call.transfer_status = "requested"
    call.save(update_fields=["status", "transfer_destination_key", "transfer_status", "updated_at"])
    try:
        provider_id = realtime_provider_for(call.voice_connection).refer(
            call_id=call.provider_call_id, target_uri=destination.target_uri, idempotency_key=idempotency_key
        )
        attempt.status = "accepted"
        attempt.provider_transfer_id = provider_id[:160]
        attempt.completed_at = timezone.now()
        attempt.save(update_fields=["status", "provider_transfer_id", "completed_at"])
        call.status = VoiceCallStatus.TRANSFERRED
        call.transfer_status = "accepted"
        call.outcome = "transferred"
        call.ai_control_active = False
        call.save(update_fields=["status", "transfer_status", "outcome", "ai_control_active", "updated_at"])
        record_activity(
            organization=call.organization, event_type="voice.transfer_accepted", summary="Voice transfer accepted",
            contact=call.contact, conversation=call.conversation,
            metadata={"call_id": str(call.id), "destination_key": destination.key},
        )
        return {"status": "accepted", "destination_key": destination.key}
    except VoiceProviderError as exc:
        attempt.status = "failed"
        attempt.error_category = safe_code(exc.code)
        attempt.completed_at = timezone.now()
        if destination.fallback_behavior == "callback_task":
            create_callback_handoff(call, "transfer_failed")
            attempt.status = "callback"
            call.outcome = "callback_requested"
        call.transfer_status = attempt.status
        attempt.save(update_fields=["status", "error_category", "completed_at"])
        call.save(update_fields=["transfer_status", "outcome", "updated_at"])
        return {"status": attempt.status, "destination_key": destination.key}


@transaction.atomic
def human_takeover(call: VoiceCall, *, membership):
    if membership.organization_id != call.organization_id or not role_allows(membership.role, "operate"):
        raise VoiceError("not_found", status_code=404)
    call = VoiceCall.objects.select_for_update().get(pk=call.pk)
    call.ai_control_active = False
    call.human_takeover_at = timezone.now()
    call.hangup_actor = "employee"
    call.save(update_fields=["ai_control_active", "human_takeover_at", "hangup_actor", "updated_at"])
    VoiceAuditEvent.objects.create(
        organization=call.organization, connection=call.voice_connection, call=call,
        actor_membership=membership, event_type="voice.human_takeover", metadata={},
    )
    return call


@transaction.atomic
def finalize_call(call: VoiceCall, *, outcome: str = "answered", hangup_actor: str = "caller", error: str = "", usage: dict | None = None):
    call = VoiceCall.objects.select_for_update().select_related("conversation", "contact", "voice_connection").get(pk=call.pk)
    if call.ended_at:
        return call
    call.ended_at = timezone.now()
    if call.started_at:
        call.duration_seconds = min(
            int((call.ended_at - call.started_at).total_seconds()), call.voice_connection.max_call_seconds
        )
    if call.status != VoiceCallStatus.TRANSFERRED:
        call.status = VoiceCallStatus.FAILED if error else VoiceCallStatus.COMPLETED
    call.outcome = "failed" if error else (call.outcome or outcome)
    call.error_category = safe_code(error) if error else ""
    call.hangup_actor = hangup_actor
    segments = list(call.transcript_segments.order_by("sequence").values_list("text", flat=True)[:20])
    if segments:
        call.summary = " ".join(segments)[:500]
    elif not call.summary:
        call.summary = "Inbound Voice call completed without a stored transcript."
    call.ai_control_active = False
    call.save()
    VoiceUsageEvent.objects.update_or_create(
        call=call,
        defaults={
            "organization": call.organization,
            "provider": call.realtime_provider,
            "model": call.realtime_model,
            "input_audio_tokens": int((usage or {}).get("input_audio_tokens", 0)),
            "output_audio_tokens": int((usage or {}).get("output_audio_tokens", 0)),
            "input_text_tokens": int((usage or {}).get("input_text_tokens", 0)),
            "output_text_tokens": int((usage or {}).get("output_text_tokens", 0)),
            "duration_seconds": call.duration_seconds,
            "tool_successes": call.tool_calls.filter(status="succeeded").count(),
            "tool_failures": call.tool_calls.filter(status__in=["failed", "rejected"]).count(),
        },
    )
    record_activity(
        organization=call.organization, event_type="voice.call_completed", summary="Inbound Voice call completed",
        contact=call.contact, conversation=call.conversation,
        metadata={"call_id": str(call.id), "outcome": call.outcome, "duration_seconds": call.duration_seconds},
    )
    call.conversation.last_message_at = call.ended_at
    call.conversation.last_inbound_at = call.ended_at
    call.conversation.save(update_fields=["last_message_at", "last_inbound_at", "updated_at"])
    return call


@transaction.atomic
def receive_carrier_status(*, connection: VoiceConnection, params) -> tuple[VoiceCarrierStatusEvent, bool]:
    carrier_call_id = str(params.get("CallSid") or "")[:80]
    status = safe_code(str(params.get("CallStatus") or "unknown"), "unknown")[:32]
    sequence = str(params.get("SequenceNumber") or "")[:20]
    event_key = hashlib.sha256(f"{carrier_call_id}|{status}|{sequence}".encode()).hexdigest()
    call = VoiceCall.objects.for_organization(connection.organization).filter(carrier_call_id=carrier_call_id).first()
    event, created = VoiceCarrierStatusEvent.objects.get_or_create(
        organization=connection.organization,
        connection=connection,
        event_key=event_key,
        defaults={
            "call": call,
            "carrier_call_id": carrier_call_id,
            "carrier_status": status,
            "error_code": safe_code(str(params.get("ErrorCode") or ""), "")[:40],
        },
    )
    if call and created and status in {"completed", "failed", "busy", "no-answer", "canceled"}:
        finalize_call(call, outcome="failed" if status == "failed" else "answered", hangup_actor="provider", error="carrier_failed" if status == "failed" else "")
    return event, created


def serialize_call_detail(call: VoiceCall) -> dict:
    transcript = []
    if call.transcript_storage_allowed:
        transcript = [
            {
                "id": str(row.id), "sequence": row.sequence, "speaker": row.speaker, "text": row.text,
                "language": row.language, "start_ms": row.start_ms, "end_ms": row.end_ms, "created_at": row.created_at,
            }
            for row in call.transcript_segments.all()
        ]
    return {
        "id": str(call.id), "organization": str(call.organization_id), "voice_connection": str(call.voice_connection_id),
        "conversation": str(call.conversation_id), "contact": str(call.contact_id), "contact_name": call.contact.display_name,
        "direction": call.direction, "caller": call.caller_display, "called_e164": call.called_e164,
        "status": call.status, "selected_language": call.selected_language, "ai_mode": call.ai_mode,
        "realtime_provider": call.realtime_provider, "realtime_model": call.realtime_model, "voice_name": call.voice_name,
        "started_at": call.started_at, "answered_at": call.answered_at, "ended_at": call.ended_at,
        "duration_seconds": call.duration_seconds, "transfer_destination_key": call.transfer_destination_key,
        "transfer_status": call.transfer_status, "hangup_actor": call.hangup_actor,
        "consent_state": call.consent_state, "transcript_storage_allowed": call.transcript_storage_allowed,
        "disclosure_version": call.disclosure_version, "summary": call.summary, "outcome": call.outcome,
        "error_category": call.error_category, "ai_control_active": call.ai_control_active,
        "human_takeover_at": call.human_takeover_at, "interruption_count": call.interruption_count,
        "transcript": transcript,
        "tools": [
            {"id": str(row.id), "name": row.tool_name, "status": row.status, "output": row.output_redacted, "error": row.error_category}
            for row in call.tool_calls.all()
        ],
        "transfers": [
            {"id": str(row.id), "destination_key": row.destination.key, "status": row.status, "error": row.error_category}
            for row in call.transfer_attempts.select_related("destination")
        ],
        "created_at": call.created_at,
    }


def conversation_policy(conversation: Conversation) -> dict:
    call = conversation.voice_calls.order_by("-created_at").first()
    if not call:
        return {"state": "provider_unavailable", "can_send": False}
    return {
        "state": call.status,
        "can_send": False,
        "voice_call_id": str(call.id),
        "call_status": call.status,
        "duration_seconds": call.duration_seconds,
        "language": call.selected_language,
        "ai_mode": call.ai_mode,
        "transfer_status": call.transfer_status,
        "consent_state": call.consent_state,
        "transcript_storage_allowed": call.transcript_storage_allowed,
        "outcome": call.outcome,
    }


def privacy_delete_expired_transcripts(*, now=None) -> int:
    now = now or timezone.now()
    total = 0
    modes = [(VoiceTranscriptRetentionMode.THIRTY_DAYS, 30), (VoiceTranscriptRetentionMode.NINETY_DAYS, 90)]
    for mode, days in modes:
        calls = VoiceCall.objects.filter(
            voice_connection__transcript_retention_mode=mode,
            ended_at__lt=now - timedelta(days=days),
        )
        deleted, _ = VoiceTranscriptSegment.objects.filter(call__in=calls).delete()
        total += deleted
    return total
