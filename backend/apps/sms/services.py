from __future__ import annotations

import hashlib
import re
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from channels.models import ChannelConnection, ChannelStatus, ChannelType
from crm.models import (
    ContactIdentity,
    ContactIdentityType,
    Conversation,
    ConversationAIState,
    Message,
    MessageContentType,
    MessageDirection,
    MessageSenderType,
    MessageStatus,
)
from crm.services import _enqueue_ai_inbound, ingest_inbound_message, record_activity
from organizations.models import OrganizationStatus
from sms.consent import apply_inbound_consent, consent_allows_send
from sms.models import (
    SMSAuditEvent,
    SMSAutomationMode,
    SMSConnection,
    SMSConnectionStatus,
    SMSConsent,
    SMSConsentSource,
    SMSConsentState,
    SMSOutboundAttempt,
    SMSOutboundAttemptStatus,
    SMSOwnershipMode,
    SMSProviderType,
    SMSStatusEvent,
    SMSWebhookEnvelope,
    SMSWebhookProcessingStatus,
)
from sms.parser import (
    SMSInbound,
    SMSPayloadError,
    SMSStatusCallback,
    inbound_event_key,
    normalize_phone,
    parse_inbound,
    parse_status,
    status_event_key,
)
from sms.providers import SMSProviderError, provider_for
from sms.segments import estimate_segments
from control_plane.policies import operation_allowed


class SMSError(Exception):
    def __init__(self, code: str, *, status_code: int = 400, details: dict | None = None):
        self.code = code
        self.status_code = status_code
        self.details = details or {}
        super().__init__(code)


ACTIVE_ORGANIZATION_STATUSES = {OrganizationStatus.TRIAL, OrganizationStatus.ACTIVE}
WEBHOOK_CONNECTION_STATUSES = {SMSConnectionStatus.CONNECTED, SMSConnectionStatus.DEGRADED}
SEND_CONNECTION_STATUSES = {SMSConnectionStatus.CONNECTED}
PERMANENT_PROVIDER_ERRORS = {"21211", "21610", "21612", "21614", "21617", "fake_invalid_recipient"}


def _safe_code(value: str, fallback: str = "provider_error") -> str:
    candidate = str(value or "").strip()[:80]
    return candidate if re.fullmatch(r"[A-Za-z0-9_.:-]+", candidate or "") else fallback


def _public_base() -> str:
    value = str(getattr(settings, "SMS_PUBLIC_BASE_URL", "") or "").rstrip("/")
    if value:
        return value
    backend = str(getattr(settings, "BACKEND_DOMAIN", "") or "").rstrip("/")
    if backend:
        return backend if "://" in backend else f"https://{backend}"
    return "http://localhost:8000"


def webhook_urls(connection: SMSConnection) -> dict:
    base = _public_base()
    root = f"{base}/api/v1/webhooks/twilio/sms/{connection.webhook_public_key}"
    return {"inbound": f"{root}/inbound/", "status": f"{root}/status/"}


def integration_readiness() -> dict:
    base = _public_base()
    live_missing = []
    for setting_name in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"):
        if not getattr(settings, setting_name, ""):
            live_missing.append(setting_name)
    if not base.startswith("https://"):
        live_missing.append("SMS_PUBLIC_BASE_URL_HTTPS")
    return {
        "mode": "live" if settings.SMS_ENABLE_LIVE else "development",
        "enabled": bool(settings.SMS_FAKE_PROVIDER or settings.SMS_ENABLE_LIVE),
        "fake_provider": bool(settings.SMS_FAKE_PROVIDER),
        "live_ready": bool(settings.SMS_ENABLE_LIVE and not live_missing),
        "missing_live_configuration": live_missing,
        "limits": {
            "human_max_segments": settings.SMS_HUMAN_MAX_SEGMENTS,
            "ai_max_segments": settings.SMS_AI_MAX_SEGMENTS,
            "confirm_above_segments": settings.SMS_CONFIRM_ABOVE_SEGMENTS,
            "organization_sends_per_minute": settings.SMS_ORG_SENDS_PER_MINUTE,
            "recipient_sends_per_minute": settings.SMS_RECIPIENT_SENDS_PER_MINUTE,
            "daily_messages": settings.SMS_DAILY_MESSAGE_LIMIT,
            "daily_segments": settings.SMS_DAILY_SEGMENT_LIMIT,
        },
        "allowed_country_codes": settings.SMS_ALLOWED_COUNTRY_CODES,
        "blocked_country_codes": settings.SMS_BLOCKED_COUNTRY_CODES,
    }


def _credentials_for_webhook(connection: SMSConnection) -> str:
    if connection.ownership_mode == SMSOwnershipMode.PLATFORM_MANAGED:
        return str(settings.TWILIO_AUTH_TOKEN or "")
    return str(connection.auth_token_encrypted or "")


def webhook_auth_token(connection: SMSConnection) -> str:
    return _credentials_for_webhook(connection)


def _assert_connection_relationships(connection: SMSConnection):
    if connection.channel_connection.organization_id != connection.organization_id:
        raise SMSError("tenant_mismatch", status_code=404)
    if connection.channel_connection.type != ChannelType.SMS:
        raise SMSError("channel_mismatch", status_code=404)


