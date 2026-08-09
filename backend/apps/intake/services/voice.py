"""
Voice intake service.

Handles inbound Twilio Voice webhook payloads:
  - status-callback  (CallStatus = ringing / in-progress / completed / …)
  - recording-status callback  (RecordingUrl available)
  - transcription-status callback  (TranscriptionText available)

The endpoint is idempotent per CallSid:
  - First webhook for a CallSid → creates an Interaction (review_required by default).
  - Subsequent webhook for the same CallSid WITH a transcript → updates the
    Interaction, runs classification, and creates a JobRecord when appropriate.
  - Subsequent webhooks WITHOUT new information are ignored gracefully.
"""

import logging
from typing import Any, Dict, Optional

from django.db import IntegrityError, transaction

from intake.models import (
    ChannelChoice,
    Contact,
    ContactSource,
    Interaction,
    InteractionCategory,
    InteractionStatus,
    JobRecord,
    JobStatus,
    PoRecStatus,
)
from intake.services.classification import (
    CATEGORY_JOB_REQUEST_INTAKE,
    classify_text,
)
from intake.services.email import (
    build_field_request_title,
    extract_inline_po_rec,
)
from jobs.services.notifications import fire_new_job_notifications

logger = logging.getLogger(__name__)

_CATEGORY_MAP = {
    'immediate_resolution': InteractionCategory.IMMEDIATE_RESOLUTION,
    'job_request_intake': InteractionCategory.JOB_REQUEST_INTAKE,
    'review_required': InteractionCategory.REVIEW_REQUIRED,
    'escalation': InteractionCategory.ESCALATION,
}

# Any of these extracted fields signals a job-request intake, so a card should
# be created even without a transcript. Minimal intake needs only name +
# community + a brief description, so `community` and description-style fields
# (summary/notes) count — not just the original service_type/builder/site/scope.
_JOB_SIGNAL_FIELDS = (
    'service_type', 'builder', 'site', 'scope', 'community', 'summary', 'notes',
)


def _normalize_phone(raw: str) -> str:
    cleaned = ''.join(c for c in raw if c.isdigit() or c == '+')
    return cleaned or raw


def _is_escalation_call(payload: Dict[str, Any], call_sid: str, *, organization) -> bool:
    """True when this voice call is a human-handoff / escalation, so we must
    NOT create a job for it (the card belongs only in the Escalation queue).

    Recognised signals:
      • explicit ``is_escalation`` truthy flag in the webhook payload;
      • ``category == 'escalation'`` in the payload;
      • an ESCALATION Interaction already logged for this CallSid (e.g. the
        agent called the escalation endpoint earlier in the same call).
    """
    flag = payload.get('is_escalation')
    if isinstance(flag, str):
        flag = flag.strip().lower() in ('true', '1', 'yes')
    if flag:
        return True
    if (payload.get('category') or '').strip().lower() == 'escalation':
        return True
    if call_sid:
        from django.db.models import Q
        already_escalated = (
            Interaction.objects
            .filter(organization=organization, category=InteractionCategory.ESCALATION)
            .filter(Q(provider_id=call_sid) | Q(provider_payload__call_sid=call_sid))
            .exists()
        )
        if already_escalated:
            return True
    return False


def _city_state_from_site(site: str) -> str:
    """Pull a "City" / "City, ST ZIP" tail off a comma-separated street address.

    Voice AI agents rarely emit a separate ``city_state`` field — they bundle
    everything into ``site`` like ``"189 East Avenue, Pittsburgh"`` or
    ``"189 East Ave, Pittsburgh, PA 15201"``.  When the first segment starts
    with a street number we treat the remainder as the city/state/ZIP tail
    and rejoin it; otherwise we bail out (too ambiguous to guess safely).
    """
    if not site or ',' not in site:
        return ''
    segments = [s.strip() for s in site.split(',') if s.strip()]
    if len(segments) < 2:
        return ''
    if not segments[0][0].isdigit():
        return ''
    return ', '.join(segments[1:])


