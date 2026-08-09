import logging
from typing import Any, Optional

from django.db import transaction
from django.db.models import Case, Count, IntegerField, Q, QuerySet, Value, When
from django.utils import timezone

from intake.models import (
    ChannelChoice,
    Contact,
    ContactSource,
    JobEvent,
    JobEventType,
    JobNote,
    JobPriority,
    JobRecord,
    JobStatus,
    PoRecStatus,
)
from jobs.services.notifications import (
    notify_job_reopened,
    notify_job_scheduled,
    notify_missing_po_rec,
    notify_new_job,
    notify_review_required,
    notify_urgent_job,
)

logger = logging.getLogger(__name__)

HOTLIST_STATUSES = [
    JobStatus.NEW,
    JobStatus.PENDING_REVIEW,
    JobStatus.PRIORITIZED,
    JobStatus.SCHEDULED,
    JobStatus.IN_PROGRESS,
    JobStatus.REOPENED,
    JobStatus.BLOCKED,
    JobStatus.PENDING_FOLLOW_UP,
]

ALLOWED_ORDERING_FIELDS = {
    'created_at', '-created_at',
    'updated_at', '-updated_at',
    'scheduled_date', '-scheduled_date',
    'service_type', '-service_type',
    'builder', '-builder',
    'status', '-status',
    'priority', '-priority',
}

_PRIORITY_ORDER = Case(
    When(priority=JobPriority.URGENT, then=Value(0)),
    When(priority=JobPriority.HIGH, then=Value(1)),
    When(priority=JobPriority.NORMAL, then=Value(2)),
    When(priority=JobPriority.LOW, then=Value(3)),
    default=Value(4),
    output_field=IntegerField(),
)


def _record_event(
        job: JobRecord,
        event_type: str,
        old_value: str = '',
        new_value: str = '',
        note: str = '',
        user=None,
) -> JobEvent:
    created_by = user if (user and getattr(user, 'is_authenticated', False)) else None
    return JobEvent.objects.create(
        organization=job.organization,
        job=job,
        event_type=event_type,
        old_value=str(old_value)[:500],
        new_value=str(new_value)[:500],
        note=str(note)[:1000],
        created_by=created_by,
    )


def _apply_filters(qs: QuerySet, params: dict) -> QuerySet:
    status = params.get('status')
    if status:
        statuses = [s.strip() for s in status.split(',') if s.strip()]
        qs = qs.filter(status__in=statuses)

    priority = params.get('priority')
    if priority:
        priorities = [p.strip() for p in priority.split(',') if p.strip()]
        qs = qs.filter(priority__in=priorities)

    source_channel = params.get('source_channel')
    if source_channel:
        qs = qs.filter(source_channel=source_channel)

    for field in ['service_type', 'builder', 'project', 'site', 'community', 'division']:
        value = params.get(field)
        if value:
            qs = qs.filter(**{f'{field}__icontains': value})

    created_after = params.get('created_after')
    if created_after:
        qs = qs.filter(created_at__date__gte=created_after)

    created_before = params.get('created_before')
    if created_before:
        qs = qs.filter(created_at__date__lte=created_before)

    scheduled_after = params.get('scheduled_after')
    if scheduled_after:
        qs = qs.filter(scheduled_date__gte=scheduled_after)

    scheduled_before = params.get('scheduled_before')
    if scheduled_before:
        qs = qs.filter(scheduled_date__lte=scheduled_before)

    po_required = params.get('po_required')
    if po_required is not None and po_required != '':
        qs = qs.filter(po_required=str(po_required).lower() in ('true', '1', 'yes'))

    rec_required = params.get('rec_required')
    if rec_required is not None and rec_required != '':
        qs = qs.filter(rec_required=str(rec_required).lower() in ('true', '1', 'yes'))

    po_status = params.get('po_status')
    if po_status:
        qs = qs.filter(po_status=po_status)

    rec_status = params.get('rec_status')
    if rec_status:
        qs = qs.filter(rec_status=rec_status)

    has_scheduled = params.get('has_scheduled')
    if has_scheduled is not None and has_scheduled != '':
        if str(has_scheduled).lower() in ('true', '1', 'yes'):
            qs = qs.filter(scheduled_date__isnull=False)
        else:
            qs = qs.filter(scheduled_date__isnull=True)

    q = params.get('q') or params.get('search')
    if q:
        qs = qs.filter(
            Q(contact__name__icontains=q)
            | Q(contact__phone__icontains=q)
            | Q(contact__email__icontains=q)
            | Q(builder__icontains=q)
            | Q(project__icontains=q)
            | Q(site__icontains=q)
            | Q(community__icontains=q)
            | Q(service_type__icontains=q)
            | Q(division__icontains=q)
            | Q(assigned_to__icontains=q)
        ).distinct()
        qs = qs.select_related('contact')

    return qs


