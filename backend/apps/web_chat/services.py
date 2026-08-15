from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from assistant_context.models import AssistantContextRevision
from channels.models import ChannelConnection, ChannelStatus, ChannelType
from crm.models import (
    ContactIdentityType,
    ConversationAIState,
    Message,
    MessageContentType,
    MessageDirection,
    MessageSenderType,
    MessageStatus,
)
from crm.services import (
    CrmConflict,
    add_identity,
    create_contact,
    ingest_inbound_message,
    record_activity,
)
from control_plane.policies import operation_allowed
from organizations.models import OrganizationStatus
from web_chat.models import (
    InstallationAIMode,
    InstallationStatus,
    WebChatEvent,
    WebChatInstallation,
    WebChatMetric,
    WebChatSession,
    WebChatSessionStatus,
)


MAX_MESSAGE_LENGTH = 4000
ORIGIN_PROOF_MAX_AGE = 180
TOKEN_BYTES = 32


class WebChatError(Exception):
    def __init__(self, code: str, *, status_code: int = 400):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def normalize_origin(value: str) -> str:
    value = (value or "").strip()
    if "*" in value:
        raise ValidationError("Wildcard origins are not allowed.")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValidationError("Enter an exact http(s) origin.")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValidationError("Origins must not include a path, query, or fragment.")
    host = parsed.hostname.casefold()
    local = host in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (local and (settings.DEBUG or settings.TESTING or settings.E2E_TESTING)):
        raise ValidationError("HTTPS is required for non-local origins.")
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}"


def normalize_allowed_origins(values) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValidationError("At least one allowed origin is required.")
    normalized = sorted({normalize_origin(str(value)) for value in values})
    if len(normalized) > 20:
        raise ValidationError("At most 20 origins are allowed.")
    return normalized


def request_origin(request) -> str:
    try:
        return normalize_origin(request.headers.get("Origin", ""))
    except ValidationError as exc:
        raise WebChatError("origin_not_allowed", status_code=403) from exc


def installation_for_public_key(public_key: str, *, active_only=True):
    rows = WebChatInstallation.objects.select_related("organization", "channel_connection")
    if active_only:
        rows = rows.filter(
            status=InstallationStatus.ACTIVE,
            channel_connection__status=ChannelStatus.ACTIVE,
            organization__status__in=[OrganizationStatus.TRIAL, OrganizationStatus.ACTIVE],
        )
    installation = rows.filter(public_key=public_key).first()
    if not installation or settings.WEB_CHAT_GLOBAL_KILL_SWITCH or not settings.WEB_CHAT_ENABLE_PUBLIC:
        raise WebChatError("installation_unavailable", status_code=404)
    return installation


def validate_installation_origin(installation: WebChatInstallation, origin: str):
    if origin not in installation.allowed_origins:
        metric(installation, "blocked_attempt", category="origin")
        raise WebChatError("origin_not_allowed", status_code=403)


def create_origin_proof(installation: WebChatInstallation, origin: str) -> str:
    validate_installation_origin(installation, origin)
    return signing.TimestampSigner(salt="web-chat-origin-v1").sign(f"{installation.public_key}|{origin}")


def verify_origin_proof(installation: WebChatInstallation, proof: str) -> str:
    try:
        value = signing.TimestampSigner(salt="web-chat-origin-v1").unsign(
            proof, max_age=ORIGIN_PROOF_MAX_AGE
        )
        public_key, origin = value.split("|", 1)
    except (signing.BadSignature, signing.SignatureExpired, ValueError) as exc:
        raise WebChatError("origin_proof_invalid", status_code=403) from exc
    if not hmac.compare_digest(public_key, installation.public_key):
        raise WebChatError("origin_proof_invalid", status_code=403)
    validate_installation_origin(installation, origin)
    return origin


def _secret_key() -> bytes:
    value = settings.WEB_CHAT_SESSION_SIGNING_KEY or settings.SECRET_KEY
    return value.encode("utf-8")


def token_hash(token: str) -> str:
    return hmac.new(_secret_key(), token.encode("utf-8"), hashlib.sha256).hexdigest()


