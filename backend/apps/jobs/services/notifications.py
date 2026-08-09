import logging

logger = logging.getLogger(__name__)


def _persist_event(job, event_type: str, note: str) -> None:
    """Write a JobEvent row inside a safe try/except so notification failures never break the caller."""
    try:
        from intake.models import JobEvent  # local import avoids circular import at module load
        JobEvent.objects.create(
            organization=job.organization,
            job=job,
            event_type=event_type,
            new_value=event_type,
            note=note,
            # created_by=None — system-generated; no request context available here
        )
    except Exception as exc:
        logger.error('[notify] failed to persist event type=%s job=%s: %s', event_type, str(job.id)[:8], exc)


def fire_new_job_notifications(job) -> None:
    """
    Persist a CREATED JobEvent and fire urgency alert when relevant.

    Call this from every intake service (SMS, email, voice) immediately after
    creating a JobRecord so that the new job is visible in Django Admin → Job
    Events regardless of the originating channel.

    create_manual_job() in jobs/services/jobs.py handles this itself via the
    lower-level _record_event() helper, so it should NOT also call this function.
    """
    try:
        from intake.models import JobEvent, JobEventType, JobPriority  # avoid circular import
        JobEvent.objects.create(
            organization=job.organization,
            job=job,
            event_type=JobEventType.CREATED,
            new_value='new',
            note=f'Job created from {job.source_channel} intake',
        )
    except Exception as exc:
        logger.error(
            '[notify] failed to persist CREATED event job=%s: %s',
            str(job.id)[:8], exc,
        )

    notify_new_job(job)

    try:
        from intake.models import JobPriority  # noqa: F811 — same import, safe re-use
        if getattr(job, 'priority', None) == JobPriority.URGENT:
            notify_urgent_job(job)
    except Exception as exc:
        logger.error('[notify] fire_new_job_notifications urgency check error: %s', exc)


def record_incoming_message(
        job,
        channel: str,
        body: str,
        sender: str = '',
        timestamp=None,
) -> None:
    """
    Persist a MESSAGE_RECEIVED JobEvent for an inbound customer message.

    Called from intake services (email, sms, voice) whenever the user sends
    something tied to a job — both at initial intake and on every follow-up
    message.  This feeds the Traceability / conversation timeline visible in
    Django Admin and in the frontend job-detail view.

    When `timestamp` is supplied, that value is written to JobEvent.created_at
    (bypassing auto_now_add).  This is used by the SMS back-fill path to
    preserve the actual time each message arrived, so the conversation
    timeline doesn't collapse into a single "everything at job-creation time"
    moment.

    Safe against all errors — intake must never break because of tracing.
    """
    if not job or not body:
        return
    try:
        from intake.models import JobEvent, JobEventType
        clean = str(body).strip()
        if len(clean) > 5000:
            clean = clean[:5000] + ' …[truncated]'
        note = clean
        if sender:
            note = f'[{sender}]\n{clean}'
        event = JobEvent.objects.create(
            organization=job.organization,
            job=job,
            event_type=JobEventType.MESSAGE_RECEIVED,
            new_value=(channel or '').lower() or 'message',
            note=note,
        )
        # JobEvent.created_at is auto_now_add=True, so we force-overwrite it
        # via a targeted UPDATE to honour the caller's original timestamp.
        if timestamp is not None:
            JobEvent.objects.filter(pk=event.pk).update(created_at=timestamp)
    except Exception as exc:
        logger.error(
            '[notify] failed to persist MESSAGE_RECEIVED event job=%s: %s',
            str(job.id)[:8], exc,
        )


def notify_new_job(job) -> None:
    """
    Log that a new job has been created.

    Persistence: for manual jobs, jobs/services/jobs.py already records a
    CREATED JobEvent before calling this hook.  For intake-channel jobs,
    fire_new_job_notifications() handles event persistence.
    """
    try:
        logger.info(
            '[notify] new_job id=%s channel=%s priority=%s service_type=%r',
            str(job.id)[:8], job.source_channel, job.priority, job.service_type,
        )
    except Exception as exc:
        logger.error('[notify] new_job hook error: %s', type(exc).__name__)


def notify_urgent_job(job) -> None:
    """
    Called when a job is set to URGENT priority (at creation or via priority update).

    Persistence: writes a JobEventType.URGENT_FLAGGED event so staff can filter
    all urgent alerts in Django Admin → Job Events.
    """
    try:
        logger.warning(
            '[notify] URGENT job id=%s service_type=%r builder=%r site=%r',
            str(job.id)[:8], job.service_type, job.builder, job.site,
        )
        _persist_event(
            job,
            event_type='urgent_flagged',
            note=f'Urgent priority alert — service_type={job.service_type!r} site={job.site!r}',
        )
    except Exception as exc:
        logger.error('[notify] urgent_job hook error: %s', type(exc).__name__)


def notify_review_required(job) -> None:
    """
    Called when a job is moved to PENDING_REVIEW status.

    Persistence: the service layer already records a JobEventType.STATUS_CHANGED
    event (new_value='pending_review') immediately before this hook, so no
    additional row is written here.
    """
    try:
        logger.info('[notify] review_required job id=%s', str(job.id)[:8])
    except Exception as exc:
        logger.error('[notify] review_required hook error: %s', type(exc).__name__)


def notify_job_scheduled(job) -> None:
    """
    Called when a job is scheduled or rescheduled.

    Persistence: the service layer already records a JobEventType.SCHEDULED /
    RESCHEDULED event immediately before this hook, so no additional row is written here.
    """
    try:
        logger.info(
            '[notify] scheduled job id=%s date=%s assigned_to=%r',
            str(job.id)[:8], job.scheduled_date, job.assigned_to,
        )
    except Exception as exc:
        logger.error('[notify] job_scheduled hook error: %s', type(exc).__name__)


def notify_job_reopened(job) -> None:
    """
    Called when a completed / cancelled job is reopened.

    Persistence: the service layer already records a JobEventType.REOPENED event
    immediately before this hook, so no additional row is written here.
    """
    try:
        logger.info(
            '[notify] reopened job id=%s reopen_count=%d',
            str(job.id)[:8], job.reopened_count,
        )
    except Exception as exc:
        logger.error('[notify] job_reopened hook error: %s', type(exc).__name__)


def notify_missing_po_rec(job) -> None:
    """
    Called when a job is marked as blocked due to missing PO or REC authorisation.

    Persistence: writes a JobEventType.MISSING_AUTH_FLAGGED event so staff can
    filter all missing-authorisation alerts in Django Admin → Job Events.
    """
    try:
        logger.warning(
            '[notify] missing_po_rec job id=%s po_status=%s rec_status=%s',
            str(job.id)[:8], job.po_status, job.rec_status,
        )
        parts = []
        if job.po_required:
            parts.append(f'po_status={job.po_status}')
        if job.rec_required:
            parts.append(f'rec_status={job.rec_status}')
        _persist_event(
            job,
            event_type='missing_auth_flagged',
            note='Missing authorisation alert — ' + ', '.join(parts),
        )
    except Exception as exc:
        logger.error('[notify] missing_po_rec hook error: %s', type(exc).__name__)