def _apply_ordering(qs: QuerySet, params: dict, default: str = '-created_at') -> QuerySet:
    ordering = params.get('ordering', default)
    if ordering in ALLOWED_ORDERING_FIELDS:
        return qs.order_by(ordering)
    return qs.order_by(default)


def _apply_user_scope(qs: QuerySet, user, membership_role=None) -> QuerySet:
    """Agents see assigned work; broader customer roles may see the tenant board."""
    if membership_role == 'agent':
        return qs.filter(assigned_user=user)
    return qs


def get_hotlist_queryset(params: dict, *, organization, user=None, membership_role=None) -> QuerySet:
    qs = (
        JobRecord.objects
        .filter(organization=organization, status__in=HOTLIST_STATUSES)
        .select_related('contact', 'source_interaction', 'assigned_user')
    )
    qs = _apply_user_scope(qs, user, membership_role)
    qs = _apply_filters(qs, params)

    ordering = params.get('ordering')
    if ordering and ordering in ALLOWED_ORDERING_FIELDS:
        return qs.order_by(ordering)

    return qs.annotate(priority_order=_PRIORITY_ORDER).order_by('priority_order', '-created_at')


def get_job_list_queryset(params: dict, *, organization, user=None, membership_role=None) -> QuerySet:
    qs = JobRecord.objects.filter(organization=organization).select_related(
        'contact', 'source_interaction', 'assigned_user',
    )
    qs = _apply_user_scope(qs, user, membership_role)
    qs = _apply_filters(qs, params)
    return _apply_ordering(qs, params)


def get_dashboard_summary(*, organization, user=None, membership_role=None) -> dict:
    base_qs = JobRecord.objects.filter(organization=organization)
    base_qs = _apply_user_scope(base_qs, user, membership_role)
    active_qs = base_qs.filter(status__in=HOTLIST_STATUSES)
    all_qs = base_qs

    by_status_qs = all_qs.values('status').annotate(count=Count('id'))
    by_status = {row['status']: row['count'] for row in by_status_qs}

    by_priority_qs = active_qs.values('priority').annotate(count=Count('id'))
    by_priority = {row['priority']: row['count'] for row in by_priority_qs}

    urgent_count = active_qs.filter(priority=JobPriority.URGENT).count()
    pending_review_count = all_qs.filter(status=JobStatus.PENDING_REVIEW).count()
    scheduled_count = all_qs.filter(status=JobStatus.SCHEDULED).count()

    missing_po = all_qs.filter(
        po_required=True
    ).exclude(po_status=PoRecStatus.RECEIVED).count()

    missing_rec = all_qs.filter(
        rec_required=True
    ).exclude(rec_status=PoRecStatus.RECEIVED).count()

    recent_jobs_qs = (
        all_qs.select_related('contact')
        .order_by('-created_at')[:5]
    )

    from jobs.serializers.jobs import JobListSerializer
    recent_jobs_data = JobListSerializer(recent_jobs_qs, many=True).data

    return {
        'total_active': active_qs.count(),
        'total_all': all_qs.count(),
        'by_status': by_status,
        'by_priority': by_priority,
        'urgent_count': urgent_count,
        'pending_review_count': pending_review_count,
        'scheduled_count': scheduled_count,
        'missing_po': missing_po,
        'missing_rec': missing_rec,
        'recent_jobs': recent_jobs_data,
    }