def issue_session_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(TOKEN_BYTES)
    return token, token_hash(token)


def _ip_hash(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",", 1)[0].strip()
    raw = forwarded or request.META.get("REMOTE_ADDR", "unknown")
    bucket = timezone.localdate().isoformat()
    return hmac.new(_secret_key(), f"{bucket}:{raw}".encode(), hashlib.sha256).hexdigest()


def _rate_limit(key: str, limit: int, seconds: int):
    cache_key = f"webchat:rate:{key}"
    if cache.add(cache_key, 1, timeout=seconds):
        return
    try:
        count = cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, timeout=seconds)
        count = 1
    if count > limit:
        raise WebChatError("rate_limited", status_code=429)


def metric(installation, event_type: str, *, session=None, category=""):
    WebChatMetric.objects.create(
        organization=installation.organization,
        installation=installation,
        session=session,
        event_type=event_type[:40],
        safe_category=category[:80],
    )


@transaction.atomic
def create_session(*, installation, origin_proof, consent_accepted, language, request):
    installation = WebChatInstallation.objects.select_for_update().select_related(
        "organization", "channel_connection"
    ).get(pk=installation.pk)
    if installation.status != InstallationStatus.ACTIVE:
        raise WebChatError("installation_unavailable", status_code=404)
    origin = verify_origin_proof(installation, origin_proof)
    if installation.require_consent and not consent_accepted:
        raise WebChatError("consent_required", status_code=409)
    if not operation_allowed(
        organization=installation.organization,
        provider_type="web_chat",
        channel_connection=installation.channel_connection,
    ):
        raise WebChatError("session_unavailable", status_code=403)
    if language not in installation.supported_languages:
        language = installation.default_language
    ip_digest = _ip_hash(request)
    try:
        _rate_limit(f"new:ip:{ip_digest}", settings.WEB_CHAT_SESSIONS_PER_IP_HOUR, 3600)
        _rate_limit(
            f"new:installation:{installation.id}",
            settings.WEB_CHAT_SESSIONS_PER_INSTALLATION_DAY,
            86400,
        )
        _rate_limit(
            f"new:organization:{installation.organization_id}",
            settings.WEB_CHAT_SESSIONS_PER_ORGANIZATION_DAY,
            86400,
        )
    except WebChatError:
        metric(installation, "rate_limited", category="session_creation")
        raise
    token, digest = issue_session_token()
    now = timezone.now()
    session = WebChatSession(
        organization=installation.organization,
        installation=installation,
        token_hash=digest,
        consented_at=now if consent_accepted else None,
        consent_version=installation.consent_version if consent_accepted else "",
        language=language,
        origin=origin,
        ip_hash=ip_digest,
        expires_at=now + timedelta(hours=settings.WEB_CHAT_SESSION_TTL_HOURS),
    )
    session.full_clean()
    session.save()
    from billing.services import record_usage

    record_usage(
        organization=installation.organization,
        meter_key="web_chat_sessions",
        quantity=1,
        unit="session",
        source_type="web_chat_session",
        source_id=str(session.id),
        idempotency_key=f"web-chat-session:{session.id}",
        occurred_at=now,
        metadata={"provider": "public_web_chat", "language": language},
    )
    publish_event(session=session, event_type="session_started", safe_payload={"language": language})
    metric(installation, "session_start", session=session)
    if consent_accepted:
        metric(installation, "consent", session=session)
    return session, token


def _allowed_request_origins(session: WebChatSession) -> set[str]:
    origins = {session.origin}
    for value in settings.WEB_CHAT_WIDGET_ORIGINS:
        try:
            origins.add(normalize_origin(value))
        except ValidationError:
            continue
    return origins