@transaction.atomic
def create_connection(*, organization, membership, data: dict) -> SMSConnection:
    from billing.services import BillingError, EntitlementService

    try:
        entitlements = EntitlementService(organization)
        entitlements.require("sms")
        entitlements.require_capacity(
            "max_sms_connections",
            SMSConnection.objects.for_organization(organization).exclude(status=SMSConnectionStatus.DISCONNECTED).count(),
        )
        entitlements.require_capacity(
            "max_channel_connections",
            ChannelConnection.objects.for_organization(organization).exclude(status="disconnected").count(),
        )
    except BillingError as exc:
        raise SMSError(exc.code, status_code=exc.status_code, details=exc.details) from exc
    provider = str(data.get("provider") or SMSProviderType.FAKE)
    ownership_mode = str(data.get("ownership_mode") or SMSOwnershipMode.PLATFORM_MANAGED)
    if provider not in SMSProviderType.values or ownership_mode not in SMSOwnershipMode.values:
        raise SMSError("invalid_connection_mode")
    if provider == SMSProviderType.FAKE and not settings.SMS_FAKE_PROVIDER:
        raise SMSError("fake_provider_disabled", status_code=409)
    if provider == SMSProviderType.TWILIO and not settings.SMS_ENABLE_LIVE:
        raise SMSError("live_provider_disabled", status_code=409)
    if SMSConnection.objects.for_organization(organization).exclude(status=SMSConnectionStatus.DISCONNECTED).exists():
        raise SMSError("active_connection_exists", status_code=409)
    try:
        sender = normalize_phone(str(data.get("sender_address") or "+15550109999"))
    except SMSPayloadError as exc:
        raise SMSError(exc.args[0]) from exc
    channel = ChannelConnection(
        organization=organization,
        type=ChannelType.SMS,
        provider="twilio" if provider == SMSProviderType.TWILIO else "fake_sms",
        display_name=str(data.get("display_name") or f"SMS {sender}")[:160],
        external_identifier=sender,
        status=ChannelStatus.ACTIVE,
        configuration={"ownership_mode": ownership_mode},
    )
    channel.full_clean()
    channel.save()
    connection = SMSConnection(
        organization=organization,
        channel_connection=channel,
        provider=provider,
        ownership_mode=ownership_mode,
        status=SMSConnectionStatus.CONNECTED,
        account_sid=str(data.get("account_sid") or "")[:64],
        messaging_service_sid=str(
            data.get("messaging_service_sid")
            or (
                settings.TWILIO_MESSAGING_SERVICE_SID
                if provider == SMSProviderType.TWILIO
                and ownership_mode == SMSOwnershipMode.PLATFORM_MANAGED
                else ""
            )
        )[:64],
        phone_number_sid=str(data.get("phone_number_sid") or "")[:64],
        sender_address=sender,
        sender_country=str(data.get("sender_country") or "")[:2].upper(),
        sender_capabilities=["sms"],
        api_key_sid=str(data.get("api_key_sid") or "")[:64],
        api_key_secret_encrypted=str(data.get("api_key_secret") or ""),
        auth_token_encrypted=str(data.get("auth_token") or ""),
        inbound_webhook_status="ready",
        status_callback_status="ready",
        advanced_opt_out_enabled=bool(data.get("advanced_opt_out_enabled", False)),
        allow_inbound_support=bool(data.get("allow_inbound_support", True)),
        default_language=str(data.get("default_language") or organization.default_language),
        supported_languages=data.get("supported_languages") or ["ru", "uz", "en"],
        ai_mode=str(data.get("ai_mode") or SMSAutomationMode.MANUAL),
        connected_by=membership,
        connected_at=timezone.now(),
    )
    if provider == SMSProviderType.TWILIO and ownership_mode == SMSOwnershipMode.CUSTOMER_OWNED:
        if not connection.account_sid or not connection.auth_token_encrypted:
            raise SMSError("customer_credentials_required")
    connection.full_clean()
    try:
        connection.save()
    except IntegrityError as exc:
        raise SMSError("sender_already_connected", status_code=409) from exc
    if provider == SMSProviderType.TWILIO:
        try:
            provider_for(connection).health(connection)
        except SMSProviderError as exc:
            raise SMSError(exc.code, status_code=409) from exc
    SMSAuditEvent.objects.create(
        organization=organization,
        connection=connection,
        actor_membership=membership,
        event_type="sms.connection_created",
        metadata={"provider": provider, "ownership_mode": ownership_mode},
    )
    return connection


def connection_health(connection: SMSConnection, *, run_provider=False) -> dict:
    _assert_connection_relationships(connection)
    urls = webhook_urls(connection)
    external_https = all(value.startswith("https://") for value in urls.values())
    provider_health = {"provider_reachable": None, "sender_active": None, "messaging_service_active": None}
    error_code = connection.last_error_code
    if run_provider:
        try:
            provider_health.update(provider_for(connection).health(connection))
            error_code = ""
            connection.last_error_code = ""
            connection.failure_count = 0
            connection.last_health_check_at = timezone.now()
            connection.save(update_fields=["last_error_code", "failure_count", "last_health_check_at", "updated_at"])
        except SMSProviderError as exc:
            error_code = _safe_code(exc.code)
            connection.last_error_code = error_code
            connection.failure_count = min(connection.failure_count + 1, 100)
            connection.last_health_check_at = timezone.now()
            if connection.status == SMSConnectionStatus.CONNECTED:
                connection.status = SMSConnectionStatus.DEGRADED
            connection.save(update_fields=["last_error_code", "failure_count", "last_health_check_at", "status", "updated_at"])
    dead_letters = SMSWebhookEnvelope.objects.for_organization(connection.organization).filter(
        connection=connection, processing_status=SMSWebhookProcessingStatus.FAILED
    ).count()
    failed_attempts = SMSOutboundAttempt.objects.for_organization(connection.organization).filter(
        connection=connection, status=SMSOutboundAttemptStatus.FAILED
    ).count()
    return {
        "status": connection.status,
        "account_reachable": provider_health["provider_reachable"],
        "sender_active": provider_health["sender_active"],
        "messaging_service_active": provider_health["messaging_service_active"],
        "inbound_webhook_url": urls["inbound"],
        "status_callback_url": urls["status"],
        "signature_validation_ready": bool(
            connection.provider == SMSProviderType.FAKE or (_credentials_for_webhook(connection) and external_https)
        ),
        "public_https_ready": external_https,
        "inbound_webhook_status": connection.inbound_webhook_status,
        "status_callback_status": connection.status_callback_status,
        "advanced_opt_out_enabled": connection.advanced_opt_out_enabled,
        "last_inbound_at": connection.last_inbound_at,
        "last_send_at": connection.last_send_at,
        "last_status_callback_at": connection.last_status_callback_at,
        "last_health_check_at": connection.last_health_check_at,
        "last_error_code": error_code,
        "failed_webhook_receipts": dead_letters,
        "failed_outbound_attempts": failed_attempts,
        "limits": integration_readiness()["limits"],
        "allowed_country_codes": settings.SMS_ALLOWED_COUNTRY_CODES,
        "blocked_country_codes": settings.SMS_BLOCKED_COUNTRY_CODES,
    }


@transaction.atomic
def update_connection(connection: SMSConnection, *, membership, data: dict) -> SMSConnection:
    allowed = {
        "advanced_opt_out_enabled",
        "allow_inbound_support",
        "default_language",
        "supported_languages",
        "ai_mode",
    }
    for field in allowed:
        if field in data:
            setattr(connection, field, data[field])
    if connection.ai_mode == SMSAutomationMode.AUTOPILOT:
        from ai_runtime.services import ensure_runtime_config
        from assistant_context.models import OrganizationAssistantProfile

        if not ensure_runtime_config(connection.organization).enabled:
            raise SMSError("runtime_disabled", status_code=409)
        if not OrganizationAssistantProfile.objects.filter(
            organization=connection.organization, status="published", published_at__isnull=False
        ).exists():
            raise SMSError("published_context_required", status_code=409)
    connection.full_clean()
    connection.save()
    SMSAuditEvent.objects.create(
        organization=connection.organization,
        connection=connection,
        actor_membership=membership,
        event_type="sms.connection_updated",
        metadata={"fields": sorted(set(data).intersection(allowed))},
    )
    return connection


