"""Main entry point and agent loop for the SMS chat."""

import json
import logging
from typing import Any, Dict, List, Optional

from django.db import transaction

from intake.models import (
    ChannelChoice,
    Contact,
    ConversationThread,
    Interaction,
    InteractionCategory,
    InteractionStatus,
    ThreadState,
)

from . import history, prompts, tools
from .utils import (
    MAX_TOOL_ITERATIONS,
    OPENAI_MAX_TOKENS,
    OPENAI_MODEL,
    OPENAI_TEMPERATURE,
    get_openai_client,
    normalize_phone,
)

logger = logging.getLogger(__name__)


def handle_inbound_sms(payload: Dict[str, Any], *, organization, channel_connection) -> Interaction:
    """Process one Twilio SMS webhook payload through the agent."""
    raw_from = (payload.get('From') or '').strip()
    body = (payload.get('Body') or '').strip()
    message_sid = (payload.get('MessageSid') or '').strip()[:255]
    norm_phone = normalize_phone(raw_from)

    # Twilio retries on socket-level failures even after a 200.  Same
    # MessageSid means the same physical SMS — short-circuit so we don't
    # double-bill OpenAI or fire finish_and_submit twice.
    if message_sid:
        existing = (
            Interaction.objects
            .filter(
                organization=organization,
                channel_connection=channel_connection,
                channel=ChannelChoice.SMS,
                provider_id=message_sid,
            )
            .first()
        )
        if existing is not None:
            logger.info(
                'SMS webhook duplicate detected for MessageSid=%s — replaying 200',
                message_sid[:16],
            )
            return existing

    contact = get_or_create_contact(norm_phone, organization=organization)
    thread = resolve_thread(
        norm_phone,
        contact,
        organization=organization,
        channel_connection=channel_connection,
    )

    history.persist_user_message(thread, body)

    interaction = Interaction.objects.create(
        organization=organization,
        channel_connection=channel_connection,
        channel=ChannelChoice.SMS,
        contact=contact,
        caller_phone=raw_from[:30],
        category=InteractionCategory.JOB_REQUEST_INTAKE,
        status=InteractionStatus.IN_PROGRESS,
        raw_content=body,
        provider_id=message_sid,
        provider_payload=dict(payload),
    )

    system_msgs = prompts.build_system_messages(thread, contact)
    chat_history = history.load_history(thread)
    messages = [*system_msgs, *chat_history]

    summary = run_agent_loop(thread, contact, messages, payload, body)

    interaction.summary = (summary or '')[:500]
    interaction.save(update_fields=['summary'])
    return interaction


def get_or_create_contact(norm_phone: str, *, organization) -> Contact:
    """Return (and lock) the Contact row for *norm_phone*.

    Locking the Contact serialises concurrent webhooks for the same phone
    BEFORE we look at threads — this prevents two simultaneous first-time
    SMS deliveries from each creating their own ConversationThread.
    """
    if not norm_phone:
        return Contact.objects.create(organization=organization, source='sms')
    existing = (
        Contact.objects.select_for_update()
        .filter(organization=organization, phone=norm_phone)
        .order_by('-created_at')
        .first()
    )
    if existing is not None:
        return existing
    contact, _ = Contact.objects.get_or_create(
        organization=organization, phone=norm_phone, defaults={'source': 'sms'},
    )
    # Re-lock the row we just observed/created for the rest of the request.
    return Contact.objects.select_for_update().get(pk=contact.pk, organization=organization)


def resolve_thread(norm_phone: str, contact: Contact, *, organization, channel_connection) -> ConversationThread:
    """Return an OPEN thread for the phone, creating one if needed."""
    thread = (
        ConversationThread.objects
        .select_for_update()
        .filter(
            organization=organization,
            channel_connection=channel_connection,
            phone=norm_phone,
            state=ThreadState.OPEN,
            is_active=True,
        )
        .order_by('-last_message_at')
        .first()
    )

    if thread is not None and thread.is_timed_out():
        thread.state = ThreadState.CLOSED
        thread.is_active = False
        thread.save(update_fields=['state', 'is_active', 'last_message_at'])
        thread = None

    if thread is None:
        thread = ConversationThread.objects.create(
            organization=organization,
            channel_connection=channel_connection,
            phone=norm_phone,
            contact=contact,
            state=ThreadState.OPEN,
            is_active=True,
        )
    elif thread.contact_id != getattr(contact, 'id', None):
        thread.contact = contact
        thread.save(update_fields=['contact', 'last_message_at'])

    return thread


