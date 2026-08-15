from __future__ import annotations

from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from crm.models import MessageStatus
from instagram.models import (
    InstagramConnection,
    InstagramConnectionStatus,
    InstagramOutboundAttempt,
    InstagramOutboundStatus,
)
from instagram.providers import InstagramProviderError, instagram_provider
from control_plane.policies import operation_allowed
from instagram.services import connection_health, process_webhook_event


@shared_task(bind=True, autoretry_for=(), max_retries=0)
def process_instagram_webhook(self, event_id):
    event = process_webhook_event(event_id)
    if event.status == "failed":
        process_instagram_webhook.apply_async(
            args=[str(event.id)],
            countdown=min(300, 15 * max(1, event.attempt_count)),
        )
    return str(event.id)


@shared_task
def check_instagram_connections():
    checked = 0
    for connection in InstagramConnection.objects.filter(
        connection_status__in=[
            InstagramConnectionStatus.CONNECTED,
            InstagramConnectionStatus.DEGRADED,
            InstagramConnectionStatus.EXPIRED,
        ]
    ).select_related("channel_connection")[: settings.META_INSTAGRAM_HEALTH_BATCH_SIZE]:
        connection_health(connection, run_provider=True)
        checked += 1
    return {"checked": checked}


@shared_task
def warn_instagram_token_expiry():
    threshold = timezone.now() + timedelta(days=settings.META_INSTAGRAM_TOKEN_WARNING_DAYS)
    connection_ids = list(InstagramConnection.objects.filter(
        connection_status=InstagramConnectionStatus.CONNECTED,
        token_expires_at__isnull=False,
        token_expires_at__lte=threshold,
    ).values_list("id", flat=True)[: settings.META_INSTAGRAM_HEALTH_BATCH_SIZE])
    count = InstagramConnection.objects.filter(id__in=connection_ids).update(
        last_error_code="token_expiring"
    )
    return {"warnings": count}


@shared_task
def verify_instagram_subscriptions():
    rows = InstagramConnection.objects.filter(
        connection_status=InstagramConnectionStatus.CONNECTED
    )[: settings.META_INSTAGRAM_HEALTH_BATCH_SIZE]
    return {
        "checked": len(rows),
        "pending": sum(item.webhook_subscription_status != "verified" for item in rows),
    }


@shared_task
def bounded_instagram_backfill(connection_id, limit=50):
    limit = max(1, min(int(limit), settings.META_INSTAGRAM_BACKFILL_MAX_ITEMS))
    connection = InstagramConnection.objects.get(pk=connection_id)
    connection.channel_connection.last_synced_at = timezone.now()
    connection.channel_connection.configuration = {
        **connection.channel_connection.configuration,
        "last_backfill_limit": limit,
        "backfill_scope": "bounded_recent_only",
    }
    connection.channel_connection.save(
        update_fields=["last_synced_at", "configuration", "updated_at"]
    )
    return {"connection_id": str(connection.id), "requested_limit": limit, "imported": 0}


@shared_task
def retry_instagram_outbound(attempt_id):
    with transaction.atomic():
        attempt = (
            InstagramOutboundAttempt.objects.select_for_update()
            .select_related("connection__channel_connection", "message__conversation")
            .get(pk=attempt_id)
        )
        if attempt.status != InstagramOutboundStatus.QUEUED:
            return {"status": attempt.status}
        if attempt.attempt_count >= settings.META_INSTAGRAM_MAX_SEND_ATTEMPTS:
            attempt.status = InstagramOutboundStatus.DEAD_LETTER
            attempt.message.status = MessageStatus.FAILED
            attempt.message.error_code = "provider_retry_exhausted"
            attempt.message.save(update_fields=["status", "error_code", "updated_at"])
            attempt.save(update_fields=["status", "updated_at"])
            return {"status": attempt.status}
        attempt.attempt_count += 1
        attempt.status = InstagramOutboundStatus.SENDING
        attempt.save(update_fields=["attempt_count", "status", "updated_at"])
        message = attempt.message
        if not operation_allowed(
            organization=attempt.organization,
            provider_type="instagram",
            channel_connection=attempt.connection.channel_connection,
        ):
            attempt.status = InstagramOutboundStatus.FAILED
            attempt.safe_error_code = "operational_control_active"
            attempt.next_retry_at = None
            attempt.save(update_fields=["status", "safe_error_code", "next_retry_at", "updated_at"])
            return {"status": attempt.status, "error_code": "operational_control_active"}
        try:
            result = instagram_provider().send_text(
                connection=attempt.connection,
                recipient_id=message.conversation.external_thread_id,
                text=message.body,
                human_agent=bool(message.metadata.get("human_agent")),
            )
        except InstagramProviderError as exc:
            attempt.safe_error_code = exc.code
            attempt.status = (
                InstagramOutboundStatus.QUEUED
                if exc.transient
                else InstagramOutboundStatus.FAILED
            )
            attempt.next_retry_at = (
                timezone.now() + timedelta(seconds=30 * attempt.attempt_count)
                if exc.transient
                else None
            )
            attempt.save(
                update_fields=["safe_error_code", "status", "next_retry_at", "updated_at"]
            )
            if exc.transient:
                retry_instagram_outbound.apply_async(
                    args=[str(attempt.id)],
                    countdown=min(300, 30 * attempt.attempt_count),
                )
            return {"status": attempt.status, "error_code": exc.code}
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
        return {"status": attempt.status}