def _extract_job_fields(payload: Dict[str, Any]) -> Dict[str, str]:
    """Pull optional structured job fields from the webhook payload."""
    fields = (
        'builder', 'project', 'site', 'community', 'service_type', 'install_type',
        'scope', 'quantity', 'urgency', 'division', 'po_rec_note', 'notes',
        'po_number', 'rec_number', 'lot_phase', 'city_state',
    )
    return {f: (payload.get(f) or '').strip() for f in fields}


def _get_or_create_contact(normalized_phone: str, *, organization) -> Optional[Contact]:
    if not normalized_phone:
        return None
    try:
        contact, created = Contact.objects.get_or_create(
            organization=organization,
            phone=normalized_phone,
            defaults={'source': ContactSource.VOICE},
        )
    except IntegrityError:
        contact = Contact.objects.get(organization=organization, phone=normalized_phone)
        created = False
    if created:
        logger.info('New contact auto-created from inbound voice call (phone masked)')
    return contact


def _build_raw_content(call_sid: str, from_phone: str, to_phone: str,
                       call_status: str, transcript: str) -> str:
    parts = []
    if call_sid:
        parts.append(f'CallSid: {call_sid}')
    if from_phone:
        parts.append(f'From: {from_phone}')
    if to_phone:
        parts.append(f'To: {to_phone}')
    if call_status:
        parts.append(f'Status: {call_status}')
    if transcript:
        parts.append(f'\nTranscript:\n{transcript}')
    return '\n'.join(parts)


def process_voice_webhook(payload: Dict[str, Any], *, organization, channel_connection) -> Interaction:
    """
    Process an inbound Twilio Voice webhook event.

    Behaviour:
      1. Extract core call identifiers and caller phone.
      2. Auto-create or retrieve a Contact for the caller.
      3. If an Interaction already exists for this CallSid:
           - If a transcript is now available → update transcript, re-classify,
             and create a linked JobRecord when job-related.
           - Otherwise → return the existing Interaction unchanged.
      4. If no Interaction exists yet → create one:
           - Classify using transcript text when available.
           - Fall back to review_required when no text is present.
           - Create a linked JobRecord when job-related.

    Returns the created or updated Interaction.
    """
    # ── Extract standard Twilio Voice fields ─────────────────────────────────
    call_sid = (payload.get('CallSid') or '').strip()
    from_phone = (payload.get('From') or '').strip()
    to_phone = (payload.get('To') or '').strip()
    call_status = (payload.get('CallStatus') or '').strip()
    recording_url = (payload.get('RecordingUrl') or '').strip()
    recording_sid = (payload.get('RecordingSid') or '').strip()
    transcript = (payload.get('TranscriptionText') or '').strip()
    transcript_sid = (payload.get('TranscriptionSid') or '').strip()
    ai_summary = (payload.get('ai_summary') or '').strip()

    normalized_phone = _normalize_phone(from_phone) if from_phone else ''
    job_fields = _extract_job_fields(payload)

    # ── Contact ───────────────────────────────────────────────────────────────
    contact = _get_or_create_contact(normalized_phone, organization=organization)

    # ── Deduplication — look up existing Interaction by CallSid ──────────────
    # `select_for_update` requires an active transaction; we open one here and
    # hold it through the update so the row lock is meaningful.
    if call_sid:
        existing = (
            Interaction.objects
            .filter(
                organization=organization,
                channel_connection=channel_connection,
                provider_id=call_sid,
                channel=ChannelChoice.VOICE,
            )
            .first()
        )
        if existing is not None:
            return _handle_existing_interaction(
                existing, transcript, transcript_sid, recording_url,
                ai_summary, job_fields, contact, payload,
            )

    return _create_new_interaction(
        call_sid=call_sid,
        from_phone=from_phone,
        to_phone=to_phone,
        call_status=call_status,
        recording_url=recording_url,
        recording_sid=recording_sid,
        transcript=transcript,
        transcript_sid=transcript_sid,
        ai_summary=ai_summary,
        contact=contact,
        job_fields=job_fields,
        payload=payload,
        organization=organization,
        channel_connection=channel_connection,
    )


