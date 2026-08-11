from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
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
from crm.services import ingest_inbound_message, record_activity
from organizations.models import OrganizationMembershipRole, OrganizationStatus
from telegram.models import (
    TelegramAuditEvent,
    TelegramAutomationMode,
    TelegramBotConnection,
    TelegramConnectionStatus,
    TelegramConnectionType,
    TelegramEventStatus,
    TelegramManagedBotRequest,
    TelegramManagedRequestStatus,
    TelegramManagerEvent,
    TelegramOutboundAttempt,
    TelegramUserLink,
    TelegramUserLinkStatus,
    TelegramWebhookEvent,
)
from telegram.providers import TelegramProviderError, telegram_provider


ALLOWED_UPDATES = ["message", "edited_message", "callback_query", "message_reaction", "my_chat_member"]
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,28}[Bb][Oo][Tt]$")


class TelegramError(Exception):
    def __init__(self, code: str, *, status_code=400):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _audit(*, organization, event_type, actor=None, connection=None, managed_request=None, metadata=None):
    return TelegramAuditEvent.objects.create(
        organization=organization,
        event_type=event_type,
        actor_membership=actor,
        connection=connection,
        managed_request=managed_request,
        metadata=metadata or {},
    )


def integration_readiness() -> dict:
    fake = bool(settings.TELEGRAM_FAKE_PROVIDER and (settings.DEBUG or settings.TESTING))
    if settings.TELEGRAM_ENABLE_LIVE:
        required = [
            settings.TELEGRAM_MANAGER_BOT_TOKEN,
            settings.TELEGRAM_MANAGER_BOT_USERNAME,
            settings.TELEGRAM_MANAGER_WEBHOOK_URL,
            settings.TELEGRAM_MANAGER_WEBHOOK_SECRET,
            settings.TELEGRAM_BOT_WEBHOOK_BASE_URL,
        ]
        return {"enabled": True, "fake_provider": False, "ready": all(required), "status": "configured" if all(required) else "configuration_incomplete"}
    return {"enabled": fake, "fake_provider": fake, "ready": True, "status": "fake_or_disabled"}


def manager_health(*, run_provider=False) -> dict:
    state = integration_readiness()
    result = {**state, "can_manage_bots": fake if (fake := state["fake_provider"]) else False, "manager_username": settings.TELEGRAM_MANAGER_BOT_USERNAME or ("AlthairManagerBot" if fake else "")}
    if run_provider and state["enabled"]:
        try:
            snapshot = telegram_provider().manager_health()
            result.update(snapshot)
            result["ready"] = bool(snapshot.get("reachable") and snapshot.get("can_manage_bots"))
            result["status"] = "ready" if result["ready"] else "manager_permission_missing"
        except TelegramProviderError as exc:
            result.update({"ready": False, "status": exc.code})
    return result


def create_user_link(*, user) -> dict:
    TelegramUserLink.objects.filter(user=user, status=TelegramUserLinkStatus.PENDING).update(status=TelegramUserLinkStatus.REVOKED, revoked_at=timezone.now())
    raw = secrets.token_urlsafe(24)
    link = TelegramUserLink.objects.create(user=user, token_hash=_hash(raw), expires_at=timezone.now() + timedelta(minutes=settings.TELEGRAM_LINK_TTL_MINUTES))
    username = settings.TELEGRAM_MANAGER_BOT_USERNAME or "AlthairManagerBot"
    return {"id": str(link.id), "status": link.status, "expires_at": link.expires_at, "telegram_url": f"https://t.me/{username}?start=link_{raw}"}


def active_user_link(user):
    link = TelegramUserLink.objects.filter(user=user).order_by("-created_at").first()
    if link and link.status == TelegramUserLinkStatus.PENDING and link.expires_at <= timezone.now():
        link.status = TelegramUserLinkStatus.EXPIRED
        link.save(update_fields=["status"])
    return link


@transaction.atomic
def revoke_user_link(*, user):
    count = TelegramUserLink.objects.filter(user=user, status__in=[TelegramUserLinkStatus.PENDING, TelegramUserLinkStatus.LINKED]).update(status=TelegramUserLinkStatus.REVOKED, revoked_at=timezone.now())
    return {"revoked": count}


def _constant_header(actual: str, expected: str) -> bool:
    return bool(actual and expected and hmac.compare_digest(actual, expected))


def _bounded_json(raw_body: bytes) -> dict:
    if not raw_body or len(raw_body) > settings.TELEGRAM_MAX_WEBHOOK_BYTES:
        raise TelegramError("webhook_payload_invalid", status_code=400)
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TelegramError("webhook_payload_invalid", status_code=400) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("update_id"), int):
        raise TelegramError("webhook_payload_invalid", status_code=400)
    return payload


