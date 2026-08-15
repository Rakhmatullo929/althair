from __future__ import annotations

from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from crm.models import MessageStatus
from telegram.models import TelegramBotConnection, TelegramConnectionStatus, TelegramOutboundAttempt
from telegram.providers import TelegramProviderError, telegram_provider
from control_plane.policies import operation_allowed
from telegram.services import connection_health, expire_pending_requests, process_manager_event, process_webhook_event


@shared_task
def process_telegram_manager_event(event_id):
    event = process_manager_event(event_id)
    if event.status == "failed":
        process_telegram_manager_event.apply_async(args=[str(event.id)], countdown=min(300, 15 * event.attempt_count))
    return str(event.id)


@shared_task
def process_telegram_webhook(event_id):
    event = process_webhook_event(event_id)
    if event.status == "failed":
        process_telegram_webhook.apply_async(args=[str(event.id)], countdown=min(300, 15 * event.attempt_count))
    return str(event.id)


@shared_task
def check_telegram_connections():
    checked = 0
    for connection in TelegramBotConnection.objects.filter(status__in=[TelegramConnectionStatus.CONNECTED, TelegramConnectionStatus.DEGRADED, TelegramConnectionStatus.TOKEN_INVALID])[: settings.TELEGRAM_HEALTH_BATCH_SIZE]:
        connection_health(connection, run_provider=True)
        checked += 1
    return {"checked": checked}


@shared_task
def expire_telegram_requests():
    return expire_pending_requests()


@shared_task
def retry_telegram_outbound(attempt_id):
    with transaction.atomic():
        attempt = TelegramOutboundAttempt.objects.select_for_update().select_related("connection__channel_connection", "message__conversation").get(pk=attempt_id)
        if attempt.status != "queued":
            return {"status": attempt.status}
        if attempt.attempt_count >= settings.TELEGRAM_MAX_SEND_ATTEMPTS:
            attempt.status = "dead_letter"
            attempt.message.status = MessageStatus.FAILED
            attempt.message.error_code = "provider_retry_exhausted"
            attempt.message.save(update_fields=["status", "error_code", "updated_at"])
            attempt.save(update_fields=["status", "updated_at"])
            return {"status": attempt.status}
        attempt.attempt_count += 1
        attempt.status = "sending"
        attempt.save(update_fields=["attempt_count", "status", "updated_at"])
        message = attempt.message
        if not operation_allowed(
            organization=attempt.organization,
            provider_type="telegram",
            channel_connection=attempt.connection.channel_connection,
        ):
            attempt.status = "failed"
            attempt.safe_error_code = "operational_control_active"
            attempt.next_retry_at = None
            attempt.save(update_fields=["status", "safe_error_code", "next_retry_at", "updated_at"])
            return {"status": attempt.status, "error_code": "operational_control_active"}
        try:
            result = telegram_provider().send_text(connection=attempt.connection, chat_id=message.conversation.external_thread_id, text=message.body)
        except TelegramProviderError as exc:
            attempt.safe_error_code = exc.code
            attempt.status = "queued" if exc.transient else "failed"
            attempt.next_retry_at = timezone.now() + timedelta(seconds=30 * attempt.attempt_count) if exc.transient else None
            attempt.save(update_fields=["safe_error_code", "status", "next_retry_at", "updated_at"])
            if exc.transient:
                retry_telegram_outbound.apply_async(args=[str(attempt.id)], countdown=min(300, 30 * attempt.attempt_count))
            return {"status": attempt.status, "error_code": exc.code}
        message.provider_message_id = result.message_id
        message.status = MessageStatus.SENT
        message.error_code = ""
        message.save(update_fields=["provider_message_id", "status", "error_code", "updated_at"])
        from billing.services import record_message_usage

        record_message_usage(message)
        attempt.status = "sent"
        attempt.safe_error_code = ""
        attempt.next_retry_at = None
        attempt.save(update_fields=["status", "safe_error_code", "next_retry_at", "updated_at"])
        return {"status": attempt.status}
