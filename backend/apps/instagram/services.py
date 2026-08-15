from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from channels.models import ChannelConnection, ChannelStatus, ChannelType
from crm.models import (
    ContactIdentityType,
    Conversation,
    ConversationAIState,
    Message,
    MessageContentType,
    MessageDirection,
    MessageSenderType,
    MessageStatus,
)
from crm.services import ingest_inbound_message, record_activity
from instagram.models import (
    InstagramAutomationMode,
    InstagramConnection,
    InstagramConnectionStatus,
    InstagramConversationWindow,
    InstagramOAuthState,
    InstagramOutboundAttempt,
    InstagramOutboundStatus,
    InstagramWebhookEvent,
    InstagramWebhookStatus,
)
from instagram.providers import (
    REQUIRED_PERMISSIONS,
    InstagramProviderError,
    instagram_provider,
)
from organizations.models import Organization, OrganizationMembershipRole, OrganizationStatus
from control_plane.policies import operation_allowed


SAFE_REDIRECT_PATTERN = re.compile(
    r"^/(ru|uz|en)/app/settings/channels/instagram(?:/[0-9a-fA-F-]{36})?/?$"
)
SAFE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,500}$")


class InstagramError(Exception):
    def __init__(self, code: str, *, status_code: int = 400):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def integration_readiness() -> dict:
    live_required = {
        "app_id": bool(settings.META_APP_ID),
        "app_secret": bool(settings.META_APP_SECRET),
        "verify_token": bool(settings.META_INSTAGRAM_VERIFY_TOKEN),
        "graph_api_version": bool(settings.META_INSTAGRAM_GRAPH_API_VERSION),
        "redirect_uri": bool(settings.META_INSTAGRAM_REDIRECT_URI),
    }
    fake_allowed = bool(
        settings.META_INSTAGRAM_FAKE_PROVIDER
        and (settings.DEBUG or settings.TESTING or settings.E2E_TESTING)
    )
    live_ready = settings.META_INSTAGRAM_ENABLE_LIVE and all(live_required.values())
    return {
        "mode": "live" if settings.META_INSTAGRAM_ENABLE_LIVE else "development",
        "enabled": bool(live_ready or fake_allowed),
        "live_ready": bool(live_ready),
        "fake_provider": fake_allowed,
        "missing_live_configuration": [name for name, present in live_required.items() if not present],
    }


def create_oauth_state(*, request, intended_redirect: str) -> dict:
    membership = request.organization_membership
    if membership.role not in {OrganizationMembershipRole.OWNER, OrganizationMembershipRole.ADMIN}:
        raise InstagramError("role_not_allowed", status_code=403)
    if not SAFE_REDIRECT_PATTERN.fullmatch(intended_redirect or ""):
        raise InstagramError("invalid_redirect")
    readiness = integration_readiness()
    if not readiness["enabled"]:
        raise InstagramError("instagram_integration_not_ready", status_code=409)
    raw_state = secrets.token_urlsafe(32)
    raw_nonce = secrets.token_urlsafe(24)
    InstagramOAuthState.objects.create(
        organization=request.organization,
        state_hash=_hash(raw_state),
        user_id=request.user.id,
        membership=membership,
        intended_redirect=intended_redirect,
        nonce_hash=_hash(raw_nonce),
        expires_at=timezone.now() + timedelta(minutes=settings.META_INSTAGRAM_OAUTH_STATE_MINUTES),
    )
    if readiness["fake_provider"] and not settings.META_INSTAGRAM_ENABLE_LIVE:
        authorization_url = f"{settings.CLIENT_APP_URL.rstrip('/')}{intended_redirect}?instagram=fake&state={raw_state}"
    else:
        authorization_url = "https://www.instagram.com/oauth/authorize?" + urlencode(
            {
                "enable_fb_login": "0",
                "force_authentication": "1",
                "client_id": settings.META_APP_ID,
                "redirect_uri": settings.META_INSTAGRAM_REDIRECT_URI,
                "response_type": "code",
                "scope": ",".join(REQUIRED_PERMISSIONS),
                "state": raw_state,
            }
        )
    return {
        "authorization_url": authorization_url,
        "state": raw_state if readiness["fake_provider"] else None,
        "expires_in": settings.META_INSTAGRAM_OAUTH_STATE_MINUTES * 60,
        "mode": readiness["mode"],
    }