@transaction.atomic
def rotate_credentials(connection: SMSConnection, *, membership, data: dict) -> SMSConnection:
    if connection.ownership_mode != SMSOwnershipMode.CUSTOMER_OWNED:
        raise SMSError("platform_credentials_managed_by_deployment", status_code=409)
    auth_token = str(data.get("auth_token") or "")
    api_secret = str(data.get("api_key_secret") or "")
    if not auth_token and not api_secret:
        raise SMSError("replacement_credentials_required")
    if auth_token:
        connection.auth_token_encrypted = auth_token
    if api_secret:
        connection.api_key_secret_encrypted = api_secret
    if data.get("api_key_sid"):
        connection.api_key_sid = str(data["api_key_sid"])[:64]
    connection.last_error_code = ""
    connection.save(update_fields=["auth_token_encrypted", "api_key_secret_encrypted", "api_key_sid", "last_error_code", "updated_at"])
    SMSAuditEvent.objects.create(
        organization=connection.organization,
        connection=connection,
        actor_membership=membership,
        event_type="sms.credentials_rotated",
        metadata={"auth_token_replaced": bool(auth_token), "api_key_replaced": bool(api_secret)},
    )
    return connection


@transaction.atomic
def set_connection_state(connection: SMSConnection, *, membership, action: str) -> SMSConnection:
    now = timezone.now()
    if action == "pause":
        connection.status = SMSConnectionStatus.PAUSED
        connection.channel_connection.status = ChannelStatus.ERROR
    elif action == "activate":
        connection.status = SMSConnectionStatus.CONNECTED
        connection.channel_connection.status = ChannelStatus.ACTIVE
    elif action == "disconnect":
        connection.status = SMSConnectionStatus.DISCONNECTED
        connection.channel_connection.status = ChannelStatus.DISCONNECTED
        connection.disconnected_at = now
        connection.auth_token_encrypted = ""
        connection.api_key_secret_encrypted = ""
    else:
        raise SMSError("invalid_connection_action")
    connection.channel_connection.save(update_fields=["status", "updated_at"])
    connection.save()
    SMSAuditEvent.objects.create(
        organization=connection.organization,
        connection=connection,
        actor_membership=membership,
        event_type=f"sms.connection_{action}d",
        metadata={},
    )
    return connection


def resolve_webhook_candidate(public_key: str) -> SMSConnection:
    try:
        connection = SMSConnection.objects.select_related("organization", "channel_connection").get(
            webhook_public_key=public_key,
            provider__in=[SMSProviderType.TWILIO, SMSProviderType.FAKE],
        )
    except SMSConnection.DoesNotExist as exc:
        raise SMSError("unknown_connection", status_code=404) from exc
    if connection.status not in WEBHOOK_CONNECTION_STATUSES:
        raise SMSError("inactive_connection", status_code=404)
    if connection.organization.status not in ACTIVE_ORGANIZATION_STATUSES:
        raise SMSError("organization_read_only", status_code=404)
    _assert_connection_relationships(connection)
    return connection


def _destination_matches(connection: SMSConnection, *, to_address: str, messaging_service_sid: str) -> bool:
    normalized_to = ""
    try:
        normalized_to = normalize_phone(to_address)
    except SMSPayloadError:
        pass
    return bool(
        normalized_to == connection.sender_address
        or (
            connection.messaging_service_sid
            and messaging_service_sid
            and connection.messaging_service_sid == messaging_service_sid
        )
    )


@transaction.atomic
def receive_verified_inbound(*, connection: SMSConnection, params) -> tuple[SMSWebhookEnvelope, bool]:
    try:
        inbound = parse_inbound(params)
    except SMSPayloadError as exc:
        raise SMSError(str(exc), status_code=400) from exc
    if not _destination_matches(
        connection, to_address=inbound.to_address, messaging_service_sid=inbound.messaging_service_sid
    ):
        raise SMSError("destination_mismatch", status_code=404)
    event_key = inbound_event_key(inbound.message_sid)
    existing = SMSWebhookEnvelope.objects.for_organization(connection.organization).filter(
        connection=connection, event_key=event_key
    ).first()
    if existing:
        return existing, False
    minute = timezone.now().strftime("%Y%m%d%H%M")
    sender_hash = hashlib.sha256(inbound.from_address.encode()).hexdigest()[:16]
    flood_key = f"sms:inbound:{connection.id}:{sender_hash}:{minute}"
    if cache.add(flood_key, 1, timeout=90):
        inbound_count = 1
    else:
        inbound_count = cache.incr(flood_key)
    if inbound_count > settings.SMS_INBOUND_PER_RECIPIENT_MINUTE:
        raise SMSError("inbound_rate_limit_exceeded", status_code=429)
    envelope, created = SMSWebhookEnvelope.objects.get_or_create(
        organization=connection.organization,
        connection=connection,
        event_key=event_key,
        defaults={
            "provider_message_sid": inbound.message_sid,
            "event_type": "inbound",
            "from_address": inbound.from_address,
            "to_address": inbound.to_address,
            "body": inbound.body,
            "num_media": inbound.num_media,
            "messaging_service_sid": inbound.messaging_service_sid,
            "opt_out_type": inbound.opt_out_type,
        },
    )
    if created:
        from sms.tasks import process_sms_inbound

        transaction.on_commit(lambda: process_sms_inbound.delay(str(envelope.id)))
    return envelope, created


