"""Finalize a JobRecord from finish_and_submit tool arguments."""

import logging
from typing import Any, Dict

from intake.models import (
    ChannelChoice,
    Contact,
    ConversationThread,
    Interaction,
    JobRecord,
    JobStatus,
)

from .utils import parse_iso_date

logger = logging.getLogger(__name__)


def finalize_job(
    thread: ConversationThread,
    contact: Contact,
    args: Dict[str, Any],
) -> JobRecord:
    """Create a JobRecord from `finish_and_submit` arguments."""
    apply_contact_updates(contact, args)

    service_type = (args.get('service_type') or '').strip()
    scope_text = (args.get('scope') or '').strip()
    quantity = (args.get('quantity') or '').strip()
    urgency_notes = (args.get('urgency_notes') or '').strip()
    po_rec_note = (args.get('po_rec_note') or '').strip()
    scheduled_date = parse_iso_date(args.get('scheduled_date'))

    summary_arg = (args.get('summary') or '').strip()
    summary = summary_arg or compute_short_summary(
        scope_text or service_type,
        service_type,
        organization=thread.organization,
    )
    priority = compute_priority(
        text=' '.join(filter(None, [scope_text, service_type])),
        urgency_notes=urgency_notes,
    )

    source_interaction = latest_unlinked_interaction(contact)

    job = JobRecord.objects.create(
        organization=thread.organization,
        source_channel=ChannelChoice.SMS,
        contact=contact,
        source_interaction=source_interaction,
        service_type=service_type[:255],
        builder=(args.get('builder') or '').strip()[:255],
        project=(args.get('project') or '').strip()[:255],
        site=(args.get('site') or '').strip()[:255],
        community=(args.get('community') or '').strip()[:255],
        scope=scope_text[:4000],
        quantity=quantity[:100],
        urgency_notes=urgency_notes[:500],
        po_rec_note=po_rec_note[:500],
        scheduled_date=scheduled_date,
        priority=priority,
        summary=summary[:500],
        status=JobStatus.NEW,
    )

    thread.active_job = job
    thread.save(update_fields=['active_job', 'last_message_at'])

    if source_interaction is not None:
        source_interaction.related_job = job
        source_interaction.save(update_fields=['related_job'])

    fire_notifications(job)
    backfill_timeline(job, contact, source_interaction)

    return job


def apply_contact_updates(contact: Contact, args: Dict[str, Any]) -> None:
    """If the LLM passed a fresh name/email and Contact has none, fill it in."""
    fields_to_update = []
    name = (args.get('contact_name') or '').strip()
    if name and not contact.name:
        contact.name = name[:255]
        fields_to_update.append('name')
    email = (args.get('contact_email') or '').strip()
    if email and not contact.email:
        contact.email = email[:254]
        fields_to_update.append('email')
    if fields_to_update:
        contact.save(update_fields=fields_to_update)


def compute_short_summary(text: str, service_type: str, *, organization) -> str:
    """Use the existing classifier helper if available, else trim raw text."""
    try:
        from intake.services.classification import get_short_summary
        return get_short_summary(
            text=text,
            service_type=service_type,
            organization=organization,
        ) or (text or service_type)[:80]
    except Exception:
        return (text or service_type)[:80]


def compute_priority(text: str, urgency_notes: str) -> str:
    try:
        from intake.services.classification import detect_priority
        return detect_priority(text=text, urgency_notes=urgency_notes)
    except Exception:
        from intake.models import JobPriority
        return JobPriority.NORMAL


def latest_unlinked_interaction(contact: Contact):
    return (
        Interaction.objects
        .filter(
            organization=contact.organization,
            contact=contact,
            channel=ChannelChoice.SMS,
            related_job__isnull=True,
        )
        .order_by('-created_at')
        .first()
    )


def fire_notifications(job: JobRecord) -> None:
    try:
        from jobs.services.notifications import fire_new_job_notifications
        fire_new_job_notifications(job)
    except Exception as exc:
        logger.error('fire_new_job_notifications failed: %s', type(exc).__name__, exc_info=True)


def backfill_timeline(job: JobRecord, contact: Contact, source_interaction) -> None:
    """Link prior orphan SMS Interactions to *job* and record them in the timeline."""
    try:
        from jobs.services.notifications import record_incoming_message
    except Exception:
        return

    sender_label = contact.name or contact.phone or 'SMS'
    orphan_qs = (
        Interaction.objects
        .filter(
            organization=job.organization,
            contact=contact,
            channel=ChannelChoice.SMS,
            related_job__isnull=True,
        )
        .order_by('created_at')
    )
    for orphan in orphan_qs:
        if source_interaction is not None and orphan.pk == source_interaction.pk:
            continue
        try:
            record_incoming_message(
                job,
                channel='sms',
                body=orphan.raw_content,
                sender=sender_label,
                timestamp=orphan.created_at,
            )
        except Exception:
            logger.warning('backfill record_incoming_message failed', exc_info=True)
        orphan.related_job = job
        orphan.save(update_fields=['related_job'])

    if source_interaction is not None:
        try:
            record_incoming_message(
                job,
                channel='sms',
                body=source_interaction.raw_content,
                sender=sender_label,
                timestamp=source_interaction.created_at,
            )
        except Exception:
            logger.warning('backfill source_interaction record failed', exc_info=True)
