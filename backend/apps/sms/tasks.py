from __future__ import annotations

from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from sms.models import SMSConnection, SMSConnectionStatus, SMSWebhookEnvelope, SMSWebhookProcessingStatus
from sms.services import (
    connection_health,
    process_inbound_envelope,
    process_status_envelope,
    retry_outbound_attempt,
)


@shared_task(bind=True, acks_late=True, reject_on_worker_lost=True, max_retries=3)
def process_sms_inbound(self, envelope_id):
    envelope = process_inbound_envelope(envelope_id)
    if envelope.processing_status == SMSWebhookProcessingStatus.FAILED and self.request.retries < self.max_retries:
        raise self.retry(countdown=min(2 ** self.request.retries, 30))
    return {"status": envelope.processing_status, "envelope_id": str(envelope.id)}


@shared_task(bind=True, acks_late=True, reject_on_worker_lost=True, max_retries=3)
def process_sms_status(self, envelope_id):
    envelope = process_status_envelope(envelope_id)
    if envelope.processing_status == SMSWebhookProcessingStatus.FAILED and self.request.retries < self.max_retries:
        raise self.retry(countdown=min(2 ** self.request.retries, 30))
    return {"status": envelope.processing_status, "envelope_id": str(envelope.id)}


@shared_task(bind=True, acks_late=True, reject_on_worker_lost=True, max_retries=2)
def retry_sms_outbound(self, attempt_id):
    result = retry_outbound_attempt(attempt_id)
    if result.get("retry") and self.request.retries < self.max_retries:
        raise self.retry(countdown=min(int(result.get("countdown") or 5), 60))
    return result


@shared_task
def retry_failed_sms_webhooks():
    rows = SMSWebhookEnvelope.objects.filter(
        processing_status=SMSWebhookProcessingStatus.FAILED,
        received_at__gte=timezone.now() - timedelta(hours=24),
    ).order_by("received_at")[:100]
    queued = 0
    for row in rows:
        task = process_sms_inbound if row.event_type == "inbound" else process_sms_status
        task.delay(str(row.id))
        queued += 1
    return {"queued": queued}


@shared_task
def check_sms_connections():
    checked = degraded = 0
    for connection in SMSConnection.objects.filter(
        status__in=[SMSConnectionStatus.CONNECTED, SMSConnectionStatus.DEGRADED]
    ).select_related("organization", "channel_connection")[:100]:
        health = connection_health(connection, run_provider=True)
        checked += 1
        degraded += int(health["status"] == SMSConnectionStatus.DEGRADED)
    return {"checked": checked, "degraded": degraded}
