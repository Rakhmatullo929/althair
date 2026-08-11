from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from channels.models import ChannelConnection, ChannelStatus, ChannelType
from crm.models import (
    Contact,
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
from crm.services import ingest_inbound_message, record_activity
from gmail_integration.models import (
    GmailAuditEvent,
    GmailAutomationMode,
    GmailConnection,
    GmailConnectionStatus,
    GmailInitialSyncMode,
    GmailInitialSyncStatus,
    GmailMessageRecord,
    GmailNotification,
    GmailNotificationStatus,
    GmailOAuthState,
    GmailOutboundAttempt,
    GmailOutboundStatus,
    GmailSyncRun,
    GmailSyncStatus,
    GmailSyncType,
)
from gmail_integration.parser import parse_gmail_message
from gmail_integration.providers import (
    GMAIL_MODIFY_SCOPE,
    GmailHistoryExpired,
    GmailProviderError,
    build_rfc_reply,
    gmail_provider,
)
from organizations.models import OrganizationMembershipRole, OrganizationStatus


SAFE_REDIRECT_PATTERN = re.compile(
    r"^/(ru|uz|en)/app/settings/channels/gmail(?:/[0-9a-fA-F-]{36})?/?$"
)
SAFE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_.:@+\-/]{1,1000}$")


class GmailError(Exception):
    def __init__(self, code: str, *, status_code: int = 400):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class GmailAttachmentScanner:
    """Replaceable server-side boundary for attachment content inspection."""

    def scan(self, *, content: bytes, mime_type: str) -> None:
        raise NotImplementedError


class SignatureAttachmentScanner(GmailAttachmentScanner):
    """Fail-closed MVP scanner for allowlisted types and obvious unsafe content."""

    def scan(self, *, content: bytes, mime_type: str) -> None:
        magic_valid = (
            (mime_type == "application/pdf" and content.startswith(b"%PDF-"))
            or (mime_type == "image/png" and content.startswith(b"\x89PNG\r\n\x1a\n"))
            or (mime_type == "image/jpeg" and content.startswith(b"\xff\xd8\xff"))
            or (
                mime_type in {"text/plain", "text/csv"}
                and b"\x00" not in content[:4096]
            )
        )
        if not magic_valid or b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in content:
            raise GmailError("attachment_content_invalid", status_code=415)


def gmail_attachment_scanner() -> GmailAttachmentScanner:
    return SignatureAttachmentScanner()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _audit(*, organization, event_type, connection=None, actor=None, metadata=None):
    return GmailAuditEvent.objects.create(
        organization=organization,
        connection=connection,
        actor_membership=actor,
        event_type=event_type,
        metadata=metadata or {},
    )


def integration_readiness() -> dict:
    live_required = {
        "client_id": bool(settings.GOOGLE_GMAIL_CLIENT_ID),
        "client_secret": bool(settings.GOOGLE_GMAIL_CLIENT_SECRET),
        "redirect_uri": bool(settings.GOOGLE_GMAIL_REDIRECT_URI),
        "pubsub_topic": bool(settings.GOOGLE_GMAIL_PUBSUB_TOPIC),
        "pubsub_audience": bool(settings.GOOGLE_GMAIL_PUBSUB_AUDIENCE),
        "push_service_account": bool(settings.GOOGLE_GMAIL_PUBSUB_SERVICE_ACCOUNT),
        "subscription": bool(settings.GOOGLE_GMAIL_PUBSUB_SUBSCRIPTION),
    }
    fake = bool(
        settings.GOOGLE_GMAIL_FAKE_PROVIDER
        and (settings.DEBUG or settings.TESTING or settings.E2E_TESTING)
    )
    live_ready = bool(settings.GOOGLE_GMAIL_ENABLE_LIVE and all(live_required.values()))
    return {
        "mode": "live" if settings.GOOGLE_GMAIL_ENABLE_LIVE else "development",
        "enabled": live_ready or fake,
        "live_ready": live_ready,
        "fake_provider": fake,
        "scope": GMAIL_MODIFY_SCOPE,
        "missing_live_configuration": [name for name, value in live_required.items() if not value],
    }