@transaction.atomic
def create_manual_job(data: dict, *, organization, user=None) -> JobRecord:
    contact: Optional[Contact] = None

    contact_id = data.get('contact_id')
    if contact_id:
        try:
            contact = Contact.objects.get(pk=contact_id, organization=organization)
        except Contact.DoesNotExist:
            logger.warning('create_manual_job: contact_id=%s not found, skipping', contact_id)

    if not contact:
        phone = (data.get('contact_phone') or '').strip()
        email = (data.get('contact_email') or '').lower().strip()
        name = (data.get('contact_name') or '').strip()
        if phone or email:
            lookup = {}
            if phone:
                lookup['phone'] = phone
            elif email:
                lookup['email'] = email
            contact, created = Contact.objects.get_or_create(
                organization=organization,
                **lookup,
                defaults={
                    'name': name,
                    'email': email,
                    'phone': phone,
                    'source': ContactSource.MANUAL,
                },
            )
            if created:
                logger.info('create_manual_job: new contact id=%s', str(contact.id)[:8])

    priority = data.get('priority') or JobPriority.NORMAL

    job = JobRecord.objects.create(
        organization=organization,
        source_channel=ChannelChoice.MANUAL,
        contact=contact,
        source_interaction=None,
        service_type=(data.get('service_type') or '').strip(),
        install_type=(data.get('install_type') or '').strip(),
        builder=(data.get('builder') or '').strip(),
        project=(data.get('project') or '').strip(),
        site=(data.get('site') or '').strip(),
        community=(data.get('community') or '').strip(),
        scope=(data.get('scope') or '').strip(),
        quantity=(data.get('quantity') or '').strip(),
        urgency_notes=(data.get('urgency_notes') or '').strip(),
        division=(data.get('division') or '').strip(),
        po_rec_note=(data.get('po_rec_note') or '').strip(),
        summary=(data.get('summary') or '').strip(),
        due_date=data.get('due_date'),
        priority=priority,
        po_required=bool(data.get('po_required', False)),
        rec_required=bool(data.get('rec_required', False)),
        status=JobStatus.NEW,
    )

    _record_event(job, JobEventType.CREATED, new_value='new', user=user)
    notify_new_job(job)

    if priority == JobPriority.URGENT:
        notify_urgent_job(job)

    first_note = (data.get('notes_text') or '').strip()
    if first_note:
        add_job_note(job, first_note, is_follow_up=False, user=user)

    logger.info(
        'manual_job created id=%s service_type=%r contact=%s',
        str(job.id)[:8], job.service_type, str(contact.id)[:8] if contact else 'none',
    )
    return job


@transaction.atomic
def update_job_status(job: JobRecord, new_status: str, user=None, note: str = '') -> JobRecord:
    old_status = job.status
    if old_status == new_status:
        return job

    job.status = new_status
    job.save(update_fields=['status', 'updated_at'])

    _record_event(
        job, JobEventType.STATUS_CHANGED,
        old_value=old_status, new_value=new_status, note=note, user=user,
    )

    if new_status == JobStatus.PENDING_REVIEW:
        notify_review_required(job)

    logger.info(
        'job status updated id=%s %s → %s',
        str(job.id)[:8], old_status, new_status,
    )
    return job


@transaction.atomic
def update_job_priority(job: JobRecord, new_priority: str, user=None) -> JobRecord:
    old_priority = job.priority
    if old_priority == new_priority:
        return job

    job.priority = new_priority
    job.save(update_fields=['priority', 'updated_at'])

    _record_event(
        job, JobEventType.PRIORITY_CHANGED,
        old_value=old_priority, new_value=new_priority, user=user,
    )

    if new_priority == JobPriority.URGENT:
        notify_urgent_job(job)

    logger.info(
        'job priority updated id=%s %s → %s',
        str(job.id)[:8], old_priority, new_priority,
    )
    return job


