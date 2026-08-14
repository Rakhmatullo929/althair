from __future__ import annotations

from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from gmail_integration.models import (
    GmailConnection,
    GmailConnectionStatus,
    GmailInitialSyncStatus,
    GmailNotificationStatus,
    GmailOutboundAttempt,
    GmailOutboundStatus,
    GmailSyncType,
)
from gmail_integration.providers import GmailProviderError, build_rfc_reply, gmail_provider
from control_plane.policies import operation_allowed
from gmail_integration.services import (
    bounded_full_sync,
    connection_health,
    incremental_sync,
    process_notification,
    renew_watch,
)
from organizations.models import OrganizationStatus


@shared_task
def initial_gmail_sync(connection_id):
    connection = GmailConnection.objects.select_related("channel_connection").get(pk=connection_id)
    if connection.initial_sync_status == GmailInitialSyncStatus.CANCELLED:
        return {"status": "cancelled", "imported": 0}
    connection.initial_sync_status = GmailInitialSyncStatus.RUNNING
    connection.connection_status = GmailConnectionStatus.SYNCING
    connection.save(
        update_fields=["initial_sync_status", "connection_status", "updated_at"]
    )
    run = bounded_full_sync(connection, sync_type=GmailSyncType.INITIAL)
    return {
        "run_id": str(run.id),
        "status": run.status,
        "imported": run.imported_count,
    }


@shared_task
def process_gmail_notification(notification_id):
    notification = process_notification(notification_id)
    if notification.status == GmailNotificationStatus.FAILED:
        process_gmail_notification.apply_async(
            args=[str(notification.id)], countdown=min(300, 15 * max(1, notification.attempt_count))
        )
    return str(notification.id)


@shared_task
def renew_gmail_watches():
    threshold = timezone.now() + timedelta(days=settings.GOOGLE_GMAIL_WATCH_RENEWAL_DAYS)
    connections = GmailConnection.objects.filter(
        connection_status__in=[
            GmailConnectionStatus.CONNECTED,
            GmailConnectionStatus.DEGRADED,
            GmailConnectionStatus.WATCH_EXPIRED,
        ],
        organization__status__in=[OrganizationStatus.TRIAL, OrganizationStatus.ACTIVE],
    ).filter(
        Q(watch_expiration_at__lte=threshold) | Q(watch_expiration_at__isnull=True)
    ).select_related("channel_connection")[: settings.GOOGLE_GMAIL_HEALTH_BATCH_SIZE]
    renewed = failed = 0
    for connection in connections:
        try:
            renew_watch(connection)
            renewed += 1
        except GmailProviderError as exc:
            connection.connection_status = GmailConnectionStatus.DEGRADED
            connection.last_error_code = exc.code
            connection.failure_count = min(connection.failure_count + 1, 100)
            connection.last_health_check_at = timezone.now()
            connection.save(update_fields=["connection_status", "last_error_code", "failure_count", "last_health_check_at", "updated_at"])
            failed += 1
    return {"renewed": renewed, "failed": failed}


@shared_task
def reconcile_gmail_history():
    checked = failed = skipped = 0
    for connection in GmailConnection.objects.filter(
        connection_status__in=[GmailConnectionStatus.CONNECTED, GmailConnectionStatus.DEGRADED],
        organization__status__in=[OrganizationStatus.TRIAL, OrganizationStatus.ACTIVE],
    ).select_related("channel_connection")[: settings.GOOGLE_GMAIL_HEALTH_BATCH_SIZE]:
        if (
            connection.failure_count
            >= settings.GOOGLE_GMAIL_CIRCUIT_BREAKER_FAILURES
            and connection.last_health_check_at
            and connection.last_health_check_at
            > timezone.now()
            - timedelta(minutes=settings.GOOGLE_GMAIL_CIRCUIT_BREAKER_MINUTES)
        ):
            skipped += 1
            continue
        try:
            incremental_sync(connection, sync_type=GmailSyncType.RECONCILIATION)
            checked += 1
        except (GmailProviderError, ValueError):
            failed += 1
    return {"checked": checked, "failed": failed, "circuit_open": skipped}


