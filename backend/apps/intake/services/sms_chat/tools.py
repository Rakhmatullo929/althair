"""Tool definitions for the SMS chat agent and the executors that run them."""

import logging
from typing import Any, Dict

from django.db import transaction

from core.utils.send_message import send_message
from intake.models import (
    ChannelChoice,
    ConversationThread,
    Contact,
    Interaction,
    InteractionCategory,
    InteractionStatus,
    JobEvent,
    JobEventType,
    JobRecord,
    ThreadState,
)

from . import jobs
from .utils import parse_iso_date


def _send_after_commit(text: str, to_phone: str) -> None:
    """Schedule an outbound Twilio SMS to fire after the current transaction
    commits, so a later rollback never leaves a customer with an SMS that
    references DB state that no longer exists."""
    transaction.on_commit(lambda: send_message(text, to_phone, user=None))

logger = logging.getLogger(__name__)


SEND_SMS_TOOL = {
    'type': 'function',
    'function': {
        'name': 'send_sms',
        'description': 'Send one SMS to the customer.',
        'parameters': {
            'type': 'object',
            'additionalProperties': False,
            'required': ['text'],
            'properties': {
                'text': {
                    'type': 'string',
                    'minLength': 1,
                    'maxLength': 600,
                },
            },
        },
    },
}


CLOSE_CHAT_TOOL = {
    'type': 'function',
    'function': {
        'name': 'close_chat',
        'description': (
            'End the conversation cleanly when nothing should be submitted '
            '(out-of-scope request, customer changed their mind, etc.).  '
            'Sends one final SMS to the customer and closes the thread in '
            'a single call.  Use escalate_chat instead if the customer '
            'needs human attention.'
        ),
        'parameters': {
            'type': 'object',
            'additionalProperties': False,
            'required': ['farewell_text', 'reason'],
            'properties': {
                'farewell_text': {
                    'type': 'string',
                    'minLength': 1,
                    'maxLength': 600,
                    'description': (
                        'Final SMS sent to the customer before closing, '
                        'e.g. "Got it, take care!".'
                    ),
                },
                'reason': {
                    'type': 'string',
                    'maxLength': 200,
                    'description': 'Short internal note for logs.',
                },
            },
        },
    },
}


ESCALATE_CHAT_TOOL = {
    'type': 'function',
    'function': {
        'name': 'escalate_chat',
        'description': (
            'Hand the conversation off to a human operator.  Use when: the '
            'customer explicitly asks for a manager / human / agent; the '
            'customer is angry, abusive, or threatening; there is a safety '
            'emergency (fire, flood, gas leak, injury, downed power line); '
            'or the customer is complaining about a previous job, raising a '
            'billing dispute, or a legal / liability concern.  Sends one '
            'acknowledgement SMS to the customer, surfaces the message in '
            'the staff escalation queue, and closes the thread — all in '
            'one call.'
        ),
        'parameters': {
            'type': 'object',
            'additionalProperties': False,
            'required': ['ack_text', 'reason', 'contact_name'],
            'properties': {
                'ack_text': {
                    'type': 'string',
                    'minLength': 1,
                    'maxLength': 600,
                    'description': (
                        'Calm acknowledgement SMS sent to the customer, '
                        'e.g. "Got it, a team member will reach out '
                        'shortly."'
                    ),
                },
                'reason': {
                    'type': 'string',
                    'maxLength': 500,
                    'description': (
                        'Short note describing WHY this is being escalated '
                        '(e.g. "customer requested manager", "safety: gas '
                        'leak reported", "complaint about job #12345").'
                    ),
                },
                'contact_name': {
                    'type': 'string',
                    'maxLength': 100,
                    'description': (
                        'The customer\'s name (person, not company).  '
                        'Operator needs it to call back.  Pass empty '
                        'string only if the customer truly never gave '
                        'their name in the conversation.'
                    ),
                },
            },
        },
    },
}