@transaction.atomic
def schedule_job(
        job: JobRecord,
        scheduled_date,
        scheduled_time=None,
        assigned_to: str = '',
        assignment_notes: str = '',
        user=None,
) -> JobRecord:
    was_scheduled = job.scheduled_date is not None
    old_date = str(job.scheduled_date) if was_scheduled else ''

    job.scheduled_date = scheduled_date
    job.scheduled_time = scheduled_time
    if assigned_to:
        job.assigned_to = assigned_to
    if assignment_notes:
        job.assignment_notes = assignment_notes

    terminal_statuses = {JobStatus.IN_PROGRESS, JobStatus.COMPLETED, JobStatus.CANCELLED}
    if job.status not in terminal_statuses:
        job.status = JobStatus.SCHEDULED

    job.save(update_fields=[
        'scheduled_date', 'scheduled_time', 'assigned_to',
        'assignment_notes', 'status', 'updated_at',
    ])

    event_type = JobEventType.RESCHEDULED if was_scheduled else JobEventType.SCHEDULED
    note_parts = []
    if assigned_to:
        note_parts.append(f'assigned_to={assigned_to}')
    if scheduled_time:
        note_parts.append(f'time={scheduled_time}')
    _record_event(
        job, event_type,
        old_value=old_date, new_value=str(scheduled_date),
        note=', '.join(note_parts), user=user,
    )

    notify_job_scheduled(job)
    logger.info(
        'job scheduled id=%s date=%s assigned_to=%r',
        str(job.id)[:8], scheduled_date, assigned_to,
    )
    return job


@transaction.atomic
def unschedule_job(job: JobRecord, user=None, note: str = '') -> JobRecord:
    if not job.scheduled_date:
        return job

    old_date = str(job.scheduled_date)
    job.scheduled_date = None
    job.scheduled_time = None

    if job.status == JobStatus.SCHEDULED:
        job.status = JobStatus.PENDING_REVIEW

    job.save(update_fields=['scheduled_date', 'scheduled_time', 'status', 'updated_at'])

    _record_event(
        job, JobEventType.UNSCHEDULED,
        old_value=old_date, new_value='', note=note, user=user,
    )
    logger.info('job unscheduled id=%s', str(job.id)[:8])
    return job


@transaction.atomic
def mark_ready_to_schedule(job: JobRecord, user=None) -> JobRecord:
    """Triage a hotlist job into the 'ready to schedule' state.

    Sets the job to PRIORITIZED, clears any schedule, and removes any crew /
    user assignment — so it drops off the hotlist and lands in the field
    board's 'Ready to schedule' column and the route calendar's unscheduled /
    unassigned staging area, ready to be dragged onto a day and crew.
    """
    old_status = job.status
    job.status = JobStatus.PRIORITIZED
    job.scheduled_date = None
    job.scheduled_time = None
    job.assigned_user = None
    job.assigned_to = ''
    job.save(update_fields=[
        'status', 'scheduled_date', 'scheduled_time',
        'assigned_user', 'assigned_to', 'updated_at',
    ])
    _record_event(
        job, JobEventType.STATUS_CHANGED,
        old_value=old_status, new_value=JobStatus.PRIORITIZED,
        note='Marked ready to schedule (unscheduled, unassigned).', user=user,
    )
    logger.info('job marked ready to schedule id=%s', str(job.id)[:8])
    return job


@transaction.atomic
def reopen_job(job: JobRecord, user=None, reason: str = '') -> JobRecord:
    old_status = job.status
    job.status = JobStatus.REOPENED
    job.reopened_at = timezone.now()
    job.reopened_count = (job.reopened_count or 0) + 1
    job.save(update_fields=['status', 'reopened_at', 'reopened_count', 'updated_at'])

    _record_event(
        job, JobEventType.REOPENED,
        old_value=old_status, new_value=JobStatus.REOPENED, note=reason, user=user,
    )
    notify_job_reopened(job)
    logger.info(
        'job reopened id=%s count=%d reason=%r',
        str(job.id)[:8], job.reopened_count, reason,
    )
    return job