@transaction.atomic
def complete_oauth(*, user, raw_state: str, code: str) -> InstagramConnection:
    if not raw_state or not code or not SAFE_CODE_PATTERN.fullmatch(code):
        raise InstagramError("invalid_oauth_callback")
    state = (
        InstagramOAuthState.objects.select_for_update()
        .select_related("organization", "membership")
        .filter(state_hash=_hash(raw_state))
        .first()
    )
    if not state:
        raise InstagramError("oauth_state_invalid", status_code=404)
    if state.consumed_at:
        raise InstagramError("oauth_state_replayed", status_code=409)
    if state.expires_at <= timezone.now():
        raise InstagramError("oauth_state_expired", status_code=410)
    if state.user_id != user.id or state.membership.user_id != user.id:
        raise InstagramError("oauth_state_user_mismatch", status_code=403)
    from billing.services import BillingError, EntitlementService

    try:
        entitlements = EntitlementService(state.organization)
        entitlements.require("instagram")
    except BillingError as exc:
        raise InstagramError(exc.code, status_code=exc.status_code) from exc
    state.consumed_at = timezone.now()
    state.save(update_fields=["consumed_at"])
    try:
        snapshot = instagram_provider().exchange_code(code)
    except InstagramProviderError as exc:
        raise InstagramError(exc.code, status_code=409) from exc
    duplicate = InstagramConnection.objects.filter(
        instagram_user_id=snapshot.instagram_user_id,
        connection_status__in=[
            InstagramConnectionStatus.DRAFT,
            InstagramConnectionStatus.CONNECTED,
            InstagramConnectionStatus.DEGRADED,
            InstagramConnectionStatus.EXPIRED,
        ],
    ).first()
    if duplicate:
        raise InstagramError("instagram_account_already_connected", status_code=409)
    try:
        entitlements.require_capacity(
            "max_instagram_connections",
            InstagramConnection.objects.for_organization(state.organization)
            .exclude(connection_status=InstagramConnectionStatus.DISCONNECTED)
            .count(),
        )
        entitlements.require_capacity(
            "max_channel_connections",
            ChannelConnection.objects.for_organization(state.organization)
            .exclude(status=ChannelStatus.DISCONNECTED)
            .count(),
        )
    except BillingError as exc:
        raise InstagramError(exc.code, status_code=exc.status_code) from exc
    token_expires_at = (
        timezone.now() + timedelta(seconds=snapshot.expires_in)
        if snapshot.expires_in
        else None
    )
    missing_permissions = sorted(set(REQUIRED_PERMISSIONS) - set(snapshot.permissions))
    connection_status = (
        InstagramConnectionStatus.DEGRADED
        if missing_permissions
        else InstagramConnectionStatus.CONNECTED
    )
    channel_status = ChannelStatus.ERROR if missing_permissions else ChannelStatus.ACTIVE
    channel = ChannelConnection(
        organization=state.organization,
        type=ChannelType.INSTAGRAM,
        provider="meta_instagram",
        display_name=f"Instagram @{snapshot.username}",
        external_identifier=snapshot.instagram_user_id,
        status=channel_status,
        configuration={
            "provider_family": "meta",
            "login_type": "instagram_login",
            "group_messaging": False,
        },
        last_error_code="permission_missing" if missing_permissions else "",
        last_error_message="Required permission is missing." if missing_permissions else "",
    )
    channel.set_credentials(
        {
            "access_token": snapshot.access_token,
            "token_type": "instagram_user",
        }
    )
    channel.full_clean()
    channel.save()
    connection = InstagramConnection(
        organization=state.organization,
        channel_connection=channel,
        instagram_user_id=snapshot.instagram_user_id,
        username=snapshot.username,
        account_type=snapshot.account_type,
        profile_name=snapshot.profile_name,
        profile_picture_url=snapshot.profile_picture_url,
        profile_picture_expires_at=token_expires_at,
        graph_api_version=settings.META_INSTAGRAM_GRAPH_API_VERSION,
        permission_snapshot=list(snapshot.permissions),
        webhook_subscription_status=(
            "verified" if integration_readiness()["fake_provider"] else "pending"
        ),
        connection_status=connection_status,
        token_expires_at=token_expires_at,
        last_error_code="permission_missing" if missing_permissions else "",
        connected_by=state.membership,
        connected_at=timezone.now(),
    )
    connection.full_clean()
    try:
        connection.save()
    except IntegrityError as exc:
        raise InstagramError("instagram_account_already_connected", status_code=409) from exc
    record_activity(
        organization=state.organization,
        actor_membership=state.membership,
        event_type="instagram.connected",
        summary="Instagram professional account connected",
        metadata={"instagram_connection_id": str(connection.id)},
    )
    connection.oauth_redirect = state.intended_redirect
    return connection


class InstagramWebhookVerifier:
    @staticmethod
    def verify(raw_body: bytes, signature: str) -> bool:
        secret = settings.META_APP_SECRET
        if not secret or not signature or not signature.startswith("sha256="):
            return False
        expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        supplied = signature.split("=", 1)[1]
        return hmac.compare_digest(expected, supplied)