@transaction.atomic
def receive_verified_status(*, connection: SMSConnection, params) -> tuple[SMSWebhookEnvelope, bool]:
    try:
        callback = parse_status(params)
    except SMSPayloadError as exc:
        raise SMSError(str(exc), status_code=400) from exc
    if callback.messaging_service_sid and connection.messaging_service_sid:
        destination_ok = callback.messaging_service_sid == connection.messaging_service_sid
    else:
        destination_ok = callback.from_address == connection.sender_address
    if not destination_ok:
        raise SMSError("destination_mismatch", status_code=404)
    key = status_event_key(callback)
    envelope, created = SMSWebhookEnvelope.objects.get_or_create(
        organization=connection.organization,
        connection=connection,
        event_key=key,
        defaults={
            "provider_message_sid": callback.message_sid,
            "event_type": "status",
            "from_address": callback.from_address,
            "to_address": callback.to_address,
            "messaging_service_sid": callback.messaging_service_sid,
            "provider_status": callback.status,
            "provider_error_code": callback.error_code,
            "provider_segments": callback.segments,
        },
    )
    if created:
        from sms.tasks import process_sms_status

        transaction.on_commit(lambda: process_sms_status.delay(str(envelope.id)))
    return envelope, created


@transaction.atomic
def process_inbound_envelope(envelope_id) -> SMSWebhookEnvelope:
    envelope = SMSWebhookEnvelope.objects.select_for_update().select_related(
        "organization", "connection__channel_connection"
    ).get(pk=envelope_id)
    if envelope.processing_status == SMSWebhookProcessingStatus.PROCESSED:
        return envelope
    envelope.processing_status = SMSWebhookProcessingStatus.PROCESSING
    envelope.save(update_fields=["processing_status"])
    connection = SMSConnection.objects.select_for_update().select_related("channel_connection", "organization").get(
        pk=envelope.connection_id
    )
    try:
        if connection.status not in WEBHOOK_CONNECTION_STATUSES:
            raise SMSError("inactive_connection")
        message, created = ingest_inbound_message(
            organization=connection.organization,
            channel_connection=connection.channel_connection,
            identity_type=ContactIdentityType.PHONE,
            sender_value=envelope.from_address,
            sender_display_name=envelope.from_address,
            external_thread_id=envelope.from_address,
            provider_message_id=envelope.provider_message_sid,
            body=envelope.body or f"[MMS: {envelope.num_media} media item(s)]",
            metadata={
                "provider": "sms",
                "raw_phone": envelope.from_address,
                "media_count": envelope.num_media,
                "segments": estimate_segments(envelope.body).as_dict(),
            },
            enqueue_ai=False,
        )
        identity = ContactIdentity.objects.for_organization(connection.organization).get(
            contact=message.conversation.contact,
            channel_connection=connection.channel_connection,
            type=ContactIdentityType.PHONE,
            normalized_value=envelope.from_address,
        )
        decision = apply_inbound_consent(
            connection=connection,
            contact_identity=identity,
            body=envelope.body,
            provider_signal=envelope.opt_out_type,
        )
        message.metadata = {
            **message.metadata,
            "consent_state": decision.state,
            "compliance_keyword": decision.keyword_type,
        }
        message.full_clean()
        message.save(update_fields=["metadata", "updated_at"])
        if created and decision.ai_eligible:
            transaction.on_commit(lambda: _enqueue_ai_inbound(message.id))
        connection.last_inbound_at = timezone.now()
        connection.inbound_webhook_status = "ready"
        connection.last_error_code = ""
        connection.save(update_fields=["last_inbound_at", "inbound_webhook_status", "last_error_code", "updated_at"])
        envelope.processing_status = SMSWebhookProcessingStatus.PROCESSED
        envelope.processed_at = timezone.now()
        envelope.redacted_error = ""
        envelope.body = ""
    except Exception as exc:
        envelope.processing_status = SMSWebhookProcessingStatus.FAILED
        envelope.processed_at = timezone.now()
        envelope.redacted_error = _safe_code(getattr(exc, "code", type(exc).__name__), "processing_failed")
        connection.inbound_webhook_status = "error"
        connection.last_error_code = envelope.redacted_error
        connection.save(update_fields=["inbound_webhook_status", "last_error_code", "updated_at"])
    envelope.save(update_fields=["processing_status", "processed_at", "redacted_error", "body"])
    return envelope


def _rate_limit(connection: SMSConnection, recipient: str):
    minute = timezone.now().strftime("%Y%m%d%H%M")
    recipient_hash = hashlib.sha256(recipient.encode()).hexdigest()[:16]
    limits = [
        (f"sms:send:org:{connection.organization_id}:{minute}", settings.SMS_ORG_SENDS_PER_MINUTE),
        (f"sms:send:recipient:{connection.id}:{recipient_hash}:{minute}", settings.SMS_RECIPIENT_SENDS_PER_MINUTE),
    ]
    for key, limit in limits:
        if cache.add(key, 1, timeout=90):
            count = 1
        else:
            count = cache.incr(key)
        if count > limit:
            raise SMSError("rate_limit_exceeded", status_code=429)


def _daily_quota(connection: SMSConnection, *, segments: int):
    summary = SMSOutboundAttempt.objects.for_organization(connection.organization).filter(
        created_at__date=timezone.localdate()
    ).aggregate(messages=Count("id"), segments=Sum("segment_count_estimated"))
    if int(summary["messages"] or 0) + 1 > settings.SMS_DAILY_MESSAGE_LIMIT:
        raise SMSError("daily_message_limit_exceeded", status_code=429)
    if int(summary["segments"] or 0) + segments > settings.SMS_DAILY_SEGMENT_LIMIT:
        raise SMSError("daily_segment_limit_exceeded", status_code=429)


def _repeat_content_guard(connection: SMSConnection, *, recipient: str, body: str):
    material = f"{connection.id}|{recipient}|{body}".encode()
    digest = hashlib.sha256(material).hexdigest()
    key = f"sms:repeat:{digest}"
    if not cache.add(key, 1, timeout=settings.SMS_REPEAT_WINDOW_SECONDS):
        raise SMSError("repeated_content_blocked", status_code=409)


def _conversation_consent(conversation: Conversation) -> SMSConsent | None:
    try:
        connection = conversation.channel_connection.sms_connection
    except SMSConnection.DoesNotExist:
        return None
    identity = ContactIdentity.objects.for_organization(conversation.organization).filter(
        contact=conversation.contact,
        channel_connection=conversation.channel_connection,
        type=ContactIdentityType.PHONE,
        normalized_value=conversation.external_thread_id,
    ).first()
    if not identity:
        return None
    return SMSConsent.objects.for_organization(conversation.organization).filter(
        connection=connection, contact_identity=identity
    ).first()