def _normalize_manager(payload: dict) -> tuple[str, dict]:
    if isinstance(payload.get("managed_bot"), dict):
        item = payload["managed_bot"]
        user, bot = item.get("user") or {}, item.get("bot") or {}
        if not all([isinstance(user.get("id"), int), isinstance(bot.get("id"), int), bot.get("is_bot")]):
            raise TelegramError("managed_bot_event_invalid")
        return "managed_bot", {"owner_user_id": int(user["id"]), "owner_username": str(user.get("username") or "")[:64], "bot_user_id": int(bot["id"]), "bot_username": str(bot.get("username") or "")[:64], "bot_name": str(bot.get("first_name") or "Telegram bot")[:64]}
    message = payload.get("message") or {}
    sender, chat = message.get("from") or {}, message.get("chat") or {}
    text = str(message.get("text") or "")
    if text.startswith("/start link_") and isinstance(sender.get("id"), int) and chat.get("type") == "private":
        raw = text.split("link_", 1)[1].split()[0]
        return "identity_link", {"user_id": int(sender["id"]), "username": str(sender.get("username") or "")[:64], "link_hash": _hash(raw)}
    raise TelegramError("manager_update_unsupported")


def receive_manager_update(*, raw_body: bytes, secret_header: str) -> dict:
    if not _constant_header(secret_header, settings.TELEGRAM_MANAGER_WEBHOOK_SECRET):
        raise TelegramError("manager_webhook_secret_invalid", status_code=403)
    payload = _bounded_json(raw_body)
    update_type, normalized = _normalize_manager(payload)
    event, created = TelegramManagerEvent.objects.get_or_create(
        update_id=payload["update_id"],
        defaults={"update_type": update_type, "normalized_payload": normalized},
    )
    if created:
        from telegram.tasks import process_telegram_manager_event
        transaction.on_commit(lambda: process_telegram_manager_event.delay(str(event.id)))
    return {"accepted": int(created), "duplicates": int(not created)}


@transaction.atomic
def process_manager_event(event_id):
    event = TelegramManagerEvent.objects.select_for_update().get(pk=event_id)
    if event.status in {TelegramEventStatus.PROCESSED, TelegramEventStatus.IGNORED, TelegramEventStatus.DEAD_LETTER}:
        return event
    event.status = TelegramEventStatus.PROCESSING
    event.attempt_count += 1
    event.save(update_fields=["status", "attempt_count"])
    try:
        if event.update_type == "identity_link":
            data = event.normalized_payload
            link = TelegramUserLink.objects.select_for_update().filter(token_hash=data["link_hash"], status=TelegramUserLinkStatus.PENDING, expires_at__gt=timezone.now()).first()
            if not link:
                raise TelegramError("identity_link_invalid")
            takeover = TelegramUserLink.objects.filter(telegram_user_id=data["user_id"], status=TelegramUserLinkStatus.LINKED).exclude(user=link.user).exists()
            if takeover:
                raise TelegramError("telegram_identity_already_linked")
            TelegramUserLink.objects.filter(user=link.user, status=TelegramUserLinkStatus.LINKED).exclude(pk=link.pk).update(status=TelegramUserLinkStatus.REVOKED, revoked_at=timezone.now())
            link.telegram_user_id = data["user_id"]
            link.telegram_username = data["username"]
            link.status = TelegramUserLinkStatus.LINKED
            link.linked_at = timezone.now()
            link.save(update_fields=["telegram_user_id", "telegram_username", "status", "linked_at"])
        elif event.update_type == "managed_bot":
            _complete_managed_event(event)
        else:
            event.status = TelegramEventStatus.IGNORED
            event.processed_at = timezone.now()
            event.save(update_fields=["status", "processed_at"])
            return event
    except (TelegramError, TelegramProviderError, IntegrityError) as exc:
        event.status = TelegramEventStatus.FAILED if event.attempt_count < settings.TELEGRAM_MAX_EVENT_ATTEMPTS else TelegramEventStatus.DEAD_LETTER
        event.safe_error_code = getattr(exc, "code", "telegram_event_conflict")[:80]
        event.save(update_fields=["status", "safe_error_code"])
        return event
    event.status = TelegramEventStatus.PROCESSED
    event.safe_error_code = ""
    event.processed_at = timezone.now()
    event.save(update_fields=["status", "safe_error_code", "processed_at", "organization_id"])
    return event


def normalize_bot_username(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]", "", (value or "").strip())
    if not normalized.casefold().endswith("bot"):
        normalized += "Bot"
    if len(normalized) < 5:
        normalized = f"{normalized}Bot"
    if len(normalized) > 32 or not USERNAME_RE.fullmatch(normalized):
        raise TelegramError("bot_username_invalid")
    return normalized