class InstagramWebhookParser:
    @staticmethod
    def parse(raw_body: bytes) -> list[dict]:
        if len(raw_body) > settings.META_INSTAGRAM_MAX_WEBHOOK_BYTES:
            raise InstagramError("webhook_payload_too_large", status_code=413)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InstagramError("webhook_json_invalid") from exc
        if not isinstance(payload, dict) or payload.get("object") != "instagram":
            raise InstagramError("webhook_object_invalid")
        normalized: list[dict] = []
        for entry in payload.get("entry") or []:
            if not isinstance(entry, dict):
                continue
            professional_id = str(entry.get("id") or "")[:255]
            for item in entry.get("messaging") or []:
                if not isinstance(item, dict):
                    continue
                sender_id = str((item.get("sender") or {}).get("id") or "")[:255]
                recipient_id = str((item.get("recipient") or {}).get("id") or professional_id)[:255]
                message = item.get("message") if isinstance(item.get("message"), dict) else {}
                reaction = item.get("reaction") if isinstance(item.get("reaction"), dict) else {}
                read = item.get("read") if isinstance(item.get("read"), dict) else {}
                postback = item.get("postback") if isinstance(item.get("postback"), dict) else {}
                attachments = message.get("attachments") if isinstance(message.get("attachments"), list) else []
                safe_attachment_types = [
                    str(value.get("type") or "unknown")[:40]
                    for value in attachments[: settings.META_INSTAGRAM_MAX_ATTACHMENTS]
                    if isinstance(value, dict)
                ]
                event_type = "unknown"
                if message:
                    event_type = "echo" if message.get("is_echo") else "message"
                    if message.get("is_deleted") or message.get("is_edited"):
                        event_type = "message_update"
                elif reaction:
                    event_type = "reaction"
                elif read:
                    event_type = "read"
                elif postback:
                    event_type = "postback"
                timestamp = item.get("timestamp")
                normalized.append(
                    {
                        "professional_account_id": professional_id,
                        "sender_id": sender_id,
                        "recipient_id": recipient_id,
                        "timestamp": int(timestamp) if str(timestamp).isdigit() else 0,
                        "event_type": event_type,
                        "message_id": str(message.get("mid") or reaction.get("mid") or "")[:255],
                        "text": str(message.get("text") or postback.get("title") or "")[:10000],
                        "is_echo": bool(message.get("is_echo")),
                        "is_deleted": bool(message.get("is_deleted")),
                        "is_edited": bool(message.get("is_edited")),
                        "reply_to_message_id": str((message.get("reply_to") or {}).get("mid") or "")[:255],
                        "attachment_types": safe_attachment_types,
                        "has_story_reference": bool(message.get("reply_to") or item.get("referral")),
                        "has_shared_reference": bool(message.get("shares")),
                        "reaction": str(reaction.get("reaction") or reaction.get("action") or "")[:40],
                        "read_watermark": int(read.get("watermark") or 0),
                        "postback_payload": str(postback.get("payload") or "")[:200],
                    }
                )
        return normalized


@transaction.atomic
def receive_webhook(*, raw_body: bytes, signature: str) -> dict:
    if not InstagramWebhookVerifier.verify(raw_body, signature):
        raise InstagramError("webhook_signature_invalid", status_code=403)
    normalized = InstagramWebhookParser.parse(raw_body)
    created_count = duplicate_count = 0
    for item in normalized:
        destination = item["professional_account_id"]
        connection = (
            InstagramConnection.objects.select_related("organization", "channel_connection")
            .filter(
                instagram_user_id=destination,
                connection_status__in=[
                    InstagramConnectionStatus.CONNECTED,
                    InstagramConnectionStatus.DEGRADED,
                ],
                channel_connection__status=ChannelStatus.ACTIVE,
            )
            .first()
        )
        if not connection:
            raise InstagramError("unknown_or_inactive_recipient", status_code=404)
        canonical = json.dumps(item, separators=(",", ":"), sort_keys=True)
        event_hash = hashlib.sha256(
            f"{destination}:{canonical}".encode("utf-8")
        ).hexdigest()
        event, created = InstagramWebhookEvent.objects.get_or_create(
            event_hash=event_hash,
            defaults={
                "organization": connection.organization,
                "connection": connection,
                "professional_account_id": destination,
                "event_type": item["event_type"],
                "normalized_payload": item,
            },
        )
        if created:
            created_count += 1
            from instagram.tasks import process_instagram_webhook

            transaction.on_commit(lambda event_id=event.id: process_instagram_webhook.delay(str(event_id)))
        else:
            duplicate_count += 1
    return {"accepted": created_count, "duplicates": duplicate_count}


def _occurred_at(milliseconds: int):
    if milliseconds <= 0:
        return timezone.now()
    try:
        return datetime.fromtimestamp(milliseconds / 1000, tz=dt_timezone.utc)
    except (OverflowError, OSError, ValueError):
        return timezone.now()