def authenticate_session(*, public_session_id, token, request, allow_closed=False):
    if not token:
        raise WebChatError("session_unauthorized", status_code=401)
    session = WebChatSession.objects.select_related(
        "installation__organization", "installation__channel_connection", "conversation__contact"
    ).filter(public_session_id=public_session_id).first()
    if not session or not hmac.compare_digest(session.token_hash, token_hash(token)):
        raise WebChatError("session_unauthorized", status_code=401)
    now = timezone.now()
    if (
        session.installation.status != InstallationStatus.ACTIVE
        or session.organization.status not in {OrganizationStatus.TRIAL, OrganizationStatus.ACTIVE}
        or settings.WEB_CHAT_GLOBAL_KILL_SWITCH
    ):
        raise WebChatError("session_unavailable", status_code=403)
    if session.expires_at <= now:
        WebChatSession.objects.filter(pk=session.pk, status=WebChatSessionStatus.ACTIVE).update(
            status=WebChatSessionStatus.EXPIRED
        )
        raise WebChatError("session_expired", status_code=401)
    if session.status in {WebChatSessionStatus.BLOCKED, WebChatSessionStatus.EXPIRED}:
        raise WebChatError("session_unavailable", status_code=403)
    if not allow_closed and session.status == WebChatSessionStatus.CLOSED:
        raise WebChatError("session_closed", status_code=409)
    origin = request_origin(request)
    if origin not in _allowed_request_origins(session):
        raise WebChatError("session_unauthorized", status_code=401)
    # SSE and polling may authenticate concurrently. Keep ordinary reads
    # read-only and throttle this operational timestamp to avoid write storms.
    if session.last_seen_at < now - timedelta(minutes=1):
        WebChatSession.objects.filter(pk=session.pk).update(last_seen_at=now)
    request.web_chat_cors_origin = origin
    return session


@transaction.atomic
def rotate_session_token(session):
    session = WebChatSession.objects.select_for_update().get(pk=session.pk)
    if session.status not in {WebChatSessionStatus.ACTIVE, WebChatSessionStatus.HANDED_OFF}:
        raise WebChatError("session_unavailable", status_code=409)
    token, digest = issue_session_token()
    session.token_hash = digest
    session.token_version += 1
    session.expires_at = timezone.now() + timedelta(hours=settings.WEB_CHAT_SESSION_TTL_HOURS)
    session.save(update_fields=["token_hash", "token_version", "expires_at", "last_seen_at"])
    publish_event(session=session, event_type="session_resumed", safe_payload={})
    return token


@transaction.atomic
def publish_event(*, session, event_type, message=None, safe_payload=None, client_event_key=""):
    session = WebChatSession.objects.select_for_update().get(pk=session.pk)
    if client_event_key:
        existing = session.events.filter(client_event_key=client_event_key).first()
        if existing:
            return existing, False
    sequence = (session.events.aggregate(value=Max("sequence"))["value"] or 0) + 1
    event = WebChatEvent(
        organization=session.organization,
        session=session,
        sequence=sequence,
        event_type=event_type[:40],
        message=message,
        safe_payload=safe_payload or {},
        client_event_key=client_event_key[:160],
    )
    event.full_clean()
    try:
        event.save()
    except IntegrityError:
        if client_event_key:
            return session.events.get(client_event_key=client_event_key), False
        raise
    return event, True


def serialize_message(message: Message) -> dict:
    sender = "visitor"
    if message.sender_type == MessageSenderType.AI:
        sender = "ai"
    elif message.sender_type == MessageSenderType.AGENT:
        sender = "employee"
    elif message.sender_type == MessageSenderType.SYSTEM:
        sender = "system"
    return {
        "id": str(message.id),
        "direction": message.direction,
        "sender": sender,
        "body": message.body,
        "status": message.status,
        "ai_generated": bool(message.metadata.get("ai_generated")),
        "occurred_at": message.occurred_at.isoformat(),
    }


def serialize_event(event: WebChatEvent) -> dict:
    payload = {"id": event.sequence, "type": event.event_type, "created_at": event.created_at.isoformat()}
    payload.update(event.safe_payload)
    if event.message_id and event.message.content_type == MessageContentType.TEXT:
        payload["message"] = serialize_message(event.message)
    return payload