def create_managed_request(*, organization, membership, user, suggested_name: str, suggested_username: str) -> dict:
    if membership.role not in {OrganizationMembershipRole.OWNER, OrganizationMembershipRole.ADMIN}:
        raise TelegramError("role_forbidden", status_code=403)
    if organization.status not in {OrganizationStatus.TRIAL, OrganizationStatus.ACTIVE}:
        raise TelegramError("organization_read_only", status_code=409)
    if TelegramBotConnection.objects.for_organization(organization).filter(status__in=[TelegramConnectionStatus.CONNECTED, TelegramConnectionStatus.DEGRADED, TelegramConnectionStatus.PAUSED]).exists():
        raise TelegramError("organization_bot_already_connected", status_code=409)
    link = TelegramUserLink.objects.filter(user=user, status=TelegramUserLinkStatus.LINKED).order_by("-linked_at").first()
    if not link or not link.telegram_user_id:
        raise TelegramError("telegram_identity_required", status_code=409)
    name = (suggested_name or "").strip()
    if not 1 <= len(name) <= 64:
        raise TelegramError("bot_name_invalid")
    username = normalize_bot_username(suggested_username)
    TelegramManagedBotRequest.objects.for_organization(organization).filter(status=TelegramManagedRequestStatus.AWAITING).update(status=TelegramManagedRequestStatus.CANCELLED)
    raw_nonce = secrets.token_urlsafe(18)
    request = TelegramManagedBotRequest.objects.create(organization=organization, requested_by=membership, linked_telegram_user_id=link.telegram_user_id, suggested_username=username, suggested_name=name, request_nonce_hash=_hash(raw_nonce), status=TelegramManagedRequestStatus.AWAITING, expires_at=timezone.now() + timedelta(minutes=settings.TELEGRAM_MANAGED_REQUEST_TTL_MINUTES))
    _audit(organization=organization, event_type="telegram.managed_request.created", actor=membership, managed_request=request, metadata={"suggested_username": username})
    manager = settings.TELEGRAM_MANAGER_BOT_USERNAME or "AlthairManagerBot"
    link_url = f"https://t.me/newbot/{manager}/{username}?{urllib.parse.urlencode({'name': name})}"
    return {"request": request, "creation_url": link_url}


def _new_connection(*, request, snapshot, owner_user_id, actor, connection_type, token):
    webhook_key = secrets.token_urlsafe(32).replace("-", "_")[:64]
    webhook_secret = secrets.token_urlsafe(32).replace("-", "_")
    channel = ChannelConnection(organization=request.organization, type=ChannelType.TELEGRAM, provider="telegram_bot_api", display_name=f"Telegram · @{snapshot.username}", external_identifier=str(snapshot.user_id), status=ChannelStatus.ACTIVE, configuration={"connection_type": connection_type, "token_version": 1})
    channel.set_credentials({"bot_token": token, "webhook_secret": webhook_secret})
    channel.set_webhook_secret(webhook_secret)
    channel.full_clean()
    channel.save()
    connection = TelegramBotConnection(organization=request.organization, channel_connection=channel, managed_request=request if connection_type == TelegramConnectionType.MANAGED else None, connection_type=connection_type, bot_user_id=snapshot.user_id, bot_username=snapshot.username, bot_name=snapshot.name, owner_telegram_user_id=owner_user_id, status=TelegramConnectionStatus.CONNECTED, webhook_public_key=webhook_key, webhook_status="pending", allowed_updates=ALLOWED_UPDATES, supported_languages=["ru", "uz", "en"], default_language=request.organization.default_language, connected_by=actor, connected_at=timezone.now())
    connection.full_clean()
    connection.save()
    try:
        telegram_provider().configure_bot(connection, token, webhook_secret)
        connection.webhook_status = "verified"
        connection.status = TelegramConnectionStatus.CONNECTED
        connection.save(update_fields=["webhook_status", "status", "updated_at"])
    except TelegramProviderError as exc:
        connection.status = TelegramConnectionStatus.DEGRADED
        connection.webhook_status = "error"
        connection.last_error_code = exc.code
        connection.save(update_fields=["status", "webhook_status", "last_error_code", "updated_at"])
    _audit(organization=request.organization, event_type="telegram.connection.created", actor=actor, connection=connection, managed_request=request if connection_type == TelegramConnectionType.MANAGED else None, metadata={"connection_type": connection_type, "bot_user_id": snapshot.user_id})
    return connection


def _complete_managed_event(event):
    data = event.normalized_payload
    request = TelegramManagedBotRequest.objects.select_for_update().filter(linked_telegram_user_id=data["owner_user_id"], suggested_username__iexact=data["bot_username"], status=TelegramManagedRequestStatus.AWAITING, expires_at__gt=timezone.now()).select_related("organization", "requested_by").first()
    if not request:
        raise TelegramError("managed_bot_request_mismatch")
    request.status = TelegramManagedRequestStatus.CREATED
    request.created_bot_user_id = data["bot_user_id"]
    request.created_bot_username = data["bot_username"]
    request.save(update_fields=["status", "created_bot_user_id", "created_bot_username", "updated_at"])
    snapshot = telegram_provider().get_managed_bot(data["bot_user_id"])
    if snapshot.user_id != data["bot_user_id"]:
        raise TelegramError("managed_bot_identity_mismatch")
    snapshot = type(snapshot)(user_id=snapshot.user_id, username=data["bot_username"], name=data["bot_name"], token=snapshot.token, can_manage_bots=False)
    request.status = TelegramManagedRequestStatus.TOKEN_RECEIVED
    request.save(update_fields=["status", "updated_at"])
    connection = _new_connection(request=request, snapshot=snapshot, owner_user_id=data["owner_user_id"], actor=request.requested_by, connection_type=TelegramConnectionType.MANAGED, token=snapshot.token)
    request.status = TelegramManagedRequestStatus.WEBHOOK_CONFIGURED if connection.webhook_status == "verified" else TelegramManagedRequestStatus.FAILED
    request.error_code = "" if connection.webhook_status == "verified" else connection.last_error_code
    request.save(update_fields=["status", "error_code", "updated_at"])
    event.organization_id = request.organization_id