def _message_body(payload: dict) -> tuple[str, dict]:
    body = payload.get("text", "").strip()
    metadata = {
        "provider": "instagram",
        "story_reply": bool(payload.get("has_story_reference")),
        "shared_post": bool(payload.get("has_shared_reference")),
        "attachment_types": payload.get("attachment_types") or [],
        "attachment_state": (
            "provider_reference_expiring" if payload.get("attachment_types") else "none"
        ),
        "reply_to_provider_message_id": payload.get("reply_to_message_id") or "",
    }
    if not body:
        if payload.get("has_story_reference"):
            body = "Instagram story reply"
        elif payload.get("has_shared_reference"):
            body = "Instagram shared post"
        elif payload.get("attachment_types"):
            body = "Instagram attachment"
        elif payload.get("postback_payload"):
            body = "Instagram quick reply"
        else:
            body = "Instagram event"
    return body, metadata


@transaction.atomic
def process_webhook_event(event_id) -> InstagramWebhookEvent:
    event = (
        InstagramWebhookEvent.objects.select_for_update()
        .select_related("connection__channel_connection", "organization")
        .get(pk=event_id)
    )
    if event.status in {InstagramWebhookStatus.PROCESSED, InstagramWebhookStatus.IGNORED}:
        return event
    event.status = InstagramWebhookStatus.PROCESSING
    event.attempt_count = F("attempt_count") + 1
    event.save(update_fields=["status", "attempt_count"])
    event.refresh_from_db(fields=["attempt_count"])
    payload = event.normalized_payload
    connection = event.connection
    try:
        if event.event_type in {"message", "postback"}:
            sender_id = payload.get("sender_id", "")
            message_id = payload.get("message_id") or _hash(
                json.dumps(payload, sort_keys=True)
            )
            if not sender_id or sender_id == connection.instagram_user_id:
                event.status = InstagramWebhookStatus.IGNORED
            else:
                body, metadata = _message_body(payload)
                message, created = ingest_inbound_message(
                    organization=event.organization,
                    channel_connection=connection.channel_connection,
                    identity_type=ContactIdentityType.INSTAGRAM,
                    sender_value=sender_id,
                    sender_display_name=f"Instagram user {sender_id[-6:]}",
                    external_thread_id=sender_id,
                    provider_message_id=message_id,
                    body=body,
                    occurred_at=_occurred_at(payload.get("timestamp", 0)),
                    metadata=metadata,
                    is_test=integration_readiness()["fake_provider"],
                )
                if created:
                    reply_to_id = metadata.get("reply_to_provider_message_id")
                    reply_to = (
                        Message.objects.for_organization(event.organization)
                        .filter(
                            channel_connection=connection.channel_connection,
                            provider_message_id=reply_to_id,
                        )
                        .first()
                        if reply_to_id
                        else None
                    )
                    update_fields = []
                    if reply_to:
                        message.reply_to = reply_to
                        update_fields.append("reply_to")
                    if metadata.get("attachment_types") and not payload.get("text", "").strip():
                        message.content_type = MessageContentType.MEDIA
                        update_fields.append("content_type")
                    if update_fields:
                        message.save(update_fields=[*update_fields, "updated_at"])
                    reopen_window(message.conversation, message.occurred_at)
                event.status = InstagramWebhookStatus.PROCESSED
        elif event.event_type == "echo":
            message = Message.objects.for_organization(event.organization).filter(
                channel_connection=connection.channel_connection,
                provider_message_id=payload.get("message_id"),
                direction=MessageDirection.OUTBOUND,
            ).first()
            if message:
                message.status = MessageStatus.SENT
                message.metadata = {**message.metadata, "provider_echo": True}
                message.save(update_fields=["status", "metadata", "updated_at"])
                event.status = InstagramWebhookStatus.PROCESSED
            else:
                conversation = Conversation.objects.for_organization(event.organization).filter(
                    channel_connection=connection.channel_connection,
                    external_thread_id=payload.get("recipient_id"),
                ).first()
                if conversation and payload.get("message_id"):
                    body, metadata = _message_body(payload)
                    occurred_at = _occurred_at(payload.get("timestamp", 0))
                    message = Message(
                        organization=event.organization,
                        conversation=conversation,
                        channel_connection=connection.channel_connection,
                        direction=MessageDirection.OUTBOUND,
                        sender_type=MessageSenderType.SYSTEM,
                        provider_message_id=payload["message_id"],
                        content_type=MessageContentType.TEXT,
                        body=body,
                        status=MessageStatus.SENT,
                        metadata={
                            **metadata,
                            "provider_echo": True,
                            "sent_outside_althair": True,
                        },
                        occurred_at=occurred_at,
                    )
                    message.full_clean()
                    message.save()
                    Conversation.objects.filter(pk=conversation.pk).update(
                        last_message_at=occurred_at,
                        last_outbound_at=occurred_at,
                    )
                    event.status = InstagramWebhookStatus.PROCESSED
                else:
                    event.status = InstagramWebhookStatus.IGNORED
        elif event.event_type == "reaction":
            message = Message.objects.for_organization(event.organization).filter(
                channel_connection=connection.channel_connection,
                provider_message_id=payload.get("message_id"),
            ).first()
            if message:
                message.metadata = {
                    **message.metadata,
                    "instagram_reaction": payload.get("reaction", "")[:40],
                }
                message.save(update_fields=["metadata", "updated_at"])
                event.status = InstagramWebhookStatus.PROCESSED
            else:
                event.status = InstagramWebhookStatus.IGNORED
        elif event.event_type == "read":
            watermark = _occurred_at(payload.get("read_watermark", 0))
            Message.objects.for_organization(event.organization).filter(
                channel_connection=connection.channel_connection,
                direction=MessageDirection.OUTBOUND,
                occurred_at__lte=watermark,
                status__in=[MessageStatus.SENT, MessageStatus.DELIVERED],
            ).update(status=MessageStatus.READ)
            event.status = InstagramWebhookStatus.PROCESSED
        elif event.event_type == "message_update":
            message = Message.objects.for_organization(event.organization).filter(
                channel_connection=connection.channel_connection,
                provider_message_id=payload.get("message_id"),
            ).first()
            if message and payload.get("is_edited") and payload.get("text"):
                message.body = payload["text"]
                message.metadata = {**message.metadata, "provider_edited": True}
                message.full_clean()
                message.save(update_fields=["body", "metadata", "updated_at"])
                event.status = InstagramWebhookStatus.PROCESSED
            elif message and payload.get("is_deleted"):
                message.metadata = {**message.metadata, "provider_deleted": True}
                message.save(update_fields=["metadata", "updated_at"])
                event.status = InstagramWebhookStatus.PROCESSED
            else:
                event.status = InstagramWebhookStatus.IGNORED
        else:
            event.status = InstagramWebhookStatus.IGNORED
        event.safe_error_code = ""
        event.processed_at = timezone.now()
        event.save(update_fields=["status", "safe_error_code", "processed_at"])
        connection.last_webhook_at = timezone.now()
        connection.save(update_fields=["last_webhook_at", "updated_at"])
        return event
    except Exception as exc:
        event.status = (
            InstagramWebhookStatus.DEAD_LETTER
            if event.attempt_count >= settings.META_INSTAGRAM_MAX_EVENT_ATTEMPTS
            else InstagramWebhookStatus.FAILED
        )
        event.safe_error_code = "event_processing_failed"
        event.save(update_fields=["status", "safe_error_code"])
        return event