@shared_task
def check_gmail_connections():
    checked = 0
    stuck_before = timezone.now() - timedelta(hours=1)
    GmailConnection.objects.filter(
        initial_sync_status=GmailInitialSyncStatus.RUNNING,
        updated_at__lt=stuck_before,
    ).update(
        initial_sync_status=GmailInitialSyncStatus.FAILED,
        connection_status=GmailConnectionStatus.DEGRADED,
        last_error_code="initial_sync_stuck",
    )
    for connection in GmailConnection.objects.filter(
        connection_status__in=[
            GmailConnectionStatus.CONNECTED,
            GmailConnectionStatus.DEGRADED,
            GmailConnectionStatus.REAUTH_REQUIRED,
        ],
        organization__status__in=[OrganizationStatus.TRIAL, OrganizationStatus.ACTIVE],
    ).select_related("channel_connection")[: settings.GOOGLE_GMAIL_HEALTH_BATCH_SIZE]:
        connection_health(connection, run_provider=True)
        checked += 1
    return {"checked": checked}


@shared_task
def cleanup_gmail_operational_data():
    from gmail_integration.models import (
        GmailAuditEvent,
        GmailMessageRecord,
        GmailNotification,
        GmailOAuthState,
        GmailSyncRun,
    )

    cutoff = timezone.now() - timedelta(days=settings.GOOGLE_GMAIL_OPERATIONAL_RETENTION_DAYS)
    notifications, _ = GmailNotification.objects.filter(received_at__lt=cutoff).delete()
    sync_runs, _ = GmailSyncRun.objects.filter(started_at__lt=cutoff).delete()
    oauth_states, _ = GmailOAuthState.objects.filter(expires_at__lt=timezone.now()).delete()
    redacted = 0
    for connection in GmailConnection.objects.select_related("organization").filter(
        connection_status__in=[
            GmailConnectionStatus.SYNCING,
            GmailConnectionStatus.CONNECTED,
            GmailConnectionStatus.DEGRADED,
            GmailConnectionStatus.REAUTH_REQUIRED,
            GmailConnectionStatus.REVOKED,
            GmailConnectionStatus.PERMISSION_MISSING,
            GmailConnectionStatus.WATCH_EXPIRED,
            GmailConnectionStatus.DISCONNECTED,
        ]
    )[: settings.GOOGLE_GMAIL_HEALTH_BATCH_SIZE]:
        retention_cutoff = timezone.now() - timedelta(days=connection.retention_days)
        records = list(
            GmailMessageRecord.objects.filter(
                connection=connection,
                message__occurred_at__lt=retention_cutoff,
            )
            .filter(
                Q(message__metadata__privacy_redacted=False)
                | ~Q(message__metadata__has_key="privacy_redacted")
            )
            .select_related("message")[: settings.GOOGLE_GMAIL_RETENTION_BATCH_SIZE]
        )
        if not records:
            continue
        message_ids = [record.message_id for record in records]
        for record in records:
            record.rfc_message_id = ""
            record.in_reply_to = ""
            record.references = ""
            record.subject = ""
            record.reply_to = ""
            record.to_recipients = []
            record.cc_recipients = []
            record.participants = []
            record.snippet = ""
            record.attachment_metadata = []
        GmailMessageRecord.objects.bulk_update(
            records,
            [
                "rfc_message_id",
                "in_reply_to",
                "references",
                "subject",
                "reply_to",
                "to_recipients",
                "cc_recipients",
                "participants",
                "snippet",
                "attachment_metadata",
            ],
        )
        from crm.models import Message

        Message.objects.filter(pk__in=message_ids).update(
            body="[redacted by retention policy]",
            metadata={"provider": "gmail", "privacy_redacted": True},
        )
        redacted += len(records)
        GmailAuditEvent.objects.create(
            organization=connection.organization,
            connection=connection,
            event_type="gmail.retention_cleanup",
            metadata={"message_count": len(records)},
        )
    return {
        "notifications": notifications,
        "sync_runs": sync_runs,
        "oauth_states": oauth_states,
        "messages_redacted": redacted,
    }