def connect_existing_bot(*, organization, membership, token: str):
    if membership.role not in {OrganizationMembershipRole.OWNER, OrganizationMembershipRole.ADMIN}:
        raise TelegramError("role_forbidden", status_code=403)
    if not integration_readiness()["fake_provider"] and not settings.TELEGRAM_ENABLE_LIVE:
        raise TelegramError("telegram_provider_disabled", status_code=409)
    if organization.status not in {OrganizationStatus.TRIAL, OrganizationStatus.ACTIVE}:
        raise TelegramError("organization_read_only", status_code=409)
    if TelegramBotConnection.objects.for_organization(organization).exclude(
        status__in=[TelegramConnectionStatus.REVOKED, TelegramConnectionStatus.DISCONNECTED]
    ).exists():
        raise TelegramError("organization_bot_already_connected", status_code=409)
    snapshot = telegram_provider().validate_existing_bot((token or "").strip())
    request = TelegramManagedBotRequest.objects.create(organization=organization, requested_by=membership, linked_telegram_user_id=0, suggested_username=snapshot.username, suggested_name=snapshot.name, request_nonce_hash=_hash(secrets.token_urlsafe(24)), status=TelegramManagedRequestStatus.TOKEN_RECEIVED, expires_at=timezone.now() + timedelta(minutes=5), created_bot_user_id=snapshot.user_id, created_bot_username=snapshot.username)
    return _new_connection(request=request, snapshot=snapshot, owner_user_id=None, actor=membership, connection_type=TelegramConnectionType.EXISTING, token=snapshot.token)


def _normalize_bot_update(payload: dict) -> tuple[str, dict]:
    update_type = next((name for name in ALLOWED_UPDATES if name in payload), "")
    if not update_type:
        raise TelegramError("telegram_update_unsupported")
    item = payload[update_type]
    if update_type == "callback_query":
        message = item.get("message") or {}
        sender = item.get("from") or {}
        data = str(item.get("data") or "")[:128]
        return update_type, {"chat": message.get("chat") or {}, "from": sender, "message_id": int(message.get("message_id") or 0), "callback_id": str(item.get("id") or "")[:128], "callback_data": data}
    if update_type == "message_reaction":
        return update_type, {"chat": item.get("chat") or {}, "from": item.get("user") or {}, "message_id": int(item.get("message_id") or 0), "reaction": [str(value.get("emoji") or value.get("type") or "")[:32] for value in item.get("new_reaction") or [] if isinstance(value, dict)]}
    if update_type == "my_chat_member":
        return update_type, {"chat": item.get("chat") or {}, "from": item.get("from") or {}, "new_status": str((item.get("new_chat_member") or {}).get("status") or "")[:32]}
    message = item
    safe = {"chat": message.get("chat") or {}, "from": message.get("from") or {}, "message_id": int(message.get("message_id") or 0), "date": int(message.get("date") or 0), "text": str(message.get("text") or message.get("caption") or "")[: settings.TELEGRAM_MAX_TEXT_LENGTH], "reply_to_message_id": int((message.get("reply_to_message") or {}).get("message_id") or 0)}
    for media_type in ("photo", "document", "video", "voice", "audio", "sticker"):
        value = message.get(media_type)
        if value:
            entry = value[-1] if media_type == "photo" and isinstance(value, list) else value
            if isinstance(entry, dict):
                safe["media"] = {"type": media_type, "file_id": str(entry.get("file_id") or "")[:256], "file_size": int(entry.get("file_size") or 0), "mime_type": str(entry.get("mime_type") or "")[:120]}
            break
    if isinstance(message.get("contact"), dict):
        contact = message["contact"]
        safe["contact"] = {"user_id": int(contact.get("user_id") or 0), "phone_number": str(contact.get("phone_number") or "")[:40], "first_name": str(contact.get("first_name") or "")[:64]}
    if isinstance(message.get("location"), dict):
        safe["location"] = {"latitude": float(message["location"].get("latitude") or 0), "longitude": float(message["location"].get("longitude") or 0)}
    return update_type, safe