def publish_message_event(message: Message):
    session = WebChatSession.objects.filter(conversation=message.conversation).first()
    if not session:
        return None
    event, _ = publish_event(
        session=session,
        event_type="message",
        message=message,
        client_event_key=f"message:{message.id}",
    )
    if message.direction == MessageDirection.OUTBOUND:
        updates = {"first_response_at": timezone.now()} if not session.first_response_at else {}
        if updates:
            WebChatSession.objects.filter(pk=session.pk, first_response_at__isnull=True).update(**updates)
        metric(
            session.installation,
            "ai_reply" if message.sender_type == MessageSenderType.AI else "human_reply",
            session=session,
        )
    return event


def _moderate(session: WebChatSession, body: str):
    if len(body) > MAX_MESSAGE_LENGTH:
        raise WebChatError("message_too_long", status_code=400)
    if re.search(r"<\s*/?\s*[a-zA-Z][^>]*>", body):
        raise WebChatError("plain_text_required", status_code=400)
    if len(re.findall(r"https?://", body.casefold())) > settings.WEB_CHAT_MAX_URLS_PER_MESSAGE:
        raise WebChatError("message_blocked", status_code=429)
    blocked = [item.casefold() for item in settings.WEB_CHAT_BLOCKED_TERMS if item]
    if any(item in body.casefold() for item in blocked):
        raise WebChatError("message_blocked", status_code=429)
    if session.conversation_id:
        repeats = session.conversation.messages.filter(
            direction=MessageDirection.INBOUND, body=body
        ).count()
        if repeats >= 2:
            session.abuse_score += 1
            if session.abuse_score >= 3:
                session.status = WebChatSessionStatus.BLOCKED
            session.save(update_fields=["abuse_score", "status"])
            raise WebChatError("message_blocked", status_code=429)


@transaction.atomic
def ingest_public_message(*, session, body, client_message_id):
    session = WebChatSession.objects.select_for_update(of=("self",)).select_related(
        "installation__channel_connection", "installation__organization", "contact", "conversation"
    ).get(pk=session.pk)
    if session.status not in {WebChatSessionStatus.ACTIVE, WebChatSessionStatus.HANDED_OFF}:
        raise WebChatError("session_unavailable", status_code=409)
    body = (body or "").strip()
    if not body:
        raise WebChatError("message_required", status_code=400)
    if not client_message_id or len(client_message_id) > 120:
        raise WebChatError("idempotency_key_required", status_code=400)
    _moderate(session, body)
    try:
        _rate_limit(
            f"message:session:{session.id}", settings.WEB_CHAT_MESSAGES_PER_SESSION_MINUTE, 60
        )
        _rate_limit(
            f"message:installation:{session.installation_id}",
            settings.WEB_CHAT_MESSAGES_PER_INSTALLATION_MINUTE,
            60,
        )
    except WebChatError:
        metric(session.installation, "rate_limited", session=session, category="message")
        raise
    provider_message_id = f"webchat:{session.id}:{client_message_id}"
    existing = Message.objects.for_organization(session.organization).filter(
        channel_connection=session.installation.channel_connection,
        provider_message_id=provider_message_id,
    ).first()
    if existing:
        return existing, False
    message, created = ingest_inbound_message(
        organization=session.organization,
        channel_connection=session.installation.channel_connection,
        identity_type=ContactIdentityType.WEB_CHAT,
        sender_value=str(session.public_session_id),
        sender_display_name="Website visitor",
        external_thread_id=str(session.public_session_id),
        provider_message_id=provider_message_id,
        body=body,
        metadata={"web_chat_session": str(session.public_session_id)},
        is_test=False,
    )
    if not session.conversation_id:
        session.conversation = message.conversation
        session.contact = message.conversation.contact
        session.first_message_at = timezone.now()
        session.save(update_fields=["conversation", "contact", "first_message_at", "last_seen_at"])
        metric(session.installation, "first_message", session=session)
    publish_message_event(message)
    metric(session.installation, "inbound_message", session=session)
    return message, created