ENRICH_JOB_TOOL = {
    'type': 'function',
    'function': {
        'name': 'enrich_job',
        'description': (
            'Append additional information to the customer\'s ACTIVE '
            'JobRecord (the one shown as "Active job" in the system '
            'context).  Use ONLY when an active job already exists and '
            'the customer is providing follow-up details — do NOT use '
            'this for a brand-new request (use finish_and_submit) and do '
            'NOT call this if there is no active job.  This tool appends '
            'the new info to the job\'s scope and timeline so the '
            'assignee sees it.  Not terminal — you must follow it with '
            'one send_sms acknowledgement.'
        ),
        'parameters': {
            'type': 'object',
            'additionalProperties': False,
            'required': ['note'],
            'properties': {
                'note': {
                    'type': 'string',
                    'minLength': 1,
                    'maxLength': 1000,
                    'description': (
                        'The new information from the customer in your '
                        'own words — what should be added to the job.'
                    ),
                },
                'scope_addition': {
                    'type': 'string', 'maxLength': 4000,
                    'description': (
                        'Optional text to APPEND to the job scope (the '
                        'tool will join it with a newline).'
                    ),
                },
                'scheduled_date': {
                    'type': 'string',
                    'pattern': r'^(\d{4}-\d{2}-\d{2})?$',
                    'description': (
                        'Optional ISO date if the customer changed/added '
                        'a deadline.  Empty string = no change.'
                    ),
                },
                'urgency_notes': {
                    'type': 'string', 'maxLength': 500,
                    'description': (
                        'Optional non-date urgency cue ("ASAP", "rush") '
                        'to add to the job.'
                    ),
                },
                'po_rec_note': {
                    'type': 'string', 'maxLength': 500,
                    'description': (
                        'Optional PO/REC number if the customer is '
                        'providing one now.'
                    ),
                },
                'quantity': {
                    'type': 'string', 'maxLength': 100,
                    'description': 'Optional updated quantity.',
                },
            },
        },
    },
}


FINISH_AND_SUBMIT_TOOL = {
    'type': 'function',
    'function': {
        'name': 'finish_and_submit',
        'description': 'Submit the collected job and create a JobRecord.',
        'parameters': {
            'type': 'object',
            'additionalProperties': False,
            'required': [
                'service_type', 'site', 'community', 'builder',
                'scope', 'scheduled_date', 'po_rec_note', 'contact_name',
            ],
            'properties': {
                'service_type': {
                    'type': 'string', 'maxLength': 255,
                    'description': 'Trade or service category, e.g. street sweeping, erosion control.',
                },
                'site': {
                    'type': 'string', 'maxLength': 255,
                    'description': 'Specific job-site / address within the community.',
                },
                'community': {
                    'type': 'string', 'maxLength': 255,
                    'description': 'Named neighborhood or development. Empty string if not provided.',
                },
                'builder': {
                    'type': 'string', 'maxLength': 255,
                    'description': 'Company / GC the customer represents. Empty string if not provided.',
                },
                'project': {
                    'type': 'string', 'maxLength': 255,
                    'description': 'Lot / unit / phase identifier (optional).',
                },
                'scope': {
                    'type': 'string', 'maxLength': 4000,
                    'description': 'Free-form description of the work to be done.',
                },
                'quantity': {
                    'type': 'string', 'maxLength': 100,
                    'description': 'Numeric scope as the customer phrased it: "8 hours", "20 tons" (optional).',
                },
                'urgency_notes': {
                    'type': 'string', 'maxLength': 500,
                    'description': (
                        'Non-date urgency cues only — "ASAP", "rush", "before crews arrive". '
                        'NEVER put a date here; dates go in scheduled_date.'
                    ),
                },
                'po_rec_note': {
                    'type': 'string', 'maxLength': 500,
                    'description': (
                        'PO or REC number the customer provided, or "" if they said they '
                        'do not have one yet.'
                    ),
                },
                'scheduled_date': {
                    'type': 'string',
                    'pattern': r'^(\d{4}-\d{2}-\d{2})?$',
                    'description': (
                        'ISO YYYY-MM-DD.  ALWAYS put any date the customer mentioned here, '
                        'including resolved relative dates ("monday", "next friday", '
                        '"tomorrow") — resolve against the "Now (local)" line in the '
                        'system context.  Empty string ONLY if the customer truly did '
                        'not give any date.'
                    ),
                },
                'contact_name': {
                    'type': 'string', 'maxLength': 100,
                    'description': 'Person\'s name only (NOT the company). Empty string if not provided.',
                },
                'contact_email': {
                    'type': 'string', 'maxLength': 254,
                    'description': 'Customer email if mentioned (optional).',
                },
                'summary': {
                    'type': 'string', 'maxLength': 500,
                    'description': '2-3 word title for the job, e.g. "Street Sweeping" (optional).',
                },
            },
        },
    },
}