def receive_bot_update(*, public_key: str, raw_body: bytes, secret_header: str) -> dict:
    connection = TelegramBotConnection.objects.select_related("channel_connection", "organization").filter(webhook_public_key=public_key, status__in=[TelegramConnectionStatus.CONNECTED, TelegramConnectionStatus.DEGRADED]).first()
    if not connection:
        raise TelegramError("telegram_connection_not_found", status_code=404)
    if not connection.channel_connection.verify_webhook_secret(secret_header):
        raise TelegramError("telegram_webhook_secret_invalid", status_code=403)
    payload = _bounded_json(raw_body)
    update_type, normalized = _normalize_bot_update(payload)
    event, created = TelegramWebhookEvent.objects.get_or_create(
        connection=connection,
        update_id=payload["update_id"],
        defaults={
            "organization": connection.organization,
            "update_type": update_type,
            "normalized_payload": normalized,
        },
    )
    if created:
        from telegram.tasks import process_telegram_webhook
        transaction.on_commit(lambda: process_telegram_webhook.delay(str(event.id)))
    return {"accepted": int(created), "duplicates": int(not created)}


def _display_name(sender):
    return (" ".join(filter(None, [str(sender.get("first_name") or "")[:80], str(sender.get("last_name") or "")[:80]])).strip() or (f"@{str(sender.get('username'))[:64]}" if sender.get("username") else "Telegram customer"))


def _command_text(command: str, connection) -> str:
    language = connection.default_language
    company = connection.organization.name
    privacy = connection.privacy_url or "Privacy information is available from the company."
    messages = {
        "start": {"en": f"Welcome to {company}. AI may assist under company policy. Use /human for an employee. {privacy}", "ru": f"Добро пожаловать в {company}. Может использоваться AI. /human — позвать сотрудника. {privacy}", "uz": f"{company} xizmatiga xush kelibsiz. AI yordam berishi mumkin. Xodim uchun /human. {privacy}"},
        "help": {"en": "Commands: /start /help /human /language /privacy", "ru": "Команды: /start /help /human /language /privacy", "uz": "Buyruqlar: /start /help /human /language /privacy"},
        "language": {"en": "Languages: RU, UZ, EN", "ru": "Языки: RU, UZ, EN", "uz": "Tillar: RU, UZ, EN"},
        "privacy": {"en": privacy, "ru": privacy, "uz": privacy},
    }
    return messages.get(command, messages["help"]).get(language, messages["help"]["en"])


@transaction.atomic
def process_webhook_event(event_id):
    event = TelegramWebhookEvent.objects.select_for_update().select_related("connection__channel_connection", "organization").get(pk=event_id)
    if event.status in {TelegramEventStatus.PROCESSED, TelegramEventStatus.IGNORED, TelegramEventStatus.DEAD_LETTER}:
        return event
    event.status = TelegramEventStatus.PROCESSING
    event.attempt_count += 1
    event.save(update_fields=["status", "attempt_count"])
    data, connection = event.normalized_payload, event.connection
    try:
        chat = data.get("chat") or {}
        if chat.get("type") != "private" or not isinstance(chat.get("id"), int):
            event.status = TelegramEventStatus.IGNORED
            event.processed_at = timezone.now()
            event.save(update_fields=["status", "processed_at"])
            return event
        if event.update_type == "my_chat_member":
            if data.get("new_status") in {"kicked", "left"}:
                Conversation.objects.for_organization(event.organization).filter(channel_connection=connection.channel_connection, external_thread_id=str(chat["id"])).update(automation_state="ai_paused", handoff_reason="bot_blocked_by_user")
            event.status = TelegramEventStatus.PROCESSED
            event.processed_at = timezone.now()
            event.save(update_fields=["status", "processed_at"])
            return event
        sender = data.get("from") or {}
        if not isinstance(sender.get("id"), int) or sender.get("is_bot"):
            raise TelegramError("telegram_sender_invalid")
        provider_message_id = f"tg:{connection.bot_user_id}:{data.get('message_id') or event.update_id}:{event.update_type}"
        text = str(data.get("text") or "").strip()
        command = text.split()[0].split("@", 1)[0].lstrip("/").casefold() if text.startswith("/") else ""
        metadata = {"provider": "telegram", "telegram_username": str(sender.get("username") or "")[:64], "update_type": event.update_type, "reply_to_provider_message_id": str(data.get("reply_to_message_id") or "")}
        for key in ("media", "contact", "location", "reaction", "callback_data"):
            if key in data:
                metadata[key] = data[key]
        body = text or ({"callback_query": "Telegram button response", "message_reaction": "Telegram reaction"}.get(event.update_type) or f"Telegram {metadata.get('media', {}).get('type', 'message')}")
        if event.update_type == "edited_message":
            original = Message.objects.for_organization(event.organization).filter(
                channel_connection=connection.channel_connection,
                provider_message_id__startswith=f"tg:{connection.bot_user_id}:{data.get('message_id')}:",
            ).first()
            if original:
                original.body = body
                original.metadata = {**original.metadata, **metadata, "edited": True}
                original.save(update_fields=["body", "metadata", "updated_at"])
                message = original
                created = False
            else:
                message, created = ingest_inbound_message(organization=event.organization, channel_connection=connection.channel_connection, identity_type=ContactIdentityType.TELEGRAM, sender_value=str(sender["id"]), sender_display_name=_display_name(sender), external_thread_id=str(chat["id"]), provider_message_id=provider_message_id, body=body, occurred_at=datetime.fromtimestamp(data.get("date"), tz=dt_timezone.utc) if data.get("date") else timezone.now(), metadata={**metadata, "edited": True}, enqueue_ai=False)
        else:
            message, created = ingest_inbound_message(organization=event.organization, channel_connection=connection.channel_connection, identity_type=ContactIdentityType.TELEGRAM, sender_value=str(sender["id"]), sender_display_name=_display_name(sender), external_thread_id=str(chat["id"]), provider_message_id=provider_message_id, body=body, occurred_at=datetime.fromtimestamp(data.get("date"), tz=dt_timezone.utc) if data.get("date") else timezone.now(), metadata=metadata, enqueue_ai=event.update_type == "message" and not command)
        identity = ContactIdentity.objects.filter(contact=message.conversation.contact, channel_connection=connection.channel_connection, type=ContactIdentityType.TELEGRAM).first()
        if identity:
            identity.metadata = {**identity.metadata, "username": str(sender.get("username") or "")[:64], "first_name": str(sender.get("first_name") or "")[:80], "last_name": str(sender.get("last_name") or "")[:80]}
            identity.save(update_fields=["metadata", "updated_at"])
        if command == "human":
            from ai_runtime.models import HandoffRequestedBy
            from ai_runtime.services import create_handoff
            create_handoff(conversation=message.conversation, run=None, reason_code="customer_requested_human", safe_summary="Telegram customer requested an employee.", requested_by=HandoffRequestedBy.CUSTOMER)
        elif command in {"start", "help", "language", "privacy"}:
            send_telegram_message(conversation=message.conversation, body=_command_text(command, connection), client_message_id=f"command:{event.id}", sender_type=MessageSenderType.SYSTEM)
        connection.last_update_at = timezone.now()
        connection.save(update_fields=["last_update_at", "updated_at"])
        event.status = TelegramEventStatus.PROCESSED
        event.safe_error_code = ""
        event.processed_at = timezone.now()
        event.save(update_fields=["status", "safe_error_code", "processed_at"])
        return event
    except (TelegramError, TelegramProviderError, ValueError) as exc:
        event.status = TelegramEventStatus.FAILED if event.attempt_count < settings.TELEGRAM_MAX_EVENT_ATTEMPTS else TelegramEventStatus.DEAD_LETTER
        event.safe_error_code = getattr(exc, "code", "telegram_processing_failed")[:80]
        event.save(update_fields=["status", "safe_error_code"])
        return event