@transaction.atomic
def update_identity(*, session, name="", email="", phone=""):
    session = WebChatSession.objects.select_for_update(of=("self",)).select_related("contact", "installation__channel_connection").get(pk=session.pk)
    if not session.contact_id:
        contact = create_contact(
            organization=session.organization,
            membership=None,
            display_name=(name or "Website visitor").strip(),
            preferred_language=session.language,
        )
        add_identity(
            organization=session.organization,
            contact=contact,
            identity_type=ContactIdentityType.WEB_CHAT,
            raw_value=str(session.public_session_id),
            channel_connection=session.installation.channel_connection,
            external_user_id=str(session.public_session_id),
            metadata={"source": "public_web_chat"},
        )
        session.contact = contact
        session.save(update_fields=["contact", "last_seen_at"])
    contact = session.contact
    if name and session.installation.collect_name:
        clean_name = name.strip()[:200]
        if "<" in clean_name or ">" in clean_name:
            raise WebChatError("identity_invalid")
        contact.display_name = clean_name
        contact.save(update_fields=["display_name", "updated_at"])
    for value, identity_type, enabled in (
        (email, ContactIdentityType.EMAIL, session.installation.collect_email),
        (phone, ContactIdentityType.PHONE, session.installation.collect_phone),
    ):
        if value and enabled:
            try:
                add_identity(
                    organization=session.organization,
                    contact=contact,
                    identity_type=identity_type,
                    raw_value=value.strip(),
                    metadata={"source": "public_web_chat", "consented": bool(session.consented_at)},
                )
            except CrmConflict:
                raise WebChatError("identity_conflict", status_code=409)
    publish_event(session=session, event_type="identity_updated", safe_payload={})
    record_activity(
        organization=session.organization,
        event_type="web_chat.identity_updated",
        summary="Visitor identity updated with consent",
        contact=contact,
        conversation=session.conversation,
    )
    return contact


@transaction.atomic
def request_handoff(session):
    session = WebChatSession.objects.select_for_update(of=("self",)).select_related("conversation", "installation").get(pk=session.pk)
    if not session.conversation_id:
        raise WebChatError("message_required_before_handoff", status_code=409)
    from ai_runtime.services import create_handoff

    handoff = create_handoff(
        conversation=session.conversation,
        run=None,
        reason_code="customer_request",
        safe_summary="Web Chat visitor requested human assistance.",
        requested_by="customer",
    )
    session.status = WebChatSessionStatus.HANDED_OFF
    session.save(update_fields=["status", "last_seen_at"])
    publish_event(
        session=session,
        event_type="handoff",
        safe_payload={"status": "requested", "message": session.installation.human_handoff_message},
    )
    metric(session.installation, "handoff", session=session)
    return handoff


@transaction.atomic
def mark_read(session, message_id=""):
    session = WebChatSession.objects.select_for_update().get(pk=session.pk)
    rows = Message.objects.for_organization(session.organization).filter(
        conversation=session.conversation,
        direction=MessageDirection.OUTBOUND,
        status__in=[MessageStatus.SENT, MessageStatus.DELIVERED],
    )
    if message_id:
        rows = rows.filter(pk=message_id)
    rows.update(status=MessageStatus.DELIVERED)
    return publish_event(
        session=session,
        event_type="read",
        safe_payload={"message_id": str(message_id or "")},
        client_event_key=f"read:{message_id or 'all'}",
    )[0]


@transaction.atomic
def close_session(session):
    session = WebChatSession.objects.select_for_update().get(pk=session.pk)
    if session.status == WebChatSessionStatus.CLOSED:
        return session
    publish_event(session=session, event_type="closed", safe_payload={})
    session.status = WebChatSessionStatus.CLOSED
    session.closed_at = timezone.now()
    session.token_hash = token_hash(secrets.token_urlsafe(TOKEN_BYTES))
    session.token_version += 1
    session.save(update_fields=["status", "closed_at", "token_hash", "token_version", "last_seen_at"])
    metric(session.installation, "session_closed", session=session)
    return session


def is_public_web_chat_connection(connection: ChannelConnection) -> bool:
    return connection.type == ChannelType.WEBCHAT and connection.provider == "public_web_chat"