@shared_task
def retry_gmail_outbound(attempt_id):
    attempt = GmailOutboundAttempt.objects.select_related(
        "connection__channel_connection", "message__conversation"
    ).get(pk=attempt_id)
    if attempt.status != GmailOutboundStatus.QUEUED:
        return {"status": attempt.status}
    if attempt.attempt_count >= settings.GOOGLE_GMAIL_MAX_SEND_ATTEMPTS:
        attempt.status = GmailOutboundStatus.DEAD_LETTER
        attempt.message.status = "failed"
        attempt.message.error_code = "provider_retry_exhausted"
        attempt.message.save(update_fields=["status", "error_code", "updated_at"])
        attempt.save(update_fields=["status", "updated_at"])
        return {"status": attempt.status}
    from gmail_integration.models import GmailMessageRecord

    source = GmailMessageRecord.objects.filter(
        connection=attempt.connection,
        message__conversation=attempt.message.conversation,
        message__direction="inbound",
    ).order_by("-message__occurred_at").first()
    recipient = next(
        (value for value in source.participants if value.casefold() != attempt.connection.mailbox_email_normalized),
        "",
    ) if source else ""
    if not source or not recipient:
        attempt.status = GmailOutboundStatus.FAILED
        attempt.safe_error_code = "thread_context_missing"
        attempt.save(update_fields=["status", "safe_error_code", "updated_at"])
        return {"status": attempt.status}
    attempt.attempt_count += 1
    raw = build_rfc_reply(
        mailbox_email=attempt.connection.mailbox_email_normalized,
        recipient=recipient,
        subject=source.subject,
        body=attempt.message.body,
        in_reply_to=source.rfc_message_id,
        references=source.references,
        origin_id=f"message:{attempt.message.id}",
        cc=tuple(attempt.message.metadata.get("cc") or []),
    )
    if not operation_allowed(
        organization=attempt.organization,
        provider_type="gmail",
        channel_connection=attempt.connection.channel_connection,
    ):
        attempt.status = GmailOutboundStatus.FAILED
        attempt.safe_error_code = "operational_control_active"
        attempt.next_retry_at = None
        attempt.save(update_fields=["attempt_count", "status", "safe_error_code", "next_retry_at", "updated_at"])
        return {"status": attempt.status}
    try:
        result = gmail_provider().send_reply(
            attempt.connection, thread_id=source.gmail_thread_id, raw_message=raw
        )
    except GmailProviderError as exc:
        attempt.status = GmailOutboundStatus.QUEUED if exc.transient else GmailOutboundStatus.FAILED
        attempt.safe_error_code = exc.code
        attempt.next_retry_at = timezone.now() + timedelta(seconds=30 * attempt.attempt_count) if exc.transient else None
        attempt.save(update_fields=["attempt_count", "status", "safe_error_code", "next_retry_at", "updated_at"])
        if exc.transient:
            retry_gmail_outbound.apply_async(args=[str(attempt.id)], countdown=min(300, 30 * attempt.attempt_count))
        return {"status": attempt.status}
    attempt.status = GmailOutboundStatus.SENT
    attempt.provider_request_id = result.request_id
    attempt.safe_error_code = ""
    attempt.message.provider_message_id = f"gmail:{result.message_id}"
    attempt.message.status = "sent"
    attempt.message.error_code = ""
    attempt.message.save(update_fields=["provider_message_id", "status", "error_code", "updated_at"])
    GmailMessageRecord.objects.get_or_create(
        organization=attempt.connection.organization,
        connection=attempt.connection,
        message=attempt.message,
        defaults={
            "gmail_message_id": result.message_id,
            "gmail_thread_id": result.thread_id,
            "subject": source.subject,
            "to_recipients": [recipient],
            "cc_recipients": list(attempt.message.metadata.get("cc") or []),
            "participants": [
                recipient,
                *(attempt.message.metadata.get("cc") or []),
            ],
            "label_ids": ["SENT"],
        },
    )
    now = timezone.now()
    attempt.connection.last_successful_send_at = now
    attempt.connection.last_error_code = ""
    attempt.connection.save(
        update_fields=["last_successful_send_at", "last_error_code", "updated_at"]
    )
    attempt.message.conversation.last_message_at = now
    attempt.message.conversation.last_outbound_at = now
    attempt.message.conversation.save(
        update_fields=["last_message_at", "last_outbound_at", "updated_at"]
    )
    attempt.save(update_fields=["attempt_count", "status", "provider_request_id", "safe_error_code", "updated_at"])
    return {"status": attempt.status}