def reopen_window(conversation: Conversation, at=None) -> InstagramConversationWindow:
    at = at or timezone.now()
    window, _ = InstagramConversationWindow.objects.update_or_create(
        organization=conversation.organization,
        conversation=conversation,
        defaults={
            "last_customer_message_at": at,
            "standard_window_expires_at": at + timedelta(hours=settings.META_INSTAGRAM_STANDARD_WINDOW_HOURS),
            "human_agent_window_expires_at": at + timedelta(hours=settings.META_INSTAGRAM_HUMAN_AGENT_WINDOW_HOURS),
        },
    )
    return window


def connection_for_conversation(conversation: Conversation) -> InstagramConnection | None:
    # Always re-read provider health for a final-send decision. A long-lived ORM
    # instance must not keep a stale token/window decision in its relation cache.
    return (
        InstagramConnection.objects.select_related("channel_connection", "organization")
        .filter(
            organization_id=conversation.organization_id,
            channel_connection_id=conversation.channel_connection_id,
        )
        .first()
    )


def window_eligibility(conversation: Conversation, *, at=None) -> dict:
    at = at or timezone.now()
    connection = connection_for_conversation(conversation)
    if not connection:
        return {"state": "provider_unavailable", "can_send": False, "human_agent_available": False}
    organization_status = Organization.objects.filter(pk=conversation.organization_id).values_list(
        "status", flat=True
    ).first()
    if organization_status not in {OrganizationStatus.TRIAL, OrganizationStatus.ACTIVE}:
        return {"state": "organization_read_only", "can_send": False, "human_agent_available": False}
    if connection.connection_status == InstagramConnectionStatus.EXPIRED or (
        connection.token_expires_at and connection.token_expires_at <= at
    ):
        return {"state": "connection_expired", "can_send": False, "human_agent_available": False}
    if connection.connection_status != InstagramConnectionStatus.CONNECTED:
        state = "permission_missing" if connection.last_error_code == "permission_missing" else "provider_degraded"
        return {"state": state, "can_send": False, "human_agent_available": False}
    if connection.channel_connection.status != ChannelStatus.ACTIVE:
        return {"state": "provider_unavailable", "can_send": False, "human_agent_available": False}
    if connection.circuit_open_until and connection.circuit_open_until > at:
        return {"state": "provider_degraded", "can_send": False, "human_agent_available": False}
    window = InstagramConversationWindow.objects.filter(
        organization_id=conversation.organization_id,
        conversation_id=conversation.id,
    ).first()
    if not window:
        return {"state": "waiting_for_customer", "can_send": False, "human_agent_available": False}
    standard = window.standard_window_expires_at > at
    human = bool(
        not standard
        and settings.META_INSTAGRAM_ENABLE_HUMAN_AGENT
        and connection.human_agent_approved
        and window.human_agent_window_expires_at
        and window.human_agent_window_expires_at > at
    )
    return {
        "state": "can_reply" if standard else "human_agent_available" if human else "window_expired",
        "can_send": standard,
        "human_agent_available": human,
        "last_customer_message_at": window.last_customer_message_at,
        "standard_window_expires_at": window.standard_window_expires_at,
        "human_agent_window_expires_at": window.human_agent_window_expires_at,
    }