def _create_new_interaction(
        call_sid: str,
        from_phone: str,
        to_phone: str,
        call_status: str,
        recording_url: str,
        recording_sid: str,
        transcript: str,
        transcript_sid: str,
        ai_summary: str,
        contact: Optional[Contact],
        job_fields: Dict[str, str],
        payload: Dict[str, Any],
        organization,
        channel_connection,
) -> Interaction:
    """Create a brand-new Interaction for a voice call."""

    # ── Classification ────────────────────────────────────────────────────────
    # Use the transcript when available; also consider structured job fields.
    text_to_classify = transcript
    # A voice summary counts as the "brief description", so it alone is enough
    # to create a card when no transcript is present.
    has_structured = any(job_fields.get(f) for f in _JOB_SIGNAL_FIELDS) or bool(
        (ai_summary or '').strip()
    )

    if text_to_classify:
        category_str, confidence, reason = classify_text(
            text_to_classify, organization=organization,
        )
    elif has_structured:
        # Structured fields from a voice-AI platform indicate a job request.
        category_str = CATEGORY_JOB_REQUEST_INTAKE
        confidence = 0.80
        reason = 'structured-voice-fields'
    else:
        # No text or structured fields — requires human review.
        category_str = 'review_required'
        confidence = 0.0
        reason = 'no-transcript'

    # When structured job fields are present, upgrade confidence to at least 0.80.
    if has_structured and category_str == CATEGORY_JOB_REQUEST_INTAKE:
        confidence = max(confidence, 0.80)
        reason = reason or 'structured-voice-fields'

    # An escalation (human handoff) must never become a job — route it to the
    # Escalation queue instead, regardless of structured fields / classification.
    if _is_escalation_call(payload, call_sid, organization=organization):
        category_str = 'escalation'
        reason = 'voice-escalation'

    interaction_category = _CATEGORY_MAP.get(category_str, InteractionCategory.REVIEW_REQUIRED)
    interaction_status = (
        InteractionStatus.REVIEW_REQUIRED
        if interaction_category == InteractionCategory.REVIEW_REQUIRED
        else InteractionStatus.NEW
    )

    # ── Build raw_content ─────────────────────────────────────────────────────
    raw_content = _build_raw_content(call_sid, from_phone, to_phone, call_status, transcript)

    # ── Recording/transcript reference stored in raw_content_reference ────────
    reference_parts = []
    if recording_url:
        reference_parts.append(f'recording:{recording_url}')
    if recording_sid:
        reference_parts.append(f'recording_sid:{recording_sid}')
    if transcript_sid:
        reference_parts.append(f'transcript_sid:{transcript_sid}')
    raw_content_reference = ' | '.join(reference_parts)

    interaction = Interaction.objects.create(
        organization=organization,
        channel_connection=channel_connection,
        channel=ChannelChoice.VOICE,
        contact=contact,
        caller_phone=from_phone,
        category=interaction_category,
        status=interaction_status,
        summary=ai_summary,
        raw_content=raw_content,
        raw_content_reference=raw_content_reference,
        provider_id=call_sid,
        provider_payload=payload,
        classification_confidence=confidence,
        classification_reason=reason,
    )

    # ── Optional Job creation ─────────────────────────────────────────────────
    job: Optional[JobRecord] = None
    if category_str == CATEGORY_JOB_REQUEST_INTAKE:
        job = _create_job_from_voice(interaction, contact, transcript, ai_summary, job_fields)

    logger.info(
        'Voice interaction created: id=%s call_sid=%s category=%s has_job=%s',
        str(interaction.id)[:8], call_sid[:8] if call_sid else 'n/a',
        category_str, job is not None,
    )
    return interaction