def conversation_policy(conversation: Conversation) -> dict:
    try:
        connection = SMSConnection.objects.select_related("organization").get(
            channel_connection=conversation.channel_connection
        )
    except SMSConnection.DoesNotExist:
        return {"state": "provider_unavailable", "can_send": False}
    consent = _conversation_consent(conversation)
    if connection.organization.status not in ACTIVE_ORGANIZATION_STATUSES:
        state = "organization_read_only"
    elif connection.status == SMSConnectionStatus.PAUSED:
        state = "connection_paused"
    elif connection.status not in SEND_CONNECTION_STATUSES:
        state = "provider_degraded" if connection.status == SMSConnectionStatus.DEGRADED else "provider_unavailable"
    elif not consent_allows_send(consent):
        state = "opted_out" if consent and consent.state in {SMSConsentState.OPTED_OUT, SMSConsentState.BLOCKED} else "consent_required"
    else:
        state = "can_reply"
    estimate = estimate_segments("")
    return {
        "state": state,
        "can_send": state == "can_reply",
        "sms_connection_id": str(connection.id),
        "sender_address": connection.sender_address,
        "connection_status": connection.status,
        "consent_state": consent.state if consent else SMSConsentState.UNKNOWN,
        "ai_mode": connection.ai_mode,
        "encoding": estimate.encoding,
        "max_segments": settings.SMS_HUMAN_MAX_SEGMENTS,
        "confirm_above_segments": settings.SMS_CONFIRM_ABOVE_SEGMENTS,
        "supports_read_receipts": False,
    }


def can_send_sms(conversation: Conversation) -> bool:
    return bool(
        conversation_policy(conversation).get("can_send")
        and operation_allowed(
            organization=conversation.organization,
            provider_type="sms",
            channel_connection=conversation.channel_connection,
        )
    )


def sms_autopilot_allowed(conversation: Conversation) -> bool:
    try:
        connection = conversation.channel_connection.sms_connection
    except SMSConnection.DoesNotExist:
        return False
    return bool(
        connection.ai_mode == SMSAutomationMode.AUTOPILOT
        and conversation.ai_state == ConversationAIState.AUTOPILOT_SMS
        and can_send_sms(conversation)
    )


def ai_state_for_connection(organization, channel_connection):
    if channel_connection.type != ChannelType.SMS:
        return None
    try:
        mode = channel_connection.sms_connection.ai_mode
    except SMSConnection.DoesNotExist:
        return ConversationAIState.OFF
    return {
        SMSAutomationMode.MANUAL: ConversationAIState.OFF,
        SMSAutomationMode.SUGGEST: ConversationAIState.SUGGEST,
        SMSAutomationMode.AUTOPILOT: ConversationAIState.AUTOPILOT_SMS,
    }[mode]


def _status_callback_url(connection: SMSConnection) -> str:
    url = webhook_urls(connection)["status"]
    if connection.provider == SMSProviderType.TWILIO and not url.startswith("https://"):
        raise SMSError("public_https_required", status_code=409)
    return url