def create_oauth_state(
    *,
    request,
    intended_redirect: str,
    initial_sync_mode: str = GmailInitialSyncMode.RECENT,
    initial_sync_max_messages: int = 100,
    reconnect_connection: GmailConnection | None = None,
) -> dict:
    membership = request.organization_membership
    if membership.role not in {OrganizationMembershipRole.OWNER, OrganizationMembershipRole.ADMIN}:
        raise GmailError("role_not_allowed", status_code=403)
    if not SAFE_REDIRECT_PATTERN.fullmatch(intended_redirect or ""):
        raise GmailError("invalid_redirect")
    if not integration_readiness()["enabled"]:
        raise GmailError("gmail_integration_not_ready", status_code=409)
    if initial_sync_mode not in GmailInitialSyncMode.values:
        raise GmailError("initial_sync_mode_invalid")
    try:
        initial_sync_max_messages = int(initial_sync_max_messages)
    except (TypeError, ValueError) as exc:
        raise GmailError("initial_sync_limit_invalid") from exc
    if not 1 <= initial_sync_max_messages <= settings.GOOGLE_GMAIL_FULL_SYNC_MAX_MESSAGES:
        raise GmailError("initial_sync_limit_invalid")
    if reconnect_connection and reconnect_connection.organization_id != request.organization.id:
        raise GmailError("gmail_connection_not_found", status_code=404)
    raw_state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    GmailOAuthState.objects.create(
        organization=request.organization,
        state_hash=_hash(raw_state),
        user_id=request.user.id,
        membership=membership,
        intended_redirect=intended_redirect,
        code_verifier=verifier,
        initial_sync_mode=initial_sync_mode,
        initial_sync_max_messages=initial_sync_max_messages,
        reconnect_connection=reconnect_connection,
        expires_at=timezone.now() + timedelta(minutes=settings.GOOGLE_GMAIL_OAUTH_STATE_MINUTES),
    )
    if integration_readiness()["fake_provider"] and not settings.GOOGLE_GMAIL_ENABLE_LIVE:
        authorization_url = f"{settings.CLIENT_APP_URL.rstrip('/')}{intended_redirect}?gmail=fake&state={raw_state}"
    else:
        authorization_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {
                "client_id": settings.GOOGLE_GMAIL_CLIENT_ID,
                "redirect_uri": settings.GOOGLE_GMAIL_REDIRECT_URI,
                "response_type": "code",
                "scope": GMAIL_MODIFY_SCOPE,
                "access_type": "offline",
                "include_granted_scopes": "true",
                "prompt": "consent",
                "state": raw_state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    return {
        "authorization_url": authorization_url,
        "state": raw_state if integration_readiness()["fake_provider"] else None,
        "expires_in": settings.GOOGLE_GMAIL_OAUTH_STATE_MINUTES * 60,
        "mode": integration_readiness()["mode"],
        "scope": GMAIL_MODIFY_SCOPE,
        "initial_sync_mode": initial_sync_mode,
        "initial_sync_max_messages": initial_sync_max_messages,
    }


@transaction.atomic
def complete_oauth(*, user, raw_state: str, code: str) -> GmailConnection:
    if not raw_state or not code or not SAFE_CODE_PATTERN.fullmatch(code):
        raise GmailError("invalid_oauth_callback")
    state = (
        GmailOAuthState.objects.select_for_update()
        .filter(state_hash=_hash(raw_state))
        .first()
    )
    if not state:
        raise GmailError("oauth_state_invalid", status_code=404)
    if state.consumed_at:
        raise GmailError("oauth_state_replayed", status_code=409)
    if state.expires_at <= timezone.now():
        raise GmailError("oauth_state_expired", status_code=410)
    if state.user_id != user.id or state.membership.user_id != user.id:
        raise GmailError("oauth_state_user_mismatch", status_code=403)
    state.consumed_at = timezone.now()
    state.save(update_fields=["consumed_at"])
    try:
        snapshot = gmail_provider().exchange_code(code=code, code_verifier=state.code_verifier)
    except GmailProviderError as exc:
        raise GmailError(exc.code, status_code=exc.status_code) from exc
    if GMAIL_MODIFY_SCOPE not in snapshot.scope:
        raise GmailError("required_gmail_scope_missing", status_code=409)
    reconnect = state.reconnect_connection
    duplicates = GmailConnection.objects.filter(
        mailbox_email_normalized=snapshot.email.casefold(),
        connection_status__in=[
            GmailConnectionStatus.SYNCING,
            GmailConnectionStatus.CONNECTED,
            GmailConnectionStatus.DEGRADED,
            GmailConnectionStatus.REAUTH_REQUIRED,
            GmailConnectionStatus.REVOKED,
            GmailConnectionStatus.PERMISSION_MISSING,
            GmailConnectionStatus.WATCH_EXPIRED,
        ],
    )
    if reconnect:
        duplicates = duplicates.exclude(pk=reconnect.pk)
    if duplicates.exists():
        raise GmailError("gmail_mailbox_already_connected", status_code=409)
    if reconnect:
        connection = GmailConnection.objects.select_for_update().select_related(
            "channel_connection"
        ).get(pk=reconnect.pk, organization=state.organization)
        if connection.mailbox_email_normalized != snapshot.email.casefold():
            raise GmailError("reconnect_mailbox_mismatch", status_code=409)
        channel = connection.channel_connection
        previous_credentials = channel.get_credentials()
        refresh_token = snapshot.refresh_token or str(
            previous_credentials.get("refresh_token") or ""
        )
        if not refresh_token:
            raise GmailError("oauth_scope_or_offline_token_missing", status_code=409)
        channel.set_credentials(
            {
                "access_token": snapshot.access_token,
                "refresh_token": refresh_token,
                "token_type": "Bearer",
            }
        )
        channel.status = ChannelStatus.ACTIVE
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
        connection.mailbox_name = snapshot.name
        connection.google_user_id = snapshot.google_user_id
        connection.scope_snapshot = list(snapshot.scope)
        connection.connection_status = GmailConnectionStatus.CONNECTED
        connection.token_expires_at = timezone.now() + timedelta(seconds=snapshot.expires_in)
        connection.disconnected_at = None
        connection.initial_sync_status = GmailInitialSyncStatus.PENDING
        connection.initial_sync_cancel_requested_at = None
        connection.last_error_code = ""
        connection.save(
            update_fields=[
                "mailbox_name",
                "google_user_id",
                "scope_snapshot",
                "connection_status",
                "token_expires_at",
                "disconnected_at",
                "initial_sync_status",
                "initial_sync_cancel_requested_at",
                "last_error_code",
                "updated_at",
            ]
        )
    else:
        if not snapshot.refresh_token:
            raise GmailError("oauth_scope_or_offline_token_missing", status_code=409)
        channel = ChannelConnection(
            organization=state.organization,
            type=ChannelType.GMAIL,
            provider="google_gmail",
            display_name=f"Gmail {snapshot.email}",
            external_identifier=snapshot.email.casefold(),
            status=ChannelStatus.ACTIVE,
            configuration={"provider_family": "google", "scope": "gmail.modify"},
        )
        channel.set_credentials(
            {
                "access_token": snapshot.access_token,
                "refresh_token": snapshot.refresh_token,
                "token_type": "Bearer",
            }
        )
        channel.full_clean()
        channel.save()
        connection = GmailConnection(
            organization=state.organization,
            channel_connection=channel,
            mailbox_email=snapshot.email,
            mailbox_name=snapshot.name,
            google_user_id=snapshot.google_user_id,
            scope_snapshot=list(snapshot.scope),
            connection_status=GmailConnectionStatus.CONNECTED,
            token_expires_at=timezone.now() + timedelta(seconds=snapshot.expires_in),
            connected_by=state.membership,
            initial_sync_mode=state.initial_sync_mode,
            initial_sync_max_messages=state.initial_sync_max_messages,
            sync_start_at=timezone.now(),
        )
        connection.full_clean()
        try:
            connection.save()
        except IntegrityError as exc:
            raise GmailError("gmail_mailbox_already_connected", status_code=409) from exc
    try:
        watch = gmail_provider().start_watch(connection)
        connection.history_id = str(watch["history_id"])
        connection.watch_expiration_at = watch["expiration"]
        connection.watch_topic = settings.GOOGLE_GMAIL_PUBSUB_TOPIC
        connection.save(update_fields=["history_id", "watch_expiration_at", "watch_topic", "updated_at"])
    except GmailProviderError as exc:
        connection.connection_status = GmailConnectionStatus.DEGRADED
        connection.last_error_code = exc.code
        connection.save(update_fields=["connection_status", "last_error_code", "updated_at"])
    event_type = "gmail.reconnected" if reconnect else "gmail.connected"
    _audit(
        organization=state.organization,
        event_type=event_type,
        connection=connection,
        actor=state.membership,
        metadata={"mailbox_domain": snapshot.email.rsplit("@", 1)[-1]},
    )
    record_activity(
        organization=state.organization,
        actor_membership=state.membership,
        event_type=event_type,
        summary="Gmail mailbox reconnected" if reconnect else "Gmail mailbox connected",
        metadata={"gmail_connection_id": str(connection.id)},
    )
    from gmail_integration.tasks import initial_gmail_sync

    transaction.on_commit(lambda: initial_gmail_sync.delay(str(connection.id)))
    connection.oauth_redirect = state.intended_redirect
    return connection


def verify_pubsub_identity(authorization: str) -> dict:
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise GmailError("pubsub_authentication_required", status_code=401)
    token = authorization[len(prefix):].strip()
    if integration_readiness()["fake_provider"] and token == settings.GOOGLE_GMAIL_FAKE_PUBSUB_TOKEN:
        return {
            "email": settings.GOOGLE_GMAIL_PUBSUB_SERVICE_ACCOUNT or "fake-pubsub@example.test",
            "email_verified": True,
            "aud": settings.GOOGLE_GMAIL_PUBSUB_AUDIENCE,
        }
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        claims = id_token.verify_oauth2_token(
            token, google_requests.Request(), audience=settings.GOOGLE_GMAIL_PUBSUB_AUDIENCE
        )
    except Exception as exc:
        raise GmailError("pubsub_identity_invalid", status_code=403) from exc
    if (
        not claims.get("email_verified")
        or claims.get("email") != settings.GOOGLE_GMAIL_PUBSUB_SERVICE_ACCOUNT
        or claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}
    ):
        raise GmailError("pubsub_identity_invalid", status_code=403)
    return claims