def connection_for_conversation(conversation):
    if conversation.channel_connection.type != ChannelType.TELEGRAM:
        return None
    return TelegramBotConnection.objects.for_organization(conversation.organization).select_related("channel_connection").filter(channel_connection=conversation.channel_connection).first()


def conversation_policy(conversation) -> dict:
    connection = connection_for_conversation(conversation)
    if not connection:
        return {"state": "provider_unavailable", "can_send": False}
    state = "can_reply"
    if connection.status == TelegramConnectionStatus.TOKEN_INVALID:
        state = "token_invalid"
    elif connection.status == TelegramConnectionStatus.WEBHOOK_ERROR or connection.webhook_status != "verified":
        state = "webhook_degraded"
    elif connection.status == TelegramConnectionStatus.PAUSED:
        state = "connection_paused"
    elif connection.status not in {TelegramConnectionStatus.CONNECTED, TelegramConnectionStatus.DEGRADED}:
        state = "provider_unavailable"
    elif conversation.handoff_reason == "bot_blocked_by_user":
        state = "bot_blocked"
    elif not conversation.last_inbound_at:
        state = "user_not_started"
    return {"state": state, "can_send": state == "can_reply", "bot_username": connection.bot_username, "bot_name": connection.bot_name, "connection_status": connection.status, "webhook_status": connection.webhook_status, "automation_mode": connection.automation_mode, "last_health_check_at": connection.last_health_check_at, "safe_chat_id": conversation.external_thread_id}


def can_send_telegram(conversation):
    return conversation_policy(conversation).get("can_send", False)


def _throttle(connection):
    key = f"telegram:org-send:{connection.organization_id}:{timezone.now().strftime('%Y%m%d%H%M')}"
    try:
        value = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=90)
        value = 1
    if value > settings.TELEGRAM_ORG_SENDS_PER_MINUTE:
        raise TelegramError("organization_send_rate_limited", status_code=429)