@transaction.atomic
def send_sms_message(
    *, conversation: Conversation, body: str, client_message_id: str, membership=None,
    sender_type=MessageSenderType.AGENT, confirm_segments=False, metadata=None,
):
    conversation = Conversation.objects.select_for_update().select_related(
        "organization", "channel_connection", "contact"
    ).get(pk=conversation.pk)
    if conversation.channel_type != ChannelType.SMS:
        raise SMSError("channel_mismatch")
    existing = Message.objects.for_organization(conversation.organization).filter(
        conversation=conversation, client_message_id=client_message_id
    ).first()
    if existing:
        return existing, False
    policy = conversation_policy(conversation)
    if not policy["can_send"]:
        raise SMSError(str(policy["state"]), status_code=409)
    if not operation_allowed(
        organization=conversation.organization,
        provider_type="sms",
        channel_connection=conversation.channel_connection,
    ):
        raise SMSError("operational_control_active", status_code=409)
    connection = SMSConnection.objects.select_for_update().get(channel_connection=conversation.channel_connection)
    try:
        recipient = normalize_phone(conversation.external_thread_id)
    except SMSPayloadError as exc:
        raise SMSError("invalid_recipient", status_code=409) from exc
    from sms.fraud import SMSFraudPolicyError, fraud_policy

    try:
        fraud_policy().validate_recipient(recipient)
    except SMSFraudPolicyError as exc:
        raise SMSError(str(exc), status_code=409) from exc
    identity = ContactIdentity.objects.for_organization(conversation.organization).filter(
        contact=conversation.contact,
        channel_connection=conversation.channel_connection,
        type=ContactIdentityType.PHONE,
        normalized_value=recipient,
    ).first()
    locked_consent = (
        SMSConsent.objects.select_for_update().for_organization(conversation.organization).filter(
            connection=connection, contact_identity=identity
        ).first()
        if identity
        else None
    )
    if not consent_allows_send(locked_consent):
        state = (
            "opted_out"
            if locked_consent and locked_consent.state in {SMSConsentState.OPTED_OUT, SMSConsentState.BLOCKED}
            else "consent_required"
        )
        raise SMSError(state, status_code=409)
    from billing.services import BillingError, EntitlementService

    try:
        entitlements = EntitlementService(conversation.organization)
        entitlements.require("sms")
        entitlements.require("monthly_external_messages")
    except BillingError as exc:
        raise SMSError(exc.code, status_code=exc.status_code, details=exc.details) from exc
    _rate_limit(connection, recipient)
    text = str(body or "").strip()
    if not text:
        raise SMSError("empty_message")
    estimate = estimate_segments(text)
    limit = settings.SMS_AI_MAX_SEGMENTS if sender_type == MessageSenderType.AI else settings.SMS_HUMAN_MAX_SEGMENTS
    if estimate.segments > limit:
        raise SMSError("segment_limit_exceeded", details=estimate.as_dict())
    if (
        sender_type != MessageSenderType.AI
        and estimate.segments > settings.SMS_CONFIRM_ABOVE_SEGMENTS
        and not confirm_segments
    ):
        raise SMSError("segment_confirmation_required", status_code=409, details=estimate.as_dict())
    _daily_quota(connection, segments=estimate.segments)
    _repeat_content_guard(connection, recipient=recipient, body=text)
    now = timezone.now()
    message = Message(
        organization=conversation.organization,
        conversation=conversation,
        channel_connection=conversation.channel_connection,
        direction=MessageDirection.OUTBOUND,
        sender_type=sender_type,
        sender_membership=membership,
        client_message_id=client_message_id,
        content_type=MessageContentType.TEXT,
        body=text,
        status=MessageStatus.QUEUED,
        metadata={
            "provider": "sms",
            "recipient": recipient,
            "segments": estimate.as_dict(),
            "supports_read_receipts": False,
            **(metadata or {}),
        },
        occurred_at=now,
    )
    message.full_clean()
    message.save()
    attempt = SMSOutboundAttempt.objects.create(
        organization=conversation.organization,
        connection=connection,
        message=message,
        status=SMSOutboundAttemptStatus.SENDING,
        attempt_count=1,
        segment_count_estimated=estimate.segments,
        encoding=estimate.encoding,
    )
    send_lock_key = f"sms:provider-send:{connection.id}"
    if not cache.add(send_lock_key, str(message.id), timeout=settings.SMS_SEND_LOCK_SECONDS):
        raise SMSError("send_in_progress", status_code=409)
    try:
        if not operation_allowed(
            organization=conversation.organization,
            provider_type="sms",
            channel_connection=conversation.channel_connection,
        ):
            attempt.status = SMSOutboundAttemptStatus.FAILED
            attempt.retryable = False
            attempt.last_error_code = "operational_control_active"
            attempt.save(update_fields=["status", "retryable", "last_error_code", "updated_at"])
            return {"status": "policy_blocked", "retry": False}
        result = provider_for(connection).send(
            connection=connection,
            to=recipient,
            body=text,
            status_callback=_status_callback_url(connection),
        )
    except SMSProviderError as exc:
        safe_code = _safe_code(exc.code)
        retryable = bool(
            exc.transient
            and safe_code not in PERMANENT_PROVIDER_ERRORS
            and settings.SMS_MAX_SEND_ATTEMPTS > 1
        )
        attempt.status = SMSOutboundAttemptStatus.FAILED
        attempt.last_error_code = safe_code
        attempt.retryable = retryable
        attempt.next_retry_at = (
            now + timedelta(seconds=settings.SMS_RETRY_BASE_SECONDS) if retryable else None
        )
        attempt.save(
            update_fields=[
                "status",
                "last_error_code",
                "retryable",
                "next_retry_at",
                "updated_at",
            ]
        )
        message.status = MessageStatus.FAILED
        message.error_code = safe_code
        message.save(update_fields=["status", "error_code", "updated_at"])
        connection.last_error_code = safe_code
        connection.failure_count = min(connection.failure_count + 1, 100)
        if connection.failure_count >= settings.SMS_CIRCUIT_BREAKER_FAILURES:
            connection.status = SMSConnectionStatus.DEGRADED
            connection.circuit_open_until = now + timedelta(seconds=settings.SMS_CIRCUIT_BREAKER_SECONDS)
        connection.save(update_fields=["last_error_code", "failure_count", "status", "circuit_open_until", "updated_at"])
        record_activity(
            organization=conversation.organization,
            actor_membership=membership,
            event_type="message.failed",
            summary="SMS provider rejected the outbound message",
            contact=conversation.contact,
            conversation=conversation,
            metadata={"provider": "sms", "error_code": safe_code},
        )
        if retryable:
            from sms.tasks import retry_sms_outbound

            transaction.on_commit(
                lambda: retry_sms_outbound.apply_async(
                    args=[str(attempt.id)], countdown=settings.SMS_RETRY_BASE_SECONDS
                )
            )
        return message, True
    finally:
        cache.delete(send_lock_key)
    attempt.status = SMSOutboundAttemptStatus.ACCEPTED
    attempt.provider_message_sid = result.message_sid
    attempt.retryable = False
    attempt.next_retry_at = None
    attempt.save(
        update_fields=["status", "provider_message_sid", "retryable", "next_retry_at", "updated_at"]
    )
    message.provider_message_id = result.message_sid
    message.status = MessageStatus.QUEUED
    if result.provider_segments:
        message.metadata = {**message.metadata, "provider_segments": result.provider_segments}
    message.save(update_fields=["provider_message_id", "status", "metadata", "updated_at"])
    Conversation.objects.filter(pk=conversation.pk).update(last_message_at=now, last_outbound_at=now)
    connection.last_send_at = now
    connection.last_error_code = ""
    connection.save(update_fields=["last_send_at", "last_error_code", "updated_at"])
    if membership:
        Conversation.objects.filter(pk=conversation.pk).update(
            ai_state=ConversationAIState.PAUSED_BY_HUMAN, ai_state_updated_at=now
        )
        try:
            from ai_runtime.services import supersede_active_runs

            supersede_active_runs(conversation=conversation, reason="human_reply")
        except ImportError:
            pass
    record_activity(
        organization=conversation.organization,
        actor_membership=membership,
        event_type="message.queued",
        summary="SMS queued for provider delivery",
        contact=conversation.contact,
        conversation=conversation,
        metadata={"provider": "sms", "segments": estimate.segments},
    )
    from billing.services import record_message_usage, record_usage

    billed_segments = result.provider_segments or estimate.segments
    record_usage(
        organization=conversation.organization,
        meter_key="sms_segments",
        quantity=billed_segments,
        unit="segment",
        source_type="sms_message",
        source_id=str(message.id),
        idempotency_key=f"sms:{message.id}:segments",
        occurred_at=now,
        metadata={"provider": connection.provider},
    )
    record_message_usage(message)
    return message, True