def run_agent_loop(
    thread: ConversationThread,
    contact: Contact,
    messages: List[Dict[str, Any]],
    payload: Dict[str, Any],
    inbound_body: str,
) -> str:
    """Loop on OpenAI tool calls until terminal (send_sms) or max iterations."""
    client = get_openai_client()
    last_summary = ''

    for step in range(MAX_TOOL_ITERATIONS):
        try:
            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=messages,
                tools=tools.ALL_TOOLS,
                tool_choice='required',
                parallel_tool_calls=False,
                temperature=OPENAI_TEMPERATURE,
                max_tokens=OPENAI_MAX_TOKENS,
            )
        except Exception as exc:
            logger.error(
                'OpenAI call failed at step %d: %s', step, type(exc).__name__,
                exc_info=True,
            )
            return last_summary or 'openai_error'

        msg = resp.choices[0].message
        tool_calls = list(msg.tool_calls or [])

        if not tool_calls:
            text = (msg.content or '').strip()
            if text:
                tools.execute_send_sms(thread, {'text': text})
                history.persist_assistant(thread, text, None)
                return text
            logger.warning('Agent returned no tool_calls and empty content')
            return last_summary

        tool_call = tool_calls[0]
        name = tool_call.function.name
        try:
            args = json.loads(tool_call.function.arguments or '{}')
        except json.JSONDecodeError:
            logger.warning('Tool call %s had invalid JSON arguments', name)
            args = {}

        oai_tool_calls = [{
            'id': tool_call.id,
            'type': 'function',
            'function': {
                'name': name,
                'arguments': tool_call.function.arguments or '{}',
            },
        }]

        history.persist_assistant(thread, msg.content or '', oai_tool_calls)
        messages.append({
            'role': 'assistant',
            'content': msg.content or None,
            'tool_calls': oai_tool_calls,
        })

        if name == 'send_sms':
            result = tools.execute_send_sms(thread, args)
            history.persist_tool_result(thread, tool_call.id, name, result)
            return (args.get('text') or '')[:500]

        if name == 'finish_and_submit':
            result = tools.execute_finish_and_submit(thread, contact, args)
            history.persist_tool_result(thread, tool_call.id, name, result)
            messages.append(_tool_result_message(tool_call.id, result))
            last_summary = f'job_created:{result.get("job_id", "")[:12]}'
            continue

        if name == 'enrich_job':
            result = tools.execute_enrich_job(thread, contact, args, inbound_body)
            history.persist_tool_result(thread, tool_call.id, name, result)
            messages.append(_tool_result_message(tool_call.id, result))
            last_summary = f'job_enriched:{result.get("job_id", "")[:12]}'
            continue

        if name == 'mmc_checker':
            # Read-only context lookup — feed the known data back to the model
            # and continue so it can reuse it instead of re-asking.
            result = tools.execute_mmc_checker(thread, contact, args)
            history.persist_tool_result(thread, tool_call.id, name, result)
            messages.append(_tool_result_message(tool_call.id, result))
            last_summary = f'mmc_checker:{result.get("match_type", "")}'
            continue

        if name == 'close_chat':
            result = tools.execute_close_chat(
                thread, contact, args, payload, inbound_body,
            )
            history.persist_tool_result(thread, tool_call.id, name, result)
            return f'closed:{(result.get("reason") or "")[:64]}'

        if name == 'escalate_chat':
            result = tools.execute_escalate_chat(
                thread, contact, args, payload, inbound_body,
            )
            history.persist_tool_result(thread, tool_call.id, name, result)
            if result.get('status') == 'error':
                # Non-terminal on validation failure — let the model react
                # (e.g. send_sms to ask for the missing field) within the
                # same webhook iteration.
                messages.append(_tool_result_message(tool_call.id, result))
                last_summary = f'escalation_error:{result.get("error", "")[:32]}'
                continue
            return f'escalated:{(result.get("reason") or "")[:64]}'

        logger.warning('Unknown tool from agent: %s', name)
        history.persist_tool_result(
            thread, tool_call.id, name, {'status': 'error', 'error': 'unknown_tool'},
        )
        return last_summary or 'unknown_tool'

    logger.warning(
        'Agent loop hit MAX_TOOL_ITERATIONS=%d without sending an SMS',
        MAX_TOOL_ITERATIONS,
    )
    return last_summary or 'max_iterations'


def _tool_result_message(tool_call_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'role': 'tool',
        'tool_call_id': tool_call_id,
        'content': json.dumps(payload, ensure_ascii=False),
    }
