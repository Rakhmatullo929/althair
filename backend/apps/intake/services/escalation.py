"""
Channel-agnostic escalation service.

Creates an ESCALATION ``Interaction`` that surfaces in the existing Escalation
queue (``apps/intake/views/escalations.py`` + the dashboard Escalation tab).

This mirrors what the SMS ``escalate_chat`` tool does (it creates an ESCALATION
Interaction), but WITHOUT the SMS-specific side effects (no ConversationThread,
no ack-SMS, no thread close) — so the external Voice platform can push a human
handoff (with name / transcript / summary) into the same queue.

Additive only: does not touch the SMS path, the voice webhook, or any model.
``provider_id`` is intentionally left blank so this never collides with the
voice webhook's CallSid de-duplication (the CallSid is kept in
``provider_payload`` for traceability instead).
"""

import logging

from django.db import transaction

from intake.models import (
    ChannelChoice,
    Contact,
    ContactSource,
    Interaction,
    InteractionCategory,
    InteractionStatus,
    JobEvent,
    JobEventType,
    JobRecord,
)

logger = logging.getLogger(__name__)

_SOURCE_BY_CHANNEL = {
    ChannelChoice.VOICE: ContactSource.VOICE,
    ChannelChoice.SMS: ContactSource.SMS,
    ChannelChoice.EMAIL: ContactSource.EMAIL,
    ChannelChoice.MANUAL: ContactSource.MANUAL,
}


def _normalize_phone(raw: str) -> str:
    cleaned = ''.join(c for c in (raw or '') if c.isdigit() or c == '+')
    return cleaned or (raw or '').strip()


def _get_or_create_contact(phone: str, name: str, channel: str, *, organization):
    if not phone:
        return None
    source = _SOURCE_BY_CHANNEL.get(channel, ContactSource.VOICE)
    contact, _created = Contact.objects.get_or_create(
        organization=organization,
        phone=phone,
        defaults={'source': source, 'name': (name or '')[:255]},
    )
    # Backfill the name if the caller gave it and we didn't have one — staff rely
    # on it when picking up the escalation.
    if name and not (contact.name or '').strip():
        contact.name = name[:255]
        contact.save(update_fields=['name'])
    return contact


def _build_raw_content(phone: str, reason: str, transcript: str, notes: str) -> str:
    """Compose raw_content so the Escalation tab renders header + transcript.

    The frontend EscalationView splits on a ``Transcript:`` marker, so we keep
    the same shape the voice webhook uses.
    """
    parts = []
    if phone:
        parts.append(f'From: {phone}')
    if reason:
        parts.append(f'Reason: {reason}')
    if notes:
        parts.append(f'Notes: {notes}')
    if transcript:
        parts.append(f'\nTranscript:\n{transcript}')
    return '\n'.join(parts)


@transaction.atomic
def create_escalation(
    *,
    organization,
    channel_connection,
    channel: str = ChannelChoice.VOICE,
    phone: str = '',
    name: str = '',
    reason: str = '',
    transcript: str = '',
    summary: str = '',
    notes: str = '',
    call_sid: str = '',
    related_job_id=None,
) -> Interaction:
    """Create an ESCALATION Interaction for the staff Escalation queue."""
    phone = _normalize_phone(phone)
    name = (name or '').strip()
    reason = (reason or '').strip()[:500] or 'unspecified'
    transcript = (transcript or '').strip()
    summary = (summary or '').strip()
    notes = (notes or '').strip()

    contact = _get_or_create_contact(
        phone, name, channel, organization=organization,
    )

    related_job = None
    if related_job_id:
        related_job = JobRecord.objects.filter(
            pk=related_job_id, organization=organization,
        ).first()

    interaction = Interaction.objects.create(
        organization=organization,
        channel_connection=channel_connection,
        channel=channel,
        contact=contact,
        caller_phone=phone,
        category=InteractionCategory.ESCALATION,
        status=InteractionStatus.REVIEW_REQUIRED,
        summary=summary,
        raw_content=_build_raw_content(phone, reason, transcript, notes),
        # Leave provider_id blank to avoid colliding with the voice webhook's
        # CallSid dedup; keep the CallSid in the payload for traceability.
        provider_payload={'call_sid': call_sid} if call_sid else {},
        escalation_reason=reason,
        is_read=False,
        related_job=related_job,
    )

    if related_job is not None:
        try:
            JobEvent.objects.create(
                organization=organization,
                job=related_job,
                event_type=JobEventType.NOTE_ADDED,
                note=f'[ESCALATION via {channel}] {reason}'[:1000],
            )
        except Exception:
            logger.warning('escalation: JobEvent record failed', exc_info=True)

    logger.info(
        '[escalation] created interaction=%s channel=%s reason=%r has_transcript=%s has_summary=%s',
        str(interaction.id)[:8], channel, reason, bool(transcript), bool(summary),
    )
    return interaction