def send_telegram_message(*, conversation, body, client_message_id, membership=None, sender_type=MessageSenderType.AGENT, metadata=None):
    existing = Message.objects.for_organization(conversation.organization).filter(conversation=conversation, client_message_id=client_message_id).first()
    if existing:
        return existing, False
    connection = connection_for_conversation(conversation)
    if not connection:
        raise TelegramError("provider_unavailable", status_code=409)
    policy = conversation_policy(conversation)
    if not policy["can_send"]:
        raise TelegramError(policy["state"], status_code=409)
    text = (body or "").strip()
    if not text or len(text) > settings.TELEGRAM_MAX_TEXT_LENGTH:
        raise TelegramError("message_length_invalid")
    _throttle(connection)
    lock_key = f"telegram:send-lock:{connection.id}"
    if not cache.add(lock_key, "1", timeout=settings.TELEGRAM_SEND_LOCK_SECONDS):
        raise TelegramError("connection_send_busy", status_code=409)
    now = timezone.now()
    try:
        message = Message(organization=conversation.organization, conversation=conversation, channel_connection=conversation.channel_connection, direction=MessageDirection.OUTBOUND, sender_type=sender_type, sender_membership=membership, client_message_id=client_message_id, content_type=MessageContentType.TEXT, body=text, status=MessageStatus.QUEUED, metadata={"provider": "telegram", **(metadata or {})}, occurred_at=now)
        message.full_clean()
        message.save()
        attempt = TelegramOutboundAttempt.objects.create(organization=conversation.organization, connection=connection, message=message, status="sending", attempt_count=1)
        try:
            result = telegram_provider().send_text(connection=connection, chat_id=conversation.external_thread_id, text=text, reply_to_message_id=str((metadata or {}).get("reply_to_provider_message_id") or ""))
        except TelegramProviderError as exc:
            attempt.status = "queued" if exc.transient else "failed"
            attempt.safe_error_code = exc.code
            attempt.next_retry_at = timezone.now() + timedelta(seconds=30) if exc.transient else None
            attempt.save(update_fields=["status", "safe_error_code", "next_retry_at", "updated_at"])
            message.status = MessageStatus.QUEUED if exc.transient else MessageStatus.FAILED
            message.error_code = exc.code
            message.save(update_fields=["status", "error_code", "updated_at"])
            if exc.code == "bot_token_invalid":
                TelegramBotConnection.objects.filter(pk=connection.pk).update(status=TelegramConnectionStatus.TOKEN_INVALID, last_error_code=exc.code)
            if exc.code in {"bot_blocked_by_user", "chat_not_found"}:
                Conversation.objects.filter(pk=conversation.pk).update(handoff_reason="bot_blocked_by_user")
            if exc.transient:
                from telegram.tasks import retry_telegram_outbound
                retry_telegram_outbound.apply_async(args=[str(attempt.id)], countdown=30)
            return message, True
        message.provider_message_id = result.message_id
        message.status = MessageStatus.SENT
        message.save(update_fields=["provider_message_id", "status", "updated_at"])
        attempt.status = "sent"
        attempt.save(update_fields=["status", "updated_at"])
        connection.last_send_at = now
        connection.last_error_code = ""
        connection.save(update_fields=["last_send_at", "last_error_code", "updated_at"])
        Conversation.objects.filter(pk=conversation.pk).update(last_message_at=now, last_outbound_at=now)
        if sender_type == MessageSenderType.AGENT:
            Conversation.objects.filter(pk=conversation.pk).update(ai_state=ConversationAIState.PAUSED_BY_HUMAN, ai_state_updated_at=now)
            from ai_runtime.services import supersede_active_runs
            supersede_active_runs(conversation=conversation, reason="human_reply")
        record_activity(organization=conversation.organization, actor_membership=membership, event_type="message.sent", summary="Telegram reply sent", contact=conversation.contact, conversation=conversation, metadata={"provider": "telegram"})
        return message, True
    finally:
        cache.delete(lock_key)


def send_ai_message(*, run, body, client_message_id, metadata):
    return send_telegram_message(conversation=run.conversation, body=body, client_message_id=client_message_id, sender_type=MessageSenderType.AI, metadata=metadata)


def ai_state_for_connection(organization, channel_connection):
    if channel_connection.type != ChannelType.TELEGRAM:
        return None
    connection = TelegramBotConnection.objects.for_organization(organization).filter(channel_connection=channel_connection).first()
    if not connection or connection.status not in {TelegramConnectionStatus.CONNECTED, TelegramConnectionStatus.DEGRADED}:
        return ConversationAIState.OFF
    return {TelegramAutomationMode.MANUAL: ConversationAIState.OFF, TelegramAutomationMode.SUGGEST: ConversationAIState.SUGGEST, TelegramAutomationMode.AUTOPILOT: ConversationAIState.AUTOPILOT_TELEGRAM}[connection.automation_mode]


def telegram_autopilot_configured(conversation):
    connection = connection_for_conversation(conversation)
    return bool(connection and connection.automation_mode == TelegramAutomationMode.AUTOPILOT and can_send_telegram(conversation))


def telegram_autopilot_allowed(conversation):
    return bool(
        telegram_autopilot_configured(conversation)
        and conversation.ai_state == ConversationAIState.AUTOPILOT_TELEGRAM
    )