MMC_CHECKER_TOOL = {
    'type': 'function',
    'function': {
        'name': 'mmc_checker',
        'description': (
            'Look up what MMC already knows about this caller / site so you do '
            'NOT re-ask details that are already on file.  Call it with the '
            "caller's phone, or a community name, or an address.  It returns the "
            'known site, address, city, state, community, builder and lot from '
            'their prior jobs.  Whenever it returns data, reuse those values and '
            'do not ask the customer for them again.'
        ),
        'parameters': {
            'type': 'object',
            'additionalProperties': False,
            'properties': {
                'phone': {
                    'type': 'string',
                    'description': "Caller phone number, e.g. '+15551234567'. Defaults to this thread's number if omitted.",
                },
                'community': {
                    'type': 'string',
                    'description': 'Community / subdivision name to look up.',
                },
                'address': {
                    'type': 'string',
                    'description': 'Street address or job site to look up.',
                },
            },
        },
    },
}


ALL_TOOLS = [
    SEND_SMS_TOOL,
    CLOSE_CHAT_TOOL,
    ESCALATE_CHAT_TOOL,
    ENRICH_JOB_TOOL,
    FINISH_AND_SUBMIT_TOOL,
    MMC_CHECKER_TOOL,
]


def execute_send_sms(thread: ConversationThread, args: Dict[str, Any]) -> Dict[str, Any]:
    text = (args.get('text') or '').strip()
    if not text:
        return {'status': 'error', 'error': 'empty_text'}
    _send_after_commit(text, thread.phone)
    return {'status': 'sent'}


def execute_mmc_checker(
    thread: ConversationThread,
    contact: Contact,
    args: Dict[str, Any],
) -> Dict[str, Any]:
    """Return MMC's known context for the caller / site so the agent stops
    re-asking known details.  Reuses the same recall logic the external Voice
    platform uses via the Context Lookup API."""
    # Imported lazily to keep the module import graph flat.
    from intake.services.recall import lookup_context

    phone = (args.get('phone') or '').strip()
    community = (args.get('community') or '').strip()
    address = (args.get('address') or '').strip()

    # Default to the caller's own number when the model passed nothing — the
    # most common case (an existing customer texting in).
    if not (phone or community or address):
        phone = (contact.phone if contact else '') or thread.phone

    try:
        result = lookup_context(
            organization=thread.organization,
            phone=phone,
            community=community,
            address=address,
        )
    except Exception as exc:
        logger.error('mmc_checker failed: %s', type(exc).__name__, exc_info=True)
        return {'status': 'error', 'error': type(exc).__name__}

    return {'status': 'ok', **result}