def can_send_instagram(conversation: Conversation) -> bool:
    return bool(
        window_eligibility(conversation).get("can_send")
        and operation_allowed(
            organization=conversation.organization,
            provider_type="instagram",
            channel_connection=conversation.channel_connection,
        )
    )


def serialize_conversation_policy(conversation: Conversation) -> dict:
    if conversation.channel_type != ChannelType.INSTAGRAM:
        return {}
    policy = window_eligibility(conversation)
    connection = connection_for_conversation(conversation)
    return {
        **policy,
        "professional_account": f"@{connection.username}" if connection else "",
        "connection_status": connection.connection_status if connection else "missing",
        "connection_health": connection.last_error_code if connection else "missing",
        "human_agent_approved": bool(connection and connection.human_agent_approved),
    }


def _throttle(connection: InstagramConnection):
    now = timezone.now()
    minute = now.strftime("%Y%m%d%H%M")
    org_key = f"instagram:send:org:{connection.organization_id}:{minute}"
    count = cache.get(org_key, 0)
    if count >= settings.META_INSTAGRAM_ORG_SENDS_PER_MINUTE:
        raise InstagramError("organization_send_throttled", status_code=429)
    cache.set(org_key, count + 1, timeout=90)


def send_instagram_message(
    *,
    conversation: Conversation,
    body: str,
    client_message_id: str,
    membership=None,
    sender_type=MessageSenderType.AGENT,
    human_agent: bool = False,
    metadata=None,
):
    existing = Message.objects.for_organization(conversation.organization).filter(
        conversation=conversation,
        client_message_id=client_message_id,
    ).first()
    if existing:
        return existing, False
    connection = connection_for_conversation(conversation)
    if not connection:
        raise InstagramError("provider_unavailable", status_code=409)
    if not operation_allowed(
        organization=conversation.organization,
        provider_type="instagram",
        channel_connection=conversation.channel_connection,
    ):
        raise InstagramError("operational_control_active", status_code=409)
    text = (body or "").strip()
    if not text or len(text) > settings.META_INSTAGRAM_MAX_TEXT_LENGTH:
        raise InstagramError("message_length_invalid")
    policy = window_eligibility(conversation)
    if human_agent:
        if sender_type != MessageSenderType.AGENT or membership is None:
            raise InstagramError("human_agent_manual_only", status_code=403)
        if policy["state"] == "can_reply":
            raise InstagramError("human_agent_not_required", status_code=409)
        if not policy.get("human_agent_available"):
            raise InstagramError("human_agent_unavailable", status_code=409)
    elif not policy.get("can_send"):
        raise InstagramError(policy["state"], status_code=409)
    if sender_type == MessageSenderType.AI and human_agent:
        raise InstagramError("ai_cannot_use_human_agent", status_code=403)
    from billing.services import BillingError, EntitlementService

    try:
        EntitlementService(conversation.organization).require("monthly_external_messages")
    except BillingError as exc:
        raise InstagramError(exc.code, status_code=exc.status_code) from exc
    _throttle(connection)
    lock_key = f"instagram:send-lock:{connection.id}"
    if not cache.add(lock_key, "1", timeout=settings.META_INSTAGRAM_SEND_LOCK_SECONDS):
        raise InstagramError("connection_send_busy", status_code=409)
    now = timezone.now()
    try:
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
                "provider": "instagram",
                "human_agent": human_agent,
                **(metadata or {}),
            },
            occurred_at=now,
        )
        message.full_clean()
        message.save()
        attempt = InstagramOutboundAttempt.objects.create(
            organization=conversation.organization,
            connection=connection,
            message=message,
            status=InstagramOutboundStatus.SENDING,
            attempt_count=1,
        )
        try:
            result = instagram_provider().send_text(
                connection=connection,
                recipient_id=conversation.external_thread_id,
                text=text,
                human_agent=human_agent,
                reply_to_message_id=str((metadata or {}).get("reply_to_provider_message_id") or ""),
            )
        except InstagramProviderError as exc:
            message.status = MessageStatus.QUEUED if exc.transient else MessageStatus.FAILED
            message.error_code = exc.code
            message.save(update_fields=["status", "error_code", "updated_at"])
            attempt.status = (
                InstagramOutboundStatus.QUEUED if exc.transient else InstagramOutboundStatus.FAILED
            )
            attempt.safe_error_code = exc.code
            attempt.next_retry_at = now + timedelta(seconds=30) if exc.transient else None
            attempt.save(update_fields=["status", "safe_error_code", "next_retry_at", "updated_at"])
            connection.failure_count += 1
            connection.last_error_code = exc.code
            if connection.failure_count >= 3:
                connection.circuit_open_until = now + timedelta(
                    seconds=settings.META_INSTAGRAM_CIRCUIT_BREAKER_SECONDS
                )
            connection.save(
                update_fields=[
                    "failure_count", "last_error_code", "circuit_open_until", "updated_at"
                ]
            )
            if exc.transient:
                from instagram.tasks import retry_instagram_outbound

                transaction.on_commit(
                    lambda attempt_id=attempt.id: retry_instagram_outbound.apply_async(
                        args=[str(attempt_id)], countdown=30
                    )
                )
            raise InstagramError(exc.code, status_code=503 if exc.transient else 409) from exc
        message.provider_message_id = result.message_id
        message.status = MessageStatus.SENT
        message.error_code = ""
        message.save(update_fields=["provider_message_id", "status", "error_code", "updated_at"])
        from billing.services import record_message_usage

        record_message_usage(message)
        attempt.status = InstagramOutboundStatus.SENT
        attempt.provider_request_id = result.request_id
        attempt.safe_error_code = ""
        attempt.next_retry_at = None
        attempt.save(
            update_fields=[
                "status",
                "provider_request_id",
                "safe_error_code",
                "next_retry_at",
                "updated_at",
            ]
        )
        connection.last_successful_send_at = now
        connection.failure_count = 0
        connection.circuit_open_until = None
        connection.last_error_code = ""
        connection.save(
            update_fields=[
                "last_successful_send_at",
                "failure_count",
                "circuit_open_until",
                "last_error_code",
                "updated_at",
            ]
        )
        Conversation.objects.filter(pk=conversation.pk).update(
            last_message_at=now,
            last_outbound_at=now,
        )
        if sender_type == MessageSenderType.AGENT:
            Conversation.objects.filter(pk=conversation.pk).update(
                ai_state=ConversationAIState.PAUSED_BY_HUMAN,
                ai_state_updated_at=now,
            )
            try:
                from ai_runtime.services import supersede_active_runs

                supersede_active_runs(conversation=conversation, reason="human_reply")
            except ImportError:
                pass
        record_activity(
            organization=conversation.organization,
            actor_membership=membership,
            event_type="instagram.message_sent",
            summary="Instagram reply sent",
            contact=conversation.contact,
            conversation=conversation,
            metadata={"human_agent": human_agent, "sender_type": sender_type},
        )
        return message, True
    finally:
        cache.delete(lock_key)