def rotate_token(*, connection, membership, replacement_token=""):
    if connection.connection_type == TelegramConnectionType.MANAGED:
        snapshot = telegram_provider().get_managed_bot(connection.bot_user_id, rotate=True)
    else:
        if not replacement_token:
            raise TelegramError("replacement_token_required")
        snapshot = telegram_provider().validate_existing_bot(replacement_token)
        if snapshot.user_id != connection.bot_user_id:
            raise TelegramError("replacement_bot_mismatch")
    webhook_secret = secrets.token_urlsafe(32).replace("-", "_")
    channel = connection.channel_connection
    channel.set_credentials({"bot_token": snapshot.token, "webhook_secret": webhook_secret})
    channel.set_webhook_secret(webhook_secret)
    channel.configuration = {**channel.configuration, "token_version": connection.token_version + 1}
    channel.save(update_fields=["encrypted_credentials", "webhook_secret_hash", "configuration", "updated_at"])
    connection.token_version += 1
    connection.webhook_status = "pending"
    connection.status = TelegramConnectionStatus.DEGRADED
    connection.save(update_fields=["token_version", "webhook_status", "status", "updated_at"])
    telegram_provider().configure_bot(connection, snapshot.token, webhook_secret)
    connection.webhook_status = "verified"
    connection.status = TelegramConnectionStatus.CONNECTED
    connection.last_error_code = ""
    connection.save(update_fields=["webhook_status", "status", "last_error_code", "updated_at"])
    _audit(organization=connection.organization, event_type="telegram.token.rotated", actor=membership, connection=connection, metadata={"token_version": connection.token_version})
    return connection


def update_access_settings(*, connection, membership, restricted, user_ids):
    if connection.connection_type != TelegramConnectionType.MANAGED:
        raise TelegramError("managed_bot_required", status_code=409)
    try:
        ids = sorted(set(int(value) for value in user_ids))
    except (TypeError, ValueError) as exc:
        raise TelegramError("telegram_user_ids_invalid") from exc
    if len(ids) > 10 or any(value <= 0 for value in ids):
        raise TelegramError("telegram_user_ids_invalid")
    result = telegram_provider().set_access_settings(connection, bool(restricted), ids)
    connection.access_restricted = bool(result["is_access_restricted"])
    connection.permitted_telegram_user_ids = list(result["added_user_ids"])
    connection.save(update_fields=["access_restricted", "permitted_telegram_user_ids", "updated_at"])
    _audit(organization=connection.organization, event_type="telegram.access.updated", actor=membership, connection=connection, metadata={"restricted": connection.access_restricted, "additional_users": len(ids)})
    return connection


def connection_health(connection, *, run_provider=False):
    state = {"status": connection.status, "webhook_status": connection.webhook_status, "has_encrypted_token": bool(connection.channel_connection.encrypted_credentials), "provider_reachable": None, "bot_matches": None, "webhook_matches": None, "pending_updates": None, "last_error_code": connection.last_error_code}
    if run_provider:
        try:
            result = telegram_provider().health(connection)
            state.update(result)
            connection.last_health_check_at = timezone.now()
            if result.get("bot_matches") and result.get("webhook_matches"):
                connection.status = TelegramConnectionStatus.CONNECTED
                connection.webhook_status = "verified"
                connection.last_error_code = ""
                connection.failure_count = 0
            else:
                connection.status = TelegramConnectionStatus.DEGRADED
                connection.last_error_code = "telegram_health_mismatch"
            connection.save(update_fields=["last_health_check_at", "status", "webhook_status", "last_error_code", "failure_count", "updated_at"])
        except TelegramProviderError as exc:
            connection.last_health_check_at = timezone.now()
            connection.failure_count += 1
            connection.last_error_code = exc.code
            connection.status = TelegramConnectionStatus.TOKEN_INVALID if exc.code == "bot_token_invalid" else TelegramConnectionStatus.DEGRADED
            connection.save(update_fields=["last_health_check_at", "failure_count", "last_error_code", "status", "updated_at"])
            state.update({"status": connection.status, "provider_reachable": False, "last_error_code": exc.code})
    return state


def set_connection_state(*, connection, membership, action):
    if action == "pause":
        connection.status = TelegramConnectionStatus.PAUSED
        connection.channel_connection.status = ChannelStatus.ERROR
    elif action == "reconnect":
        connection.status = TelegramConnectionStatus.CONNECTED
        connection.channel_connection.status = ChannelStatus.ACTIVE
    elif action == "disconnect":
        connection.status = TelegramConnectionStatus.DISCONNECTED
        connection.disconnected_at = timezone.now()
        connection.channel_connection.status = ChannelStatus.DISCONNECTED
        connection.channel_connection.encrypted_credentials = ""
    else:
        raise TelegramError("connection_action_invalid")
    connection.channel_connection.save(update_fields=["status", "encrypted_credentials", "updated_at"])
    connection.save(update_fields=["status", "disconnected_at", "updated_at"])
    _audit(organization=connection.organization, event_type=f"telegram.connection.{action}", actor=membership, connection=connection)
    return connection


def expire_pending_requests():
    now = timezone.now()
    request_count = TelegramManagedBotRequest.objects.filter(status=TelegramManagedRequestStatus.AWAITING, expires_at__lte=now).update(status=TelegramManagedRequestStatus.EXPIRED, error_code="request_expired")
    link_count = TelegramUserLink.objects.filter(status=TelegramUserLinkStatus.PENDING, expires_at__lte=now).update(status=TelegramUserLinkStatus.EXPIRED)
    return {"requests": request_count, "links": link_count}