@transaction.atomic
def retry_outbound_attempt(attempt_id) -> dict:
    attempt = SMSOutboundAttempt.objects.select_for_update().select_related(
        "connection__organization", "message__conversation__channel_connection"
    ).get(pk=attempt_id)
    if attempt.status == SMSOutboundAttemptStatus.ACCEPTED:
        return {"status": "accepted", "retry": False}
    if not attempt.retryable or attempt.attempt_count >= settings.SMS_MAX_SEND_ATTEMPTS:
        attempt.retryable = False
        attempt.next_retry_at = None
        attempt.save(update_fields=["retryable", "next_retry_at", "updated_at"])
        return {"status": "exhausted", "retry": False}
    message = attempt.message
    conversation = message.conversation
    connection = attempt.connection
    if Message.objects.for_organization(attempt.organization).filter(
        conversation=conversation,
        direction=MessageDirection.OUTBOUND,
        created_at__gt=message.created_at,
    ).exists():
        attempt.retryable = False
        attempt.next_retry_at = None
        attempt.last_error_code = "superseded_by_newer_reply"
        attempt.save(
            update_fields=["retryable", "next_retry_at", "last_error_code", "updated_at"]
        )
        return {"status": "superseded", "retry": False}
    if not conversation_policy(conversation)["can_send"]:
        attempt.retryable = False
        attempt.next_retry_at = None
        attempt.last_error_code = "policy_blocked_retry"
        attempt.save(
            update_fields=["retryable", "next_retry_at", "last_error_code", "updated_at"]
        )
        return {"status": "policy_blocked", "retry": False}
    recipient = normalize_phone(conversation.external_thread_id)
    from sms.fraud import SMSFraudPolicyError, fraud_policy

    try:
        fraud_policy().validate_recipient(recipient)
    except SMSFraudPolicyError:
        attempt.retryable = False
        attempt.next_retry_at = None
        attempt.last_error_code = "fraud_policy_blocked_retry"
        attempt.save(
            update_fields=["retryable", "next_retry_at", "last_error_code", "updated_at"]
        )
        return {"status": "fraud_policy_blocked", "retry": False}
    _rate_limit(connection, recipient)
    lock_key = f"sms:provider-send:{connection.id}"
    if not cache.add(lock_key, str(message.id), timeout=settings.SMS_SEND_LOCK_SECONDS):
        return {"status": "busy", "retry": True}
    attempt.attempt_count += 1
    attempt.status = SMSOutboundAttemptStatus.SENDING
    attempt.next_retry_at = None
    attempt.save(update_fields=["attempt_count", "status", "next_retry_at", "updated_at"])
    try:
        result = provider_for(connection).send(
            connection=connection,
            to=recipient,
            body=message.body,
            status_callback=_status_callback_url(connection),
        )
    except SMSProviderError as exc:
        code = _safe_code(exc.code)
        can_retry = bool(
            exc.transient
            and code not in PERMANENT_PROVIDER_ERRORS
            and attempt.attempt_count < settings.SMS_MAX_SEND_ATTEMPTS
        )
        attempt.status = SMSOutboundAttemptStatus.FAILED
        attempt.last_error_code = code
        attempt.retryable = can_retry
        delay = settings.SMS_RETRY_BASE_SECONDS * (2 ** (attempt.attempt_count - 1))
        attempt.next_retry_at = timezone.now() + timedelta(seconds=delay) if can_retry else None
        attempt.save(
            update_fields=[
                "status",
                "last_error_code",
                "retryable",
                "next_retry_at",
                "updated_at",
            ]
        )
        message.status = MessageStatus.FAILED
        message.error_code = code
        message.save(update_fields=["status", "error_code", "updated_at"])
        return {"status": "failed", "retry": can_retry, "countdown": delay}
    finally:
        cache.delete(lock_key)
    attempt.status = SMSOutboundAttemptStatus.ACCEPTED
    attempt.provider_message_sid = result.message_sid
    attempt.last_error_code = ""
    attempt.retryable = False
    attempt.next_retry_at = None
    attempt.save(
        update_fields=[
            "status",
            "provider_message_sid",
            "last_error_code",
            "retryable",
            "next_retry_at",
            "updated_at",
        ]
    )
    message.provider_message_id = result.message_sid
    message.status = MessageStatus.QUEUED
    message.error_code = ""
    message.save(update_fields=["provider_message_id", "status", "error_code", "updated_at"])
    connection.last_send_at = timezone.now()
    connection.last_error_code = ""
    connection.save(update_fields=["last_send_at", "last_error_code", "updated_at"])
    return {"status": "accepted", "retry": False}


@transaction.atomic
def request_outbound_retry(*, connection, message_id, membership) -> SMSOutboundAttempt:
    try:
        attempt = SMSOutboundAttempt.objects.select_for_update().for_organization(
            connection.organization
        ).select_related("message").get(
            connection=connection,
            message_id=message_id,
            status=SMSOutboundAttemptStatus.FAILED,
        )
    except SMSOutboundAttempt.DoesNotExist as exc:
        raise SMSError("failed_attempt_not_found", status_code=404) from exc
    if attempt.attempt_count >= settings.SMS_MAX_SEND_ATTEMPTS:
        raise SMSError("retry_attempts_exhausted", status_code=409)
    attempt.retryable = True
    attempt.next_retry_at = timezone.now()
    attempt.save(update_fields=["retryable", "next_retry_at", "updated_at"])
    SMSAuditEvent.objects.create(
        organization=connection.organization,
        connection=connection,
        actor_membership=membership,
        event_type="sms.outbound_retry_requested",
        metadata={"message_id": str(attempt.message_id), "attempt_count": attempt.attempt_count},
    )
    from sms.tasks import retry_sms_outbound

    transaction.on_commit(lambda: retry_sms_outbound.delay(str(attempt.id)))
    return attempt


def send_ai_message(*, run, body, client_message_id, metadata):
    return send_sms_message(
        conversation=run.conversation,
        body=body,
        client_message_id=client_message_id,
        sender_type=MessageSenderType.AI,
        metadata={"ai_generated": True, **metadata},
    )


STATUS_MAP = {
    "accepted": MessageStatus.QUEUED,
    "scheduled": MessageStatus.QUEUED,
    "queued": MessageStatus.QUEUED,
    "sending": MessageStatus.SENDING,
    "sent": MessageStatus.SENT,
    "delivered": MessageStatus.DELIVERED,
    "undelivered": MessageStatus.UNDELIVERED,
    "failed": MessageStatus.FAILED,
    "canceled": MessageStatus.CANCELED,
}
STATUS_RANK = {
    MessageStatus.QUEUED: 0,
    MessageStatus.SENDING: 1,
    MessageStatus.SENT: 2,
    MessageStatus.DELIVERED: 3,
    MessageStatus.UNDELIVERED: 3,
    MessageStatus.FAILED: 3,
    MessageStatus.CANCELED: 3,
}
TERMINAL_STATUSES = {MessageStatus.DELIVERED, MessageStatus.UNDELIVERED, MessageStatus.FAILED, MessageStatus.CANCELED}