def receive_pubsub(*, authorization: str, raw_body: bytes) -> dict:
    verify_pubsub_identity(authorization)
    if len(raw_body) > settings.GOOGLE_GMAIL_MAX_NOTIFICATION_BYTES:
        raise GmailError("notification_too_large", status_code=413)
    try:
        envelope = json.loads(raw_body.decode())
        subscription = str(envelope.get("subscription") or "")
        message = envelope["message"]
        pubsub_id = str(message["messageId"])
        encoded_data = str(message["data"])
        if not re.fullmatch(r"[A-Za-z0-9_+/=-]{1,8192}", encoded_data):
            raise ValueError("notification_data_invalid")
        encoded_data += "=" * (-len(encoded_data) % 4)
        data = json.loads(
            base64.b64decode(encoded_data, altchars=b"-_", validate=True).decode()
        )
        mailbox = str(data["emailAddress"]).strip().casefold()
        history_id = str(data["historyId"])
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise GmailError("notification_invalid") from exc
    if settings.GOOGLE_GMAIL_PUBSUB_SUBSCRIPTION and subscription != settings.GOOGLE_GMAIL_PUBSUB_SUBSCRIPTION:
        raise GmailError("pubsub_subscription_mismatch", status_code=403)
    connection = GmailConnection.objects.filter(
        mailbox_email_normalized=mailbox,
        connection_status__in=[
            GmailConnectionStatus.SYNCING,
            GmailConnectionStatus.CONNECTED,
            GmailConnectionStatus.DEGRADED,
        ],
        organization__status__in=[OrganizationStatus.TRIAL, OrganizationStatus.ACTIVE],
    ).first()
    if not connection:
        return {"accepted": 0, "ignored": 1}
    notification, created = GmailNotification.objects.get_or_create(
        pubsub_message_id=pubsub_id,
        defaults={
            "organization": connection.organization,
            "connection": connection,
            "history_id": history_id,
        },
    )
    if created:
        from gmail_integration.tasks import process_gmail_notification

        transaction.on_commit(lambda: process_gmail_notification.delay(str(notification.id)))
    return {"accepted": int(created), "duplicates": int(not created)}


def _ingest_provider_message(connection, payload: dict, *, historical: bool) -> tuple[Message | None, bool]:
    parsed = parse_gmail_message(payload, mailbox_email=connection.mailbox_email_normalized)
    if parsed.is_from_self or parsed.has_althair_origin:
        return None, False
    existing = GmailMessageRecord.objects.filter(
        connection=connection, gmail_message_id=parsed.gmail_message_id
    ).first()
    if existing:
        existing.label_ids = list(parsed.label_ids)
        existing.save(update_fields=["label_ids"])
        existing.message.metadata = {
            **existing.message.metadata,
            "labels": list(parsed.label_ids),
        }
        existing.message.save(update_fields=["metadata", "updated_at"])
        return existing.message, False
    labels = set(parsed.label_ids)
    if labels & set(connection.excluded_label_ids) or not labels.intersection(
        connection.included_label_ids
    ):
        return None, False
    enqueue_ai = not historical and not parsed.is_automated and not parsed.is_encrypted
    participants = list(
        dict.fromkeys(
            [
                parsed.sender_email,
                *parsed.to_recipients,
                *parsed.cc_recipients,
            ]
        )
    )
    message, created = ingest_inbound_message(
        organization=connection.organization,
        channel_connection=connection.channel_connection,
        identity_type=ContactIdentityType.EMAIL,
        sender_value=parsed.sender_email,
        sender_display_name=parsed.sender_name,
        external_thread_id=parsed.gmail_thread_id,
        provider_message_id=f"gmail:{parsed.google_message_id}" if hasattr(parsed, "google_message_id") else f"gmail:{parsed.gmail_message_id}",
        body=parsed.body,
        occurred_at=parsed.occurred_at,
        metadata={
            "provider": "gmail",
            "subject": parsed.subject,
            "participants": participants,
            "to": list(parsed.to_recipients),
            "cc": list(parsed.cc_recipients),
            "attachments": [
                {key: value for key, value in item.items() if key != "attachment_id"}
                for item in parsed.attachments
            ],
            "automated": parsed.is_automated,
            "encrypted": parsed.is_encrypted,
            "historical": historical,
            "labels": list(parsed.label_ids),
        },
        # Gmail policy depends on the provider record below (thread, loop and
        # encryption state), so enqueue only after that record is durable.
        enqueue_ai=False,
    )
    if created:
        Conversation.objects.filter(pk=message.conversation_id).update(subject=parsed.subject)
        record = GmailMessageRecord.objects.create(
            organization=connection.organization,
            connection=connection,
            message=message,
            gmail_message_id=parsed.gmail_message_id,
            gmail_thread_id=parsed.gmail_thread_id,
            rfc_message_id=parsed.rfc_message_id,
            in_reply_to=parsed.in_reply_to,
            references=parsed.references,
            subject=parsed.subject,
            reply_to=parsed.reply_to,
            to_recipients=list(parsed.to_recipients),
            cc_recipients=list(parsed.cc_recipients),
            participants=participants,
            label_ids=list(parsed.label_ids),
            snippet=parsed.snippet,
            attachment_metadata=list(parsed.attachments),
            is_automated=parsed.is_automated,
            is_encrypted=parsed.is_encrypted,
            is_from_self=parsed.is_from_self,
            is_historical=historical,
            internal_date=parsed.occurred_at,
        )
        if parsed.attachments:
            message.metadata = {
                **message.metadata,
                "attachments": [
                    {
                        key: value
                        for key, value in item.items()
                        if key != "attachment_id"
                    }
                    | {
                        "download_path": f"/integrations/gmail/attachments/{record.id}/{index}/"
                    }
                    for index, item in enumerate(parsed.attachments)
                ],
            }
            message.save(update_fields=["metadata", "updated_at"])
        if enqueue_ai:
            from ai_runtime.tasks import evaluate_inbound_message

            transaction.on_commit(
                lambda message_id=message.id: evaluate_inbound_message.delay(
                    str(message_id)
                )
            )
    return message, created