def send_ai_message(*, run, body: str, client_message_id: str, metadata: dict):
    return send_instagram_message(
        conversation=run.conversation,
        body=body,
        client_message_id=client_message_id,
        sender_type=MessageSenderType.AI,
        human_agent=False,
        metadata={"ai_generated": True, **metadata},
    )


def ai_state_for_connection(organization, channel_connection):
    try:
        connection = channel_connection.instagram_connection
    except (InstagramConnection.DoesNotExist, AttributeError):
        return None
    if connection.organization_id != organization.id:
        return ConversationAIState.OFF
    if connection.automation_mode == InstagramAutomationMode.SUGGEST:
        return ConversationAIState.SUGGEST
    if connection.automation_mode == InstagramAutomationMode.AUTOPILOT:
        return ConversationAIState.AUTOPILOT_INSTAGRAM
    return ConversationAIState.OFF


def instagram_autopilot_allowed(conversation: Conversation) -> bool:
    connection = connection_for_conversation(conversation)
    return bool(
        connection
        and connection.automation_mode == InstagramAutomationMode.AUTOPILOT
        and conversation.ai_state == ConversationAIState.AUTOPILOT_INSTAGRAM
        and window_eligibility(conversation).get("can_send")
    )


@transaction.atomic
def disconnect_instagram(connection: InstagramConnection, actor) -> InstagramConnection:
    connection = InstagramConnection.objects.select_for_update().get(pk=connection.pk)
    connection.connection_status = InstagramConnectionStatus.DISCONNECTED
    connection.disconnected_at = timezone.now()
    connection.automation_mode = InstagramAutomationMode.MANUAL
    connection.last_error_code = ""
    connection.save(
        update_fields=[
            "connection_status",
            "disconnected_at",
            "automation_mode",
            "last_error_code",
            "updated_at",
        ]
    )
    channel = connection.channel_connection
    channel.set_credentials({})
    channel.status = ChannelStatus.DISCONNECTED
    channel.last_error_code = ""
    channel.last_error_message = ""
    channel.save(
        update_fields=[
            "encrypted_credentials",
            "status",
            "last_error_code",
            "last_error_message",
            "updated_at",
        ]
    )
    record_activity(
        organization=connection.organization,
        actor_membership=actor,
        event_type="instagram.disconnected",
        summary="Instagram account disconnected",
        metadata={"instagram_connection_id": str(connection.id)},
    )
    return connection