def _handle_existing_interaction(
        interaction: Interaction,
        transcript: str,
        transcript_sid: str,
        recording_url: str,
        ai_summary: str,
        job_fields: Dict[str, str],
        contact: Optional[Contact],
        payload: Dict[str, Any],
) -> Interaction:
    """
    Update an existing voice Interaction when new information arrives
    (e.g. transcription callback).  Only updates when there is something new.
    """
    if not transcript and not recording_url and not ai_summary:
        # Nothing new — idempotent return.
        logger.debug(
            'Voice webhook for existing interaction %s has no new data; skipping.',
            str(interaction.id)[:8],
        )
        return interaction

    update_fields = []

    # ── Update transcript in raw_content ─────────────────────────────────────
    if transcript and transcript not in (interaction.raw_content or ''):
        append = f'\n\nTranscript (updated):\n{transcript}'
        interaction.raw_content = (interaction.raw_content or '') + append
        update_fields.append('raw_content')

    # ── Update recording/transcript reference ─────────────────────────────────
    if recording_url or transcript_sid:
        parts = [interaction.raw_content_reference or '']
        if recording_url and recording_url not in parts[0]:
            parts.append(f'recording:{recording_url}')
        if transcript_sid and transcript_sid not in parts[0]:
            parts.append(f'transcript_sid:{transcript_sid}')
        new_ref = ' | '.join(p for p in parts if p)
        if new_ref != (interaction.raw_content_reference or ''):
            interaction.raw_content_reference = new_ref
            update_fields.append('raw_content_reference')

    # ── Update AI summary ─────────────────────────────────────────────────────
    if ai_summary and not interaction.summary:
        interaction.summary = ai_summary
        update_fields.append('summary')

    # ── Re-classify when a transcript arrives and interaction was review_required
    job_created = False
    if transcript and interaction.category == InteractionCategory.REVIEW_REQUIRED:
        has_structured = any(job_fields.get(f) for f in _JOB_SIGNAL_FIELDS)
        category_str, confidence, reason = classify_text(
            transcript, organization=interaction.organization,
        )
        if has_structured and category_str == CATEGORY_JOB_REQUEST_INTAKE:
            confidence = max(confidence, 0.80)
        # Escalation calls never become jobs — route to the Escalation queue.
        if _is_escalation_call(
            payload, interaction.provider_id, organization=interaction.organization,
        ):
            category_str = 'escalation'
            reason = 'voice-escalation'
        new_category = _CATEGORY_MAP.get(category_str, InteractionCategory.REVIEW_REQUIRED)
        if new_category != interaction.category:
            interaction.category = new_category
            interaction.status = (
                InteractionStatus.REVIEW_REQUIRED
                if new_category == InteractionCategory.REVIEW_REQUIRED
                else InteractionStatus.NEW
            )
            interaction.classification_confidence = confidence
            interaction.classification_reason = reason
            update_fields += ['category', 'status', 'classification_confidence', 'classification_reason']

            if category_str == CATEGORY_JOB_REQUEST_INTAKE and not interaction.related_job_id:
                job = _create_job_from_voice(interaction, contact, transcript, ai_summary, job_fields)
                job_created = True

    if update_fields:
        interaction.save(update_fields=list(set(update_fields + ['updated_at'])))
        logger.info(
            'Voice interaction updated: id=%s fields=%s job_created=%s',
            str(interaction.id)[:8], update_fields, job_created,
        )

    return interaction