@transaction.atomic
def update_job_po_rec(job: JobRecord, data: dict, user=None) -> JobRecord:
    update_fields = ['updated_at']
    changes: list[str] = []

    for field in ('po_required', 'rec_required'):
        if field in data:
            old = getattr(job, field)
            new = bool(data[field])
            if old != new:
                setattr(job, field, new)
                update_fields.append(field)
                changes.append(f'{field}: {old}→{new}')

    for field in ('po_status', 'rec_status'):
        if field in data and data[field]:
            old = getattr(job, field)
            new = data[field]
            if old != new:
                setattr(job, field, new)
                update_fields.append(field)
                changes.append(f'{field}: {old}→{new}')

    if 'po_rec_note' in data:
        job.po_rec_note = (data['po_rec_note'] or '').strip()
        update_fields.append('po_rec_note')

    if len(update_fields) > 1:
        job.save(update_fields=update_fields)
        _record_event(
            job, JobEventType.PO_REC_UPDATED,
            new_value='; '.join(changes), user=user,
        )

    if (
            (job.po_required and job.po_status == PoRecStatus.MISSING)
            or (job.rec_required and job.rec_status == PoRecStatus.MISSING)
    ):
        notify_missing_po_rec(job)

    return job


PATCHABLE_CONTENT_FIELDS = {
    'service_type', 'install_type', 'builder', 'project', 'site', 'community',
    'scope', 'quantity', 'urgency_notes', 'division', 'po_rec_note',
    'summary', 'ai_summary', 'completion_notes',
    'assigned_to', 'assignment_notes', 'due_date',
    'is_recurring',
}


@transaction.atomic
def update_job_fields(job: JobRecord, data: dict, user=None) -> JobRecord:
    update_fields = ['updated_at']
    changed: list[str] = []

    for field in PATCHABLE_CONTENT_FIELDS:
        if field in data:
            new_val = data[field]
            if getattr(job, field) != new_val:
                setattr(job, field, new_val)
                update_fields.append(field)
                changed.append(field)

    if len(update_fields) > 1:
        job.save(update_fields=update_fields)
        _record_event(
            job, JobEventType.FIELD_UPDATED,
            new_value=', '.join(changed), user=user,
        )
        logger.info(
            'job fields updated id=%s fields=%s',
            str(job.id)[:8], changed,
        )

    # Update linked Contact fields if provided
    if job.contact and any(k in data for k in ('contact_phone', 'contact_email', 'contact_preferred_callback')):
        contact = job.contact
        contact_save_fields = []
        if 'contact_phone' in data and contact.phone != data['contact_phone']:
            contact.phone = data['contact_phone']
            contact_save_fields.append('phone')
        if 'contact_email' in data and contact.email != data['contact_email']:
            contact.email = data['contact_email']
            contact_save_fields.append('email')
        if 'contact_preferred_callback' in data:
            meta = dict(contact.metadata or {})
            new_pref = data['contact_preferred_callback']
            if meta.get('preferred_callback') != new_pref:
                meta['preferred_callback'] = new_pref
                contact.metadata = meta
                contact_save_fields.append('metadata')
        if contact_save_fields:
            contact.save(update_fields=contact_save_fields)

    return job


@transaction.atomic
def set_job_seen(job: JobRecord, is_seen: bool, user=None) -> JobRecord:
    """Toggle the 'seen / in the works' acknowledgement on a job."""
    is_seen = bool(is_seen)
    if job.is_seen == is_seen:
        return job

    job.is_seen = is_seen
    job.seen_at = timezone.now() if is_seen else None
    job.save(update_fields=['is_seen', 'seen_at', 'updated_at'])

    _record_event(
        job, JobEventType.FIELD_UPDATED,
        new_value='is_seen',
        note='Marked seen / in the works' if is_seen else 'Marked not seen',
        user=user,
    )
    logger.info('job seen toggled id=%s is_seen=%s', str(job.id)[:8], is_seen)
    return job


@transaction.atomic
def add_job_note(
        job: JobRecord,
        body: str,
        is_follow_up: bool = False,
        user=None,
) -> JobNote:
    created_by = user if (user and getattr(user, 'is_authenticated', False)) else None
    note = JobNote.objects.create(
        organization=job.organization,
        job=job,
        body=body.strip(),
        is_follow_up=is_follow_up,
        created_by=created_by,
    )
    _record_event(
        job, JobEventType.NOTE_ADDED,
        new_value='follow-up' if is_follow_up else 'note', user=user,
    )
    logger.info('job note added id=%s job=%s', str(note.id)[:8], str(job.id)[:8])
    return note
