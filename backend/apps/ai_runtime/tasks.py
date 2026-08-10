from __future__ import annotations

from celery import shared_task
from django.conf import settings

from ai_runtime.services import (
    AIRuntimeConflict,
    AIRuntimeLimit,
    AIRuntimeUnavailable,
    process_run,
    queue_for_inbound_message,
)


@shared_task(
    bind=True,
    autoretry_for=(),
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
)
def evaluate_inbound_message(self, message_id):
    try:
        run, created = queue_for_inbound_message(message_id)
    except (AIRuntimeUnavailable, AIRuntimeConflict, AIRuntimeLimit):
        return {"status": "skipped"}
    if not created and run.status not in {"queued", "running"}:
        return {"status": run.status, "run_id": str(run.id)}
    config = run.organization.ai_runtime_config
    if config.inbound_debounce_seconds and not settings.CELERY_TASK_ALWAYS_EAGER:
        process_ai_run.apply_async(args=[str(run.id)], countdown=config.inbound_debounce_seconds)
        return {"status": "debounced", "run_id": str(run.id)}
    process_ai_run.delay(str(run.id))
    return {"status": "queued", "run_id": str(run.id)}


@shared_task(bind=True, acks_late=True, reject_on_worker_lost=True, max_retries=3)
def process_ai_run(self, run_id):
    try:
        run = process_run(run_id)
        return {"status": run.status, "run_id": str(run.id)}
    except Exception as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=min(2 ** self.request.retries, 30))
        raise