def _create_job_from_voice(
        interaction: Interaction,
        contact: Optional[Contact],
        transcript: str,
        ai_summary: str,
        job_fields: Dict[str, str],
) -> JobRecord:
    """Create a JobRecord linked to the given voice Interaction."""
    service_type = job_fields.get('service_type', '')
    community = job_fields.get('community', '')
    site = job_fields.get('site', '')
    lot_phase = job_fields.get('lot_phase', '')
    po_number = job_fields.get('po_number', '')
    rec_number = job_fields.get('rec_number', '')
    city_state = job_fields.get('city_state', '')
    po_rec_note_raw = job_fields.get('po_rec_note', '')

    # Voice AI agents typically don't send a separate ``po_number`` field —
    # they stuff it into ``po_rec_note`` ("PO number is 1287RT") or only mention
    # it in the transcript.  Recover the identifier with the same parser email
    # uses so the UI's PO/REC fields populate consistently across channels.
    inline_search = ' '.join(filter(None, [po_rec_note_raw, transcript, ai_summary]))
    if not po_number and inline_search:
        po_number = extract_inline_po_rec(inline_search, kind='po')
    if not rec_number and inline_search:
        rec_number = extract_inline_po_rec(inline_search, kind='rec')

    # Combine PO/REC into po_rec_note so the serializer's ``\bPO\s*:`` regex
    # can split them back out for the "PO number" / "REC number" UI fields.
    pr_parts = []
    if po_number:
        pr_parts.append(f'PO: {po_number}')
    if rec_number:
        pr_parts.append(f'REC: {rec_number}')
    if pr_parts:
        combined = ' | '.join(pr_parts)
        po_rec_note = (
            combined if not po_rec_note_raw else f'{combined} | {po_rec_note_raw}'
        )
    else:
        po_rec_note = po_rec_note_raw

    # Voice payloads also rarely include a separate ``city_state`` field — the
    # AI bundles it into ``site``.  Recover it from the trailing comma-segment.
    if not city_state:
        city_state = _city_state_from_site(site)

    # Fold city/state into ``site`` so the address is stored as one full string
    # ("189 East Avenue, Pittsburgh, PA 15201").  When the AI already bundled
    # it into site the dup-check below makes this a no-op.
    if site and city_state and city_state.lower() not in site.lower():
        site = f'{site}, {city_state}'[:255]

    # Persist city_state on Contact.metadata — there's no dedicated column,
    # and the JobDetailSerializer reads it from metadata to populate City/state.
    if contact and city_state:
        meta = contact.metadata or {}
        if not meta.get('city_state'):
            meta['city_state'] = city_state
            contact.metadata = meta
            contact.save(update_fields=['metadata'])

    # Mirror the AI-generated description into ``summary`` so the Field Request
    # card shows the full readable narrative.  We fall back to a structured
    # "[Service] — [Community] (Lot)" title only when the AI didn't produce a
    # summary (e.g. transcription-only callbacks with no LLM enrichment).
    summary_text = ai_summary or build_field_request_title(
        service_type=service_type,
        community=community,
        lot_phase=lot_phase,
        site=site,
        fallback_summary=(job_fields.get('notes') or transcript)[:120],
    )

    # Map lot_phase → project so the dispatcher's "Lot/phase" column populates
    # (matches the email service convention).
    effective_project = job_fields.get('project', '') or lot_phase

    # Infer priority from the call transcript + AI summary + scope.
    # We never ask the caller directly because self-reported urgency is noise.
    from intake.services.classification import detect_priority
    inferred_priority = detect_priority(
        text=' '.join(filter(None, [
            transcript, ai_summary,
            job_fields.get('scope', ''), service_type,
            job_fields.get('notes', ''),
        ])),
        urgency_notes=job_fields.get('urgency', ''),
    )

    job = JobRecord.objects.create(
        organization=interaction.organization,
        source_channel=ChannelChoice.VOICE,
        contact=contact,
        source_interaction=interaction,
        service_type=service_type,
        install_type=job_fields.get('install_type', ''),
        builder=job_fields.get('builder', ''),
        project=effective_project,
        site=site,
        community=community,
        scope=job_fields.get('scope', ''),
        quantity=job_fields.get('quantity', ''),
        urgency_notes=job_fields.get('urgency', ''),
        priority=inferred_priority,
        division=job_fields.get('division', ''),
        po_rec_note=po_rec_note,
        po_required=bool(po_number),
        rec_required=bool(rec_number),
        po_status=PoRecStatus.RECEIVED if po_number else PoRecStatus.NOT_REQUIRED,
        rec_status=PoRecStatus.RECEIVED if rec_number else PoRecStatus.NOT_REQUIRED,
        summary=summary_text,
        ai_summary=ai_summary,
        status=JobStatus.NEW,
    )

    interaction.related_job = job
    interaction.save(update_fields=['related_job', 'updated_at'])
    fire_new_job_notifications(job)

    logger.info(
        'Voice job record created: job=%s interaction=%s service_type=%r',
        str(job.id)[:8], str(interaction.id)[:8], job.service_type,
    )
    return job