def execute_finish_and_submit(
    thread: ConversationThread,
    contact: Contact,
    args: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        job = jobs.finalize_job(thread, contact, args)
    except Exception as exc:
        logger.error('finish_and_submit failed: %s', type(exc).__name__, exc_info=True)
        return {'status': 'error', 'error': type(exc).__name__}
    return {'status': 'ok', 'job_id': str(job.id)}


def execute_enrich_job(
    thread: ConversationThread,
    contact: Contact,
    args: Dict[str, Any],
    inbound_body: str,
) -> Dict[str, Any]:
    """Append info to the thread's active JobRecord and add a timeline event."""
    if thread.active_job_id is None:
        return {'status': 'error', 'error': 'no_active_job'}

    # No select_for_update on JobRecord — would create thread→job lock order
    # that conflicts with admin/notification paths.  An SMS race on a single
    # job is mild and bounded by the thread lock we already hold.
    try:
        job = JobRecord.objects.get(pk=thread.active_job_id, organization=thread.organization)
    except JobRecord.DoesNotExist:
        thread.active_job = None
        thread.save(update_fields=['active_job', 'last_message_at'])
        return {'status': 'error', 'error': 'active_job_missing'}

    note = (args.get('note') or '').strip()
    update_fields = []

    scope_addition = (args.get('scope_addition') or '').strip()
    if scope_addition:
        existing = (job.scope or '').strip()
        job.scope = (existing + '\n' + scope_addition).strip()[:4000] if existing else scope_addition[:4000]
        update_fields.append('scope')

    scheduled_date = (args.get('scheduled_date') or '').strip()
    if scheduled_date:
        parsed = parse_iso_date(scheduled_date)
        if parsed:
            job.scheduled_date = parsed
            update_fields.append('scheduled_date')

    urgency_notes = (args.get('urgency_notes') or '').strip()
    if urgency_notes:
        existing = (job.urgency_notes or '').strip()
        if existing:
            job.urgency_notes = f'{existing}; {urgency_notes}'[:500]
        else:
            job.urgency_notes = urgency_notes[:500]
        update_fields.append('urgency_notes')

    po_rec_note = (args.get('po_rec_note') or '').strip()
    if po_rec_note:
        job.po_rec_note = po_rec_note[:500]
        update_fields.append('po_rec_note')

    quantity = (args.get('quantity') or '').strip()
    if quantity:
        job.quantity = quantity[:100]
        update_fields.append('quantity')

    if update_fields:
        job.save(update_fields=update_fields)

    try:
        JobEvent.objects.create(
            organization=thread.organization,
            job=job,
            event_type=JobEventType.NOTE_ADDED,
            note=f'[SMS update] {note} — raw: {inbound_body[:200]}'[:1000],
        )
    except Exception:
        logger.warning('enrich_job: JobEvent record failed', exc_info=True)

    return {
        'status': 'ok',
        'job_id': str(job.id),
        'updated_fields': update_fields,
    }


def execute_close_chat(
    thread: ConversationThread,
    contact: Contact,
    args: Dict[str, Any],
    inbound_payload: Dict[str, Any],
    inbound_body: str,
) -> Dict[str, Any]:
    farewell = (args.get('farewell_text') or '').strip()
    reason = (args.get('reason') or '').strip()[:200]

    sent = bool(farewell)
    if farewell:
        _send_after_commit(farewell, thread.phone)

    thread.state = ThreadState.CLOSED
    thread.is_active = False
    thread.save(update_fields=['state', 'is_active', 'last_message_at'])

    Interaction.objects.create(
        organization=thread.organization,
        channel_connection=thread.channel_connection,
        channel=ChannelChoice.SMS,
        contact=contact,
        caller_phone=thread.phone,
        category=InteractionCategory.IMMEDIATE_RESOLUTION,
        status=InteractionStatus.RESOLVED,
        raw_content=inbound_body,
        summary=(farewell or f'closed:{reason}' or 'closed')[:500],
        # This is a derived lifecycle record, not a second provider delivery.
        provider_id='',
        provider_payload=inbound_payload,
        classification_reason=f'closed:{reason}' if reason else 'closed',
    )
    return {'status': 'closed', 'reason': reason, 'sms_sent': sent}


def execute_escalate_chat(
    thread: ConversationThread,
    contact: Contact,
    args: Dict[str, Any],
    inbound_payload: Dict[str, Any],
    inbound_body: str,
) -> Dict[str, Any]:
    """Send the customer ack, mark the conversation for human review, and close."""
    ack_text = (args.get('ack_text') or '').strip()
    reason = (args.get('reason') or '').strip()[:500] or 'unspecified'
    contact_name = (args.get('contact_name') or '').strip()

    # Refuse to escalate without a real customer name — operator needs
    # someone to call back.  Returns an error result; the agent loop
    # treats this as non-terminal so the model can react by asking for
    # the name via send_sms.  Reject obvious placeholder values too.
    placeholders = {'', 'unknown', 'n/a', 'na', 'customer', 'client', 'user', 'anonymous'}
    if contact_name.lower() in placeholders and not (contact.name or '').strip():
        return {
            'status': 'error',
            'error': 'missing_contact_name',
            'message': (
                'Cannot escalate without the customer\'s real name.  '
                'Send_sms to ask "Can I get your name so a team member '
                'can reach back out?", wait for the next customer reply, '
                'then call escalate_chat again with their name in '
                'contact_name.'
            ),
        }

    # Backfill the Contact's name if the LLM captured one and we didn't
    # have it yet — operators rely on this when picking up an escalation.
    if contact_name and not contact.name:
        contact.name = contact_name[:255]
        contact.save(update_fields=['name'])

    sent = bool(ack_text)
    if ack_text:
        _send_after_commit(ack_text, thread.phone)

    thread.state = ThreadState.CLOSED
    thread.is_active = False
    thread.save(update_fields=['state', 'is_active', 'last_message_at'])

    interaction = Interaction.objects.create(
        organization=thread.organization,
        channel_connection=thread.channel_connection,
        channel=ChannelChoice.SMS,
        contact=contact,
        caller_phone=thread.phone,
        category=InteractionCategory.ESCALATION,
        status=InteractionStatus.REVIEW_REQUIRED,
        raw_content=inbound_body,
        summary=(ack_text or f'escalation: {reason}')[:500],
        # The inbound delivery already owns the provider idempotency key.
        provider_id='',
        provider_payload=inbound_payload,
        escalation_reason=reason,
        is_read=False,
        related_job=thread.active_job,
    )

    if thread.active_job_id:
        try:
            JobEvent.objects.create(
                organization=thread.organization,
                job=thread.active_job,
                event_type=JobEventType.NOTE_ADDED,
                note=f'[ESCALATION via SMS] {reason} — {inbound_body[:200]}'[:1000],
            )
        except Exception:
            logger.warning('escalate_chat: JobEvent record failed', exc_info=True)

    return {
        'status': 'escalated',
        'reason': reason,
        'sms_sent': sent,
        'interaction_id': str(interaction.id),
    }