def bounded_full_sync(connection, *, sync_type=GmailSyncType.FULL, fallback_reason=""):
    run = GmailSyncRun.objects.create(
        organization=connection.organization,
        connection=connection,
        sync_type=sync_type,
        start_history_id=connection.history_id,
        fallback_reason=fallback_reason,
    )
    try:
        if (
            sync_type == GmailSyncType.INITIAL
            and connection.initial_sync_mode == GmailInitialSyncMode.FROM_NOW
        ):
            payloads, end_history_id = [], connection.history_id
        else:
            sync_limit = min(
                connection.initial_sync_max_messages,
                settings.GOOGLE_GMAIL_FULL_SYNC_MAX_MESSAGES,
            )
            payloads, end_history_id = gmail_provider().list_recent(
                connection,
                limit=sync_limit,
            )
            payloads = list(payloads)[:sync_limit]
        imported = 0
        ignored = 0
        for payload in reversed(payloads):
            if sync_type == GmailSyncType.INITIAL:
                connection.refresh_from_db(fields=["initial_sync_status"])
                if connection.initial_sync_status == GmailInitialSyncStatus.CANCELLED:
                    run.status = GmailSyncStatus.CANCELLED
                    run.completed_at = timezone.now()
                    run.save(update_fields=["status", "completed_at"])
                    return run
            _, created = _ingest_provider_message(connection, payload, historical=True)
            imported += int(created)
            ignored += int(not created)
        now = timezone.now()
        connection.history_id = end_history_id or connection.history_id
        connection.last_full_sync_at = now
        connection.channel_connection.last_synced_at = now
        connection.channel_connection.save(update_fields=["last_synced_at", "updated_at"])
        update_fields = ["history_id", "last_full_sync_at", "updated_at"]
        if sync_type == GmailSyncType.INITIAL:
            connection.initial_sync_status = GmailInitialSyncStatus.SUCCEEDED
            connection.connection_status = GmailConnectionStatus.CONNECTED
            update_fields.extend(["initial_sync_status", "connection_status"])
        connection.save(update_fields=update_fields)
        run.status = GmailSyncStatus.SUCCEEDED
        run.end_history_id = connection.history_id
        run.imported_count = imported
        run.ignored_count = ignored
        run.completed_at = now
        run.save(update_fields=["status", "end_history_id", "imported_count", "ignored_count", "completed_at"])
        return run
    except (GmailProviderError, ValueError) as exc:
        run.status = GmailSyncStatus.FAILED
        run.safe_error_code = getattr(exc, "code", str(exc))[:80]
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "safe_error_code", "completed_at"])
        if sync_type == GmailSyncType.INITIAL:
            connection.initial_sync_status = GmailInitialSyncStatus.FAILED
            connection.connection_status = GmailConnectionStatus.DEGRADED
            connection.last_error_code = run.safe_error_code
            connection.save(
                update_fields=[
                    "initial_sync_status",
                    "connection_status",
                    "last_error_code",
                    "updated_at",
                ]
            )
        raise


def incremental_sync(connection, *, target_history_id="", sync_type=GmailSyncType.INCREMENTAL):
    if not connection.history_id:
        return bounded_full_sync(connection, sync_type=GmailSyncType.FULL, fallback_reason="cursor_missing")
    run = GmailSyncRun.objects.create(
        organization=connection.organization,
        connection=connection,
        sync_type=sync_type,
        start_history_id=connection.history_id,
    )
    try:
        ids, end_history_id = gmail_provider().list_history(
            connection,
            start_history_id=connection.history_id,
            limit=settings.GOOGLE_GMAIL_INCREMENTAL_MAX_MESSAGES,
        )
        imported = 0
        ignored = 0
        for message_id in ids:
            payload = gmail_provider().get_message(connection, message_id)
            _, created = _ingest_provider_message(connection, payload, historical=False)
            imported += int(created)
            ignored += int(not created)
        next_cursor = str(max(int(end_history_id or 0), int(target_history_id or 0), int(connection.history_id or 0)))
        now = timezone.now()
        connection.history_id = next_cursor
        connection.last_incremental_sync_at = now
        connection.failure_count = 0
        connection.last_error_code = ""
        connection.channel_connection.last_synced_at = now
        connection.channel_connection.save(update_fields=["last_synced_at", "updated_at"])
        connection.save(
            update_fields=[
                "history_id",
                "last_incremental_sync_at",
                "failure_count",
                "last_error_code",
                "updated_at",
            ]
        )
        run.status = GmailSyncStatus.SUCCEEDED
        run.end_history_id = next_cursor
        run.imported_count = imported
        run.ignored_count = ignored
        run.completed_at = now
        run.save(update_fields=["status", "end_history_id", "imported_count", "ignored_count", "completed_at"])
        return run
    except GmailHistoryExpired:
        run.status = GmailSyncStatus.FAILED
        run.safe_error_code = "history_cursor_expired"
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "safe_error_code", "completed_at"])
        return bounded_full_sync(connection, fallback_reason="history_cursor_expired")
    except (GmailProviderError, ValueError) as exc:
        run.status = GmailSyncStatus.FAILED
        run.safe_error_code = getattr(exc, "code", str(exc))[:80]
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "safe_error_code", "completed_at"])
        connection.failure_count = min(connection.failure_count + 1, 100)
        connection.last_error_code = run.safe_error_code
        connection.last_health_check_at = timezone.now()
        connection.save(
            update_fields=[
                "failure_count",
                "last_error_code",
                "last_health_check_at",
                "updated_at",
            ]
        )
        raise