@transaction.atomic
def process_status_envelope(envelope_id) -> SMSWebhookEnvelope:
    envelope = SMSWebhookEnvelope.objects.select_for_update().select_related("connection__organization").get(pk=envelope_id)
    if envelope.processing_status == SMSWebhookProcessingStatus.PROCESSED:
        return envelope
    envelope.processing_status = SMSWebhookProcessingStatus.PROCESSING
    envelope.save(update_fields=["processing_status"])
    mapped = STATUS_MAP.get(envelope.provider_status)
    if envelope.provider_status == "read":
        connection = envelope.connection
        connection.last_status_callback_at = timezone.now()
        connection.status_callback_status = "ready"
        connection.save(update_fields=["last_status_callback_at", "status_callback_status", "updated_at"])
        envelope.processing_status = SMSWebhookProcessingStatus.PROCESSED
        envelope.processed_at = timezone.now()
        envelope.redacted_error = "sms_read_status_ignored"
        envelope.save(update_fields=["processing_status", "processed_at", "redacted_error"])
        return envelope
    try:
        if not mapped:
            raise SMSError("unsupported_provider_status")
        message = Message.objects.select_for_update().for_organization(envelope.organization).get(
            channel_connection=envelope.connection.channel_connection,
            provider_message_id=envelope.provider_message_sid,
            direction=MessageDirection.OUTBOUND,
        )
        SMSStatusEvent.objects.get_or_create(
            organization=envelope.organization,
            connection=envelope.connection,
            message=message,
            event_key=envelope.event_key,
            defaults={
                "provider_message_sid": envelope.provider_message_sid,
                "provider_status": envelope.provider_status,
                "mapped_status": mapped,
                "provider_error_code": envelope.provider_error_code,
                "provider_segments": envelope.provider_segments,
            },
        )
        current_rank = STATUS_RANK.get(message.status, -1)
        next_rank = STATUS_RANK[mapped]
        apply_update = not (
            message.status in TERMINAL_STATUSES
            or next_rank < current_rank
            or (next_rank == current_rank and message.status != mapped)
        )
        if apply_update:
            message.status = mapped
            message.error_code = (
                _safe_code(envelope.provider_error_code, "delivery_failed")
                if mapped in {MessageStatus.UNDELIVERED, MessageStatus.FAILED}
                else ""
            )
            if envelope.provider_segments:
                message.metadata = {**message.metadata, "provider_segments": envelope.provider_segments}
            message.full_clean()
            message.save(update_fields=["status", "error_code", "metadata", "updated_at"])
            record_activity(
                organization=envelope.organization,
                event_type=f"message.{mapped}",
                summary=f"SMS delivery status changed to {mapped}",
                contact=message.conversation.contact,
                conversation=message.conversation,
                metadata={"provider": "sms", "status": mapped},
            )
            if mapped in {MessageStatus.UNDELIVERED, MessageStatus.FAILED}:
                Conversation.objects.filter(pk=message.conversation_id).update(
                    ai_state=ConversationAIState.PAUSED_BY_HUMAN,
                    ai_state_updated_at=timezone.now(),
                    handoff_reason="sms_delivery_failed",
                )
        connection = envelope.connection
        connection.last_status_callback_at = timezone.now()
        connection.status_callback_status = "ready"
        connection.save(update_fields=["last_status_callback_at", "status_callback_status", "updated_at"])
        envelope.processing_status = SMSWebhookProcessingStatus.PROCESSED
        envelope.redacted_error = ""
    except Exception as exc:
        envelope.processing_status = SMSWebhookProcessingStatus.FAILED
        envelope.redacted_error = _safe_code(getattr(exc, "code", type(exc).__name__), "status_processing_failed")
    envelope.processed_at = timezone.now()
    envelope.save(update_fields=["processing_status", "processed_at", "redacted_error"])
    return envelope


@transaction.atomic
def update_consent_by_employee(*, connection, contact_identity, membership, state: str) -> SMSConsent:
    if state not in {SMSConsentState.OPTED_IN, SMSConsentState.BLOCKED, SMSConsentState.INVALID}:
        raise SMSError("employee_transition_not_allowed")
    consent, _ = SMSConsent.objects.select_for_update().get_or_create(
        organization=connection.organization,
        connection=connection,
        contact_identity=contact_identity,
    )
    if consent.state == SMSConsentState.OPTED_OUT and state == SMSConsentState.OPTED_IN:
        raise SMSError("provider_opt_out_is_authoritative", status_code=409)
    previous = consent.state
    consent.state = state
    consent.source = SMSConsentSource.EMPLOYEE
    consent.updated_by = membership
    consent.consented_at = timezone.now() if state == SMSConsentState.OPTED_IN else consent.consented_at
    consent.save()
    SMSAuditEvent.objects.create(
        organization=connection.organization,
        connection=connection,
        actor_membership=membership,
        event_type="sms.consent_employee_update",
        metadata={"from": previous, "to": state},
    )
    return consent


def privacy_export(*, connection, contact) -> dict:
    identities = list(ContactIdentity.objects.for_organization(connection.organization).filter(
        contact=contact, channel_connection=connection.channel_connection, type=ContactIdentityType.PHONE
    ))
    consents = SMSConsent.objects.for_organization(connection.organization).filter(
        connection=connection, contact_identity__in=identities
    )
    messages = Message.objects.for_organization(connection.organization).filter(
        conversation__contact=contact, channel_connection=connection.channel_connection
    ).order_by("occurred_at")[:1000]
    return {
        "export_version": 1,
        "generated_at": timezone.now(),
        "organization_id": str(connection.organization_id),
        "connection_id": str(connection.id),
        "contact_id": str(contact.id),
        "phone_identities": [item.raw_value for item in identities],
        "consents": [
            {"state": row.state, "source": row.source, "updated_at": row.updated_at} for row in consents
        ],
        "messages": [
            {
                "id": str(row.id),
                "direction": row.direction,
                "body": row.body,
                "status": row.status,
                "occurred_at": row.occurred_at,
            }
            for row in messages
        ],
        "truncated": messages.count() == 1000,
    }


@transaction.atomic
def privacy_erase(*, connection, contact, membership, mode: str) -> dict:
    if mode not in {"anonymize", "delete"}:
        raise SMSError("invalid_privacy_mode")
    messages = Message.objects.for_organization(connection.organization).filter(
        conversation__contact=contact, channel_connection=connection.channel_connection
    )
    affected = messages.count()
    if mode == "delete":
        messages.delete()
    else:
        messages.update(body="[redacted by privacy request]", metadata={"provider": "sms", "privacy_redacted": True})
    consent_rows = SMSConsent.objects.for_organization(connection.organization).filter(
        connection=connection, contact_identity__contact=contact
    )
    consent_rows.exclude(
        state__in=[SMSConsentState.OPTED_OUT, SMSConsentState.BLOCKED, SMSConsentState.INVALID]
    ).delete()
    consent_rows.filter(
        state__in=[SMSConsentState.OPTED_OUT, SMSConsentState.BLOCKED, SMSConsentState.INVALID]
    ).update(last_keyword="", updated_by=None)
    SMSAuditEvent.objects.create(
        organization=connection.organization,
        connection=connection,
        actor_membership=membership,
        event_type="sms.privacy_erased",
        metadata={"mode": mode, "messages_affected": affected},
    )
    return {"mode": mode, "messages_affected": affected}