@transaction.atomic
def reconnect_fake(connection: InstagramConnection, actor) -> InstagramConnection:
    readiness = integration_readiness()
    if not readiness["fake_provider"]:
        raise InstagramError("oauth_reconnect_required", status_code=409)
    channel = connection.channel_connection
    channel.set_credentials({"access_token": f"fake-reconnected-{_hash(str(connection.id))}"})
    channel.status = ChannelStatus.ACTIVE
    channel.save(update_fields=["encrypted_credentials", "status", "updated_at"])
    connection.connection_status = InstagramConnectionStatus.CONNECTED
    connection.token_expires_at = timezone.now() + timedelta(days=60)
    connection.disconnected_at = None
    connection.last_error_code = ""
    connection.save(
        update_fields=[
            "connection_status",
            "token_expires_at",
            "disconnected_at",
            "last_error_code",
            "updated_at",
        ]
    )
    record_activity(
        organization=connection.organization,
        actor_membership=actor,
        event_type="instagram.reconnected",
        summary="Instagram account reconnected",
        metadata={"instagram_connection_id": str(connection.id)},
    )
    return connection


def connection_health(connection: InstagramConnection, *, run_provider=False) -> dict:
    now = timezone.now()
    required = set(REQUIRED_PERMISSIONS)
    permissions = set(connection.permission_snapshot)
    token_present = bool(connection.channel_connection.encrypted_credentials)
    token_expired = bool(connection.token_expires_at and connection.token_expires_at <= now)
    provider_health = None
    if run_provider and token_present and not token_expired:
        try:
            provider_health = instagram_provider().health(connection)
            connection.last_health_check_at = now
            account_matches = bool(provider_health.get("account_matches"))
            current_permissions = list(provider_health.get("permissions") or [])
            missing = required - set(current_permissions)
            connection.permission_snapshot = current_permissions
            connection.last_error_code = (
                "account_ownership_changed"
                if not account_matches
                else "permission_missing"
                if missing
                else ""
            )
            connection.connection_status = (
                InstagramConnectionStatus.REVOKED
                if not account_matches
                else InstagramConnectionStatus.DEGRADED
                if missing
                else InstagramConnectionStatus.CONNECTED
            )
            connection.failure_count = 0
            connection.save(
                update_fields=[
                    "last_health_check_at",
                    "last_error_code",
                    "connection_status",
                    "permission_snapshot",
                    "failure_count",
                    "updated_at",
                ]
            )
        except InstagramProviderError as exc:
            connection.last_health_check_at = now
            connection.last_error_code = exc.code
            connection.connection_status = (
                InstagramConnectionStatus.EXPIRED
                if exc.code in {"access_token_missing", "access_token_expired"}
                else InstagramConnectionStatus.DEGRADED
            )
            connection.save(
                update_fields=[
                    "last_health_check_at",
                    "last_error_code",
                    "connection_status",
                    "updated_at",
                ]
            )
    return {
        "status": connection.connection_status,
        "account_connected": connection.connection_status
        not in {InstagramConnectionStatus.DISCONNECTED, InstagramConnectionStatus.REVOKED},
        "token_present": token_present,
        "token_expired": token_expired,
        "token_expires_at": connection.token_expires_at,
        "permissions_ok": required.issubset(permissions),
        "missing_permissions": sorted(required - permissions),
        "webhook_subscription": connection.webhook_subscription_status,
        "last_webhook_at": connection.last_webhook_at,
        "last_send_at": connection.last_successful_send_at,
        "last_health_check_at": connection.last_health_check_at,
        "error_code": connection.last_error_code,
        "graph_api_version": connection.graph_api_version or "not_configured",
        "app_mode": integration_readiness()["mode"],
        "provider": provider_health,
        "queue": {
            "queued": connection.outbound_attempts.filter(status=InstagramOutboundStatus.QUEUED).count(),
            "failed": connection.outbound_attempts.filter(status=InstagramOutboundStatus.FAILED).count(),
            "dead_letter": connection.outbound_attempts.filter(status=InstagramOutboundStatus.DEAD_LETTER).count(),
        },
    }


def app_review_checklist(connection: InstagramConnection) -> list[dict]:
    return [
        {"key": "professional_account", "ready": bool(connection.instagram_user_id)},
        {"key": "messaging_permission", "ready": "instagram_business_manage_messages" in connection.permission_snapshot},
        {"key": "privacy_policy", "ready": False},
        {"key": "terms", "ready": False},
        {"key": "data_deletion", "ready": True},
        {"key": "webhook", "ready": connection.webhook_subscription_status == "verified"},
        {"key": "human_agent_policy", "ready": True},
        {"key": "review_approval", "ready": False},
    ]