@transaction.atomic
def process_notification(notification_id):
    notification = (
        GmailNotification.objects.select_for_update()
        .select_related("connection__channel_connection", "organization")
        .get(pk=notification_id)
    )
    if notification.status in {GmailNotificationStatus.PROCESSED, GmailNotificationStatus.IGNORED, GmailNotificationStatus.DEAD_LETTER}:
        return notification
    notification.status = GmailNotificationStatus.PROCESSING
    notification.attempt_count += 1
    notification.save(update_fields=["status", "attempt_count"])
    try:
        if notification.connection.initial_sync_status == GmailInitialSyncStatus.RUNNING:
            raise GmailProviderError("initial_sync_running", transient=True)
        incremental_sync(notification.connection, target_history_id=notification.history_id)
        notification.status = GmailNotificationStatus.PROCESSED
        notification.safe_error_code = ""
        notification.processed_at = timezone.now()
        notification.connection.last_notification_at = notification.received_at
        notification.connection.save(update_fields=["last_notification_at", "updated_at"])
    except (GmailProviderError, ValueError) as exc:
        notification.status = (
            GmailNotificationStatus.FAILED
            if notification.attempt_count < settings.GOOGLE_GMAIL_MAX_SYNC_ATTEMPTS
            else GmailNotificationStatus.DEAD_LETTER
        )
        notification.safe_error_code = getattr(exc, "code", str(exc))[:80]
    notification.save(update_fields=["status", "safe_error_code", "processed_at"])
    return notification


def connection_for_conversation(conversation):
    if conversation.channel_connection.type != ChannelType.GMAIL:
        return None
    return GmailConnection.objects.for_organization(conversation.organization).select_related("channel_connection").filter(
        channel_connection=conversation.channel_connection
    ).first()


def conversation_policy(conversation) -> dict:
    connection = connection_for_conversation(conversation)
    if not connection:
        return {"state": "provider_unavailable", "can_send": False}
    state = "can_reply"
    latest = GmailMessageRecord.objects.filter(
        connection=connection,
        message__conversation=conversation,
        message__direction=MessageDirection.INBOUND,
    ).order_by("-message__occurred_at").first()
    if conversation.organization.status not in {
        OrganizationStatus.TRIAL,
        OrganizationStatus.ACTIVE,
    }:
        state = "organization_read_only"
    elif connection.connection_status in {
        GmailConnectionStatus.REAUTH_REQUIRED,
        GmailConnectionStatus.REVOKED,
    }:
        state = "reauthorization_required"
    elif (
        connection.connection_status == GmailConnectionStatus.PERMISSION_MISSING
        or GMAIL_MODIFY_SCOPE not in connection.scope_snapshot
    ):
        state = "permission_missing"
    elif (
        connection.connection_status == GmailConnectionStatus.WATCH_EXPIRED
        or not connection.watch_expiration_at
        or connection.watch_expiration_at <= timezone.now()
    ):
        state = "watch_expired"
    elif connection.connection_status == GmailConnectionStatus.DEGRADED:
        state = "connection_degraded"
    elif connection.connection_status != GmailConnectionStatus.CONNECTED:
        state = "provider_unavailable"
    elif latest and latest.is_automated:
        state = "automated_message"
    elif latest and latest.is_encrypted:
        state = "encrypted_message"
    if not latest:
        state = "thread_context_missing"
    thread_url = ""
    if latest and re.fullmatch(r"[A-Za-z0-9_-]{1,255}", latest.gmail_thread_id):
        thread_url = (
            "https://mail.google.com/mail/u/"
            + connection.mailbox_email_normalized
            + "/#all/"
            + latest.gmail_thread_id
        )
    return {
        "state": state,
        "can_send": state == "can_reply",
        "mailbox": connection.mailbox_email,
        "subject": conversation.subject,
        "automation_mode": connection.automation_mode,
        "watch_expiration_at": connection.watch_expiration_at,
        "last_sync_at": connection.channel_connection.last_synced_at,
        "participants": latest.participants if latest else [],
        "labels": latest.label_ids if latest else [],
        "has_attachments": bool(latest and latest.attachment_metadata),
        "thread_url": thread_url,
    }


def can_send_gmail(conversation):
    return conversation_policy(conversation)["can_send"]


def _send_throttle(connection):
    key = f"gmail:org-send:{connection.organization_id}:{timezone.now():%Y%m%d%H%M}"
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=90)
        count = 1
    if count > settings.GOOGLE_GMAIL_ORG_SENDS_PER_MINUTE:
        raise GmailError("organization_send_rate_limited", status_code=429)


def _normalize_cc(values, *, mailbox: str, recipient: str) -> tuple[str, ...]:
    if values in (None, ""):
        return ()
    if not isinstance(values, (list, tuple)) or len(values) > 10:
        raise GmailError("cc_invalid")
    normalized: list[str] = []
    for value in values:
        address = str(value).strip().casefold()
        try:
            validate_email(address)
        except ValidationError as exc:
            raise GmailError("cc_invalid") from exc
        if address not in {mailbox, recipient} and address not in normalized:
            normalized.append(address)
    return tuple(normalized)