def ai_state_for_installation(organization, connection):
    if not is_public_web_chat_connection(connection):
        return None
    try:
        installation = connection.web_chat_installation
    except WebChatInstallation.DoesNotExist:
        return ConversationAIState.OFF
    if installation.status != InstallationStatus.ACTIVE:
        return ConversationAIState.OFF
    if installation.ai_mode == InstallationAIMode.SUGGEST:
        return ConversationAIState.SUGGEST
    if installation.ai_mode == InstallationAIMode.AUTOPILOT and web_chat_autopilot_allowed(installation):
        return ConversationAIState.AUTOPILOT_WEB_CHAT
    return ConversationAIState.OFF


def web_chat_autopilot_allowed(installation):
    if settings.AI_RUNTIME_GLOBAL_KILL_SWITCH or settings.WEB_CHAT_GLOBAL_KILL_SWITCH:
        return False
    if installation.status != InstallationStatus.ACTIVE or installation.ai_mode != InstallationAIMode.AUTOPILOT:
        return False
    if not AssistantContextRevision.objects.filter(organization=installation.organization).exists():
        return False
    try:
        config = installation.organization.ai_runtime_config
    except Exception:
        return False
    if not config.enabled:
        return False
    if config.allowed_channel_connections.exists() and not config.allowed_channel_connections.filter(
        pk=installation.channel_connection_id
    ).exists():
        return False
    if config.provider == "fake":
        return bool(
            settings.WEB_CHAT_ALLOW_FAKE_AUTOPILOT
            and (settings.DEBUG or settings.TESTING or settings.E2E_TESTING)
        )
    return bool(
        installation.production_approved
        and installation.live_ai_opt_in
        and settings.AI_RUNTIME_ENABLE_REAL_OPENAI
        and settings.OPENAI_API_KEY
    )


def can_send_public_web_chat(conversation):
    session = WebChatSession.objects.filter(conversation=conversation).select_related("installation").first()
    return bool(
        session
        and session.status in {WebChatSessionStatus.ACTIVE, WebChatSessionStatus.HANDED_OFF}
        and session.installation.status == InstallationStatus.ACTIVE
        and session.expires_at > timezone.now()
        and operation_allowed(
            organization=conversation.organization,
            provider_type="web_chat",
            channel_connection=conversation.channel_connection,
        )
    )


@transaction.atomic
def anonymize_session(session, actor):
    session = WebChatSession.objects.select_for_update(of=("self",)).select_related("contact", "conversation").get(pk=session.pk)
    if actor.organization_id != session.organization_id:
        raise WebChatSession.DoesNotExist
    if session.contact_id:
        session.contact.identities.exclude(type=ContactIdentityType.WEB_CHAT).delete()
        session.contact.display_name = "Anonymized visitor"
        session.contact.first_name = ""
        session.contact.last_name = ""
        session.contact.company_name = ""
        session.contact.notes_summary = ""
        session.contact.save(update_fields=["display_name", "first_name", "last_name", "company_name", "notes_summary", "updated_at"])
    session.ip_hash = ""
    session.save(update_fields=["ip_hash", "last_seen_at"])
    record_activity(
        organization=session.organization,
        actor_membership=actor,
        event_type="web_chat.session_anonymized",
        summary="Web Chat visitor identity anonymized",
        contact=session.contact,
        conversation=session.conversation,
    )
    return session


def cleanup_expired_sessions(*, organization=None, limit=500):
    now = timezone.now()
    rows = WebChatSession.objects.select_related("installation").filter(expires_at__lt=now)
    if organization is not None:
        rows = rows.filter(organization=organization)
    processed = 0
    for session in rows.order_by("expires_at")[:limit]:
        retention_cutoff = now - timedelta(days=session.installation.retention_days)
        if session.status in {WebChatSessionStatus.ACTIVE, WebChatSessionStatus.HANDED_OFF}:
            session.status = WebChatSessionStatus.EXPIRED
            session.token_hash = token_hash(secrets.token_urlsafe(TOKEN_BYTES))
            session.save(update_fields=["status", "token_hash", "last_seen_at"])
        if session.expires_at < retention_cutoff:
            session.events.all().delete()
            session.metrics.all().delete()
            session.ip_hash = ""
            session.save(update_fields=["ip_hash", "last_seen_at"])
        processed += 1
    return processed