def send_gmail_message(
    *,
    conversation,
    body,
    client_message_id,
    membership=None,
    sender_type=MessageSenderType.AGENT,
    metadata=None,
    cc=None,
):
    existing = Message.objects.for_organization(conversation.organization).filter(
        conversation=conversation, client_message_id=client_message_id
    ).first()
    if existing:
        return existing, False
    connection = connection_for_conversation(conversation)
    if not connection:
        raise GmailError("provider_unavailable", status_code=409)
    policy = conversation_policy(conversation)
    if not policy["can_send"]:
        raise GmailError(policy["state"], status_code=409)
    text = (body or "").strip()
    if not text or len(text) > settings.GOOGLE_GMAIL_MAX_SEND_TEXT:
        raise GmailError("message_length_invalid")
    source = GmailMessageRecord.objects.filter(
        connection=connection,
        message__conversation=conversation,
        message__direction=MessageDirection.INBOUND,
    ).select_related("message").order_by("-message__occurred_at").first()
    if not source:
        raise GmailError("thread_context_missing", status_code=409)
    _send_throttle(connection)
    lock_key = f"gmail:send-lock:{conversation.id}"
    if not cache.add(lock_key, "1", timeout=settings.GOOGLE_GMAIL_SEND_LOCK_SECONDS):
        raise GmailError("conversation_send_busy", status_code=409)
    now = timezone.now()
    try:
        recipient = source.reply_to or next(
            (
                item
                for item in source.participants
                if item.casefold() != connection.mailbox_email_normalized
            ),
            conversation.contact.identities.filter(type=ContactIdentityType.EMAIL)
            .values_list("normalized_value", flat=True)
            .first(),
        )
        if not recipient:
            raise GmailError("recipient_missing", status_code=409)
        try:
            validate_email(recipient)
        except ValidationError as exc:
            raise GmailError("recipient_invalid", status_code=409) from exc
        cc_recipients = _normalize_cc(
            cc,
            mailbox=connection.mailbox_email_normalized,
            recipient=recipient,
        )
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
                "provider": "gmail",
                "ai_generated": sender_type == MessageSenderType.AI,
                "cc": list(cc_recipients),
                **(metadata or {}),
            },
            occurred_at=now,
        )
        message.full_clean()
        message.save()
        attempt = GmailOutboundAttempt.objects.create(
            organization=conversation.organization,
            connection=connection,
            message=message,
            status=GmailOutboundStatus.SENDING,
            attempt_count=1,
        )
        raw = build_rfc_reply(
            mailbox_email=connection.mailbox_email_normalized,
            recipient=recipient,
            subject=source.subject or conversation.subject,
            body=text,
            in_reply_to=source.rfc_message_id,
            references=source.references,
            origin_id=f"message:{message.id}",
            cc=cc_recipients,
        )
        try:
            result = gmail_provider().send_reply(connection, thread_id=source.gmail_thread_id, raw_message=raw)
        except GmailProviderError as exc:
            attempt.status = GmailOutboundStatus.QUEUED if exc.transient else GmailOutboundStatus.FAILED
            attempt.safe_error_code = exc.code
            attempt.next_retry_at = timezone.now() + timedelta(seconds=30) if exc.transient else None
            attempt.save(update_fields=["status", "safe_error_code", "next_retry_at", "updated_at"])
            message.status = MessageStatus.QUEUED if exc.transient else MessageStatus.FAILED
            message.error_code = exc.code
            message.save(update_fields=["status", "error_code", "updated_at"])
            if exc.transient:
                from gmail_integration.tasks import retry_gmail_outbound

                retry_gmail_outbound.apply_async(args=[str(attempt.id)], countdown=30)
            return message, True
        message.provider_message_id = f"gmail:{result.message_id}"
        message.status = MessageStatus.SENT
        message.save(update_fields=["provider_message_id", "status", "updated_at"])
        GmailMessageRecord.objects.create(
            organization=conversation.organization,
            connection=connection,
            message=message,
            gmail_message_id=result.message_id,
            gmail_thread_id=result.thread_id,
            subject=source.subject,
            to_recipients=[recipient],
            cc_recipients=list(cc_recipients),
            participants=list(dict.fromkeys([recipient, *cc_recipients])),
            label_ids=["SENT"],
        )
        attempt.status = GmailOutboundStatus.SENT
        attempt.provider_request_id = result.request_id
        attempt.save(update_fields=["status", "provider_request_id", "updated_at"])
        connection.last_successful_send_at = now
        connection.last_error_code = ""
        connection.save(update_fields=["last_successful_send_at", "last_error_code", "updated_at"])
        Conversation.objects.filter(pk=conversation.pk).update(last_message_at=now, last_outbound_at=now)
        if sender_type == MessageSenderType.AGENT:
            Conversation.objects.filter(pk=conversation.pk).update(
                ai_state=ConversationAIState.PAUSED_BY_HUMAN, ai_state_updated_at=now
            )
            from ai_runtime.services import supersede_active_runs

            supersede_active_runs(conversation=conversation, reason="human_reply")
        record_activity(
            organization=conversation.organization,
            actor_membership=membership,
            event_type="message.sent",
            summary="Gmail reply sent",
            contact=conversation.contact,
            conversation=conversation,
            metadata={"provider": "gmail"},
        )
        return message, True
    finally:
        cache.delete(lock_key)


def send_ai_message(*, run, body, client_message_id, metadata):
    return send_gmail_message(
        conversation=run.conversation,
        body=body,
        client_message_id=client_message_id,
        sender_type=MessageSenderType.AI,
        metadata=metadata,
    )


def ai_state_for_connection(organization, channel_connection):
    if channel_connection.type != ChannelType.GMAIL:
        return None
    connection = GmailConnection.objects.for_organization(organization).filter(
        channel_connection=channel_connection
    ).first()
    if not connection or connection.connection_status != GmailConnectionStatus.CONNECTED:
        return ConversationAIState.OFF
    return {
        GmailAutomationMode.MANUAL: ConversationAIState.OFF,
        GmailAutomationMode.SUGGEST: ConversationAIState.SUGGEST,
        GmailAutomationMode.AUTOPILOT: ConversationAIState.AUTOPILOT_GMAIL,
    }[connection.automation_mode]


def gmail_autopilot_allowed(conversation):
    connection = connection_for_conversation(conversation)
    latest = GmailMessageRecord.objects.filter(
        connection=connection, message__conversation=conversation, message__direction=MessageDirection.INBOUND
    ).order_by("-message__occurred_at").first() if connection else None
    return bool(
        connection
        and latest
        and not latest.is_automated
        and not latest.is_encrypted
        and not latest.is_historical
        and connection.automation_mode == GmailAutomationMode.AUTOPILOT
        and connection.connection_status == GmailConnectionStatus.CONNECTED
        and conversation.ai_state == ConversationAIState.AUTOPILOT_GMAIL
        and can_send_gmail(conversation)
    )


def renew_watch(connection):
    result = gmail_provider().start_watch(connection)
    connection.history_id = connection.history_id or str(result["history_id"])
    connection.watch_expiration_at = result["expiration"]
    connection.watch_topic = settings.GOOGLE_GMAIL_PUBSUB_TOPIC
    connection.connection_status = GmailConnectionStatus.CONNECTED
    connection.last_error_code = ""
    connection.failure_count = 0
    connection.save(update_fields=["history_id", "watch_expiration_at", "watch_topic", "connection_status", "last_error_code", "failure_count", "updated_at"])
    return connection


def connection_health(connection, *, run_provider=False):
    visible_status = connection.connection_status
    try:
        credentials = connection.channel_connection.get_credentials()
    except (TypeError, ValueError):
        credentials = {}
    if GMAIL_MODIFY_SCOPE not in connection.scope_snapshot:
        visible_status = GmailConnectionStatus.PERMISSION_MISSING
    elif (
        connection.connection_status == GmailConnectionStatus.CONNECTED
        and (
            not connection.watch_expiration_at
            or connection.watch_expiration_at <= timezone.now()
        )
    ):
        visible_status = GmailConnectionStatus.WATCH_EXPIRED
    state = {
        "status": visible_status,
        "has_encrypted_access_token": bool(credentials.get("access_token")),
        "has_encrypted_refresh_token": bool(credentials.get("refresh_token")),
        "watch_active": bool(connection.watch_expiration_at and connection.watch_expiration_at > timezone.now()),
        "watch_expiration_at": connection.watch_expiration_at,
        "last_sync_at": connection.channel_connection.last_synced_at,
        "last_error_code": connection.last_error_code,
        "provider_reachable": None,
        "mailbox_matches": None,
        "scope_valid": GMAIL_MODIFY_SCOPE in connection.scope_snapshot,
    }
    if run_provider:
        try:
            provider_state = gmail_provider().health(connection)
            state.update(provider_state)
            connection.last_health_check_at = timezone.now()
            connection.connection_status = (
                GmailConnectionStatus.CONNECTED
                if all(provider_state.get(key) for key in ("provider_reachable", "mailbox_matches", "scope_valid"))
                else GmailConnectionStatus.DEGRADED
            )
            connection.last_error_code = "" if connection.connection_status == GmailConnectionStatus.CONNECTED else "gmail_health_mismatch"
            connection.failure_count = (
                0
                if connection.connection_status == GmailConnectionStatus.CONNECTED
                else min(connection.failure_count + 1, 100)
            )
            connection.save(update_fields=["last_health_check_at", "connection_status", "last_error_code", "failure_count", "updated_at"])
            state.update(
                {
                    "status": connection.connection_status,
                    "last_error_code": connection.last_error_code,
                }
            )
        except GmailProviderError as exc:
            connection.last_health_check_at = timezone.now()
            if exc.code in {"refresh_token_missing", "token_refresh_failed"}:
                connection.connection_status = GmailConnectionStatus.REAUTH_REQUIRED
            elif exc.code == "gmail_permission_missing":
                connection.connection_status = GmailConnectionStatus.PERMISSION_MISSING
            else:
                connection.connection_status = GmailConnectionStatus.DEGRADED
            connection.last_error_code = exc.code
            connection.failure_count = min(connection.failure_count + 1, 100)
            connection.save(update_fields=["last_health_check_at", "connection_status", "last_error_code", "failure_count", "updated_at"])
            state.update(
                {
                    "status": connection.connection_status,
                    "last_error_code": exc.code,
                    "provider_reachable": False,
                }
            )
    return state


@transaction.atomic
def disconnect_gmail(connection, membership):
    connection = GmailConnection.objects.select_for_update().select_related("channel_connection").get(pk=connection.pk)
    try:
        gmail_provider().stop_watch(connection)
    except GmailProviderError:
        pass
    channel = connection.channel_connection
    channel.encrypted_credentials = ""
    channel.status = ChannelStatus.DISCONNECTED
    channel.last_error_code = ""
    channel.last_error_message = ""
    channel.save(update_fields=["encrypted_credentials", "status", "last_error_code", "last_error_message", "updated_at"])
    connection.connection_status = GmailConnectionStatus.DISCONNECTED
    connection.disconnected_at = timezone.now()
    connection.history_id = ""
    connection.watch_expiration_at = None
    connection.save(update_fields=["connection_status", "disconnected_at", "history_id", "watch_expiration_at", "updated_at"])
    _audit(organization=connection.organization, event_type="gmail.disconnected", connection=connection, actor=membership)
    return connection


def verification_checklist(connection=None):
    organization_settings = connection.organization.settings if connection else {}
    readiness = integration_readiness()
    configured_privacy = bool(organization_settings.get("privacy_policy_url"))
    return {
        "approval_claimed": False,
        "items": [
            {"key": "verified_product_domain", "ready": bool(organization_settings.get("google_verified_product_domain"))},
            {"key": "accurate_homepage", "ready": bool(organization_settings.get("public_homepage_url"))},
            {"key": "privacy_policy", "ready": configured_privacy},
            {"key": "domain_ownership", "ready": bool(organization_settings.get("google_domain_ownership_verified"))},
            {"key": "exact_scope", "ready": bool(connection and connection.scope_snapshot == [GMAIL_MODIFY_SCOPE])},
            {"key": "scope_justification", "ready": True},
            {"key": "narrower_scope_explanation", "ready": True},
            {"key": "demo_video", "ready": bool(organization_settings.get("google_oauth_demo_video_url"))},
            {"key": "user_data_disclosure", "ready": configured_privacy},
            {"key": "data_delete_export", "ready": True},
            {"key": "retention", "ready": bool(connection and connection.retention_days)},
            {"key": "google_branding", "ready": bool(organization_settings.get("google_branding_reviewed"))},
            {"key": "project_separation", "ready": readiness["fake_provider"] or readiness["live_ready"]},
            {"key": "security_assessment", "ready": bool(organization_settings.get("google_security_assessment_complete"))},
            {"key": "annual_review", "ready": bool(organization_settings.get("google_annual_review_owner"))},
            {"key": "test_user_instructions", "ready": bool(organization_settings.get("google_test_user_instructions_url"))},
        ],
    }


def export_gmail_contact_data(*, connection, contact_id):
    contact = Contact.objects.for_organization(connection.organization).filter(pk=contact_id).first()
    if not contact:
        raise GmailError("contact_not_found", status_code=404)
    conversations = Conversation.objects.for_organization(connection.organization).filter(
        channel_connection=connection.channel_connection,
        contact=contact,
    )
    if not conversations.exists():
        raise GmailError("gmail_contact_not_found", status_code=404)
    rows = Message.objects.for_organization(connection.organization).filter(
        conversation__in=conversations
    ).order_by("occurred_at")[:1000]
    messages = []
    for message in rows:
        metadata = dict(message.metadata or {})
        metadata.pop("download_path", None)
        messages.append(
            {
                "id": str(message.id),
                "conversation_id": str(message.conversation_id),
                "direction": message.direction,
                "sender_type": message.sender_type,
                "body": message.body,
                "occurred_at": message.occurred_at,
                "metadata": metadata,
            }
        )
    return {
        "export_version": 1,
        "generated_at": timezone.now(),
        "organization_id": str(connection.organization_id),
        "connection_id": str(connection.id),
        "contact": {
            "id": str(contact.id),
            "display_name": contact.display_name,
            "company_name": contact.company_name,
            "email_identities": list(
                ContactIdentity.objects.for_organization(connection.organization)
                .filter(
                    contact=contact,
                    channel_connection=connection.channel_connection,
                    type=ContactIdentityType.EMAIL,
                )
                .values_list("normalized_value", flat=True)
            ),
        },
        "conversations": list(
            conversations.values("id", "subject", "status", "created_at", "updated_at")
        ),
        "messages": messages,
        "truncated": len(messages) == 1000,
    }


@transaction.atomic
def erase_gmail_contact_data(*, connection, contact_id, mode: str, actor):
    if mode not in {"anonymize", "delete"}:
        raise GmailError("privacy_mode_invalid")
    contact = Contact.objects.select_for_update().for_organization(
        connection.organization
    ).filter(pk=contact_id).first()
    if not contact:
        raise GmailError("contact_not_found", status_code=404)
    conversations = Conversation.objects.select_for_update().for_organization(
        connection.organization
    ).filter(channel_connection=connection.channel_connection, contact=contact)
    if not conversations.exists():
        raise GmailError("gmail_contact_not_found", status_code=404)
    messages = Message.objects.for_organization(connection.organization).filter(
        conversation__in=conversations
    )
    records = GmailMessageRecord.objects.for_organization(connection.organization).filter(
        connection=connection,
        message__in=messages,
    )
    affected = messages.count()
    if mode == "delete":
        messages.delete()
    else:
        messages.update(
            body="[redacted by privacy request]",
            metadata={"provider": "gmail", "privacy_redacted": True},
        )
        records.update(
            rfc_message_id="",
            in_reply_to="",
            references="",
            subject="",
            reply_to="",
            to_recipients=[],
            cc_recipients=[],
            participants=[],
            snippet="",
            attachment_metadata=[],
        )
    conversations.update(subject="")
    ContactIdentity.objects.for_organization(connection.organization).filter(
        contact=contact,
        channel_connection=connection.channel_connection,
        type=ContactIdentityType.EMAIL,
    ).delete()
    if not contact.identities.exists():
        contact.display_name = "Anonymized contact"
        contact.first_name = ""
        contact.last_name = ""
        contact.company_name = ""
        contact.notes_summary = ""
        contact.save(
            update_fields=[
                "display_name",
                "first_name",
                "last_name",
                "company_name",
                "notes_summary",
                "updated_at",
            ]
        )
    _audit(
        organization=connection.organization,
        event_type=f"gmail.data_{mode}d",
        connection=connection,
        actor=actor,
        metadata={"contact_id": str(contact.id), "message_count": affected},
    )
    return {"mode": mode, "messages_affected": affected}


def cancel_initial_sync(connection, actor):
    if connection.initial_sync_status != GmailInitialSyncStatus.RUNNING:
        raise GmailError("initial_sync_not_running", status_code=409)
    connection.initial_sync_status = GmailInitialSyncStatus.CANCELLED
    connection.initial_sync_cancel_requested_at = timezone.now()
    connection.save(
        update_fields=[
            "initial_sync_status",
            "initial_sync_cancel_requested_at",
            "updated_at",
        ]
    )
    _audit(
        organization=connection.organization,
        event_type="gmail.initial_sync_cancelled",
        connection=connection,
        actor=actor,
    )
    return connection


def fetch_attachment(*, organization, record_id, index):
    record = GmailMessageRecord.objects.for_organization(organization).select_related(
        "connection__channel_connection"
    ).filter(pk=record_id).first()
    if not record:
        raise GmailError("attachment_not_found", status_code=404)
    if index < 0 or index >= len(record.attachment_metadata):
        raise GmailError("attachment_not_found", status_code=404)
    metadata = record.attachment_metadata[index]
    if int(metadata.get("size") or 0) > settings.GOOGLE_GMAIL_MAX_ATTACHMENT_BYTES:
        raise GmailError("attachment_too_large", status_code=413)
    attachment_id = str(metadata.get("attachment_id") or "")
    if not attachment_id:
        raise GmailError("attachment_not_found", status_code=404)
    mime_type = str(metadata.get("mime_type") or "").casefold()
    if mime_type not in {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "text/plain",
        "text/csv",
    }:
        raise GmailError("attachment_type_not_allowed", status_code=415)
    try:
        content = gmail_provider().get_attachment(
            record.connection,
            message_id=record.gmail_message_id,
            attachment_id=attachment_id,
        )
    except GmailProviderError as exc:
        raise GmailError(exc.code, status_code=exc.status_code) from exc
    gmail_attachment_scanner().scan(content=content, mime_type=mime_type)
    return metadata, content
