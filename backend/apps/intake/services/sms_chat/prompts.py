"""Build the four system messages that prefix every agent call."""

import datetime
import logging
from typing import Dict, List, Optional

from django.utils import timezone

from intake.models import (
    Contact,
    ConversationThread,
    JobRecord,
    KnowledgeBaseEntry,
    SystemPrompt,
)

logger = logging.getLogger(__name__)


KB_BLOCK_SOFT_CAP = 6000
PAST_JOBS_LIMIT = 5
SCOPE_PREVIEW_CHARS = 200

_GENERIC_KB_QUESTION_VALUES = {
    '', 'general', 'general question', 'general questions',
    'kb', 'knowledge base', 'faq', 'faqs', 'info', 'information',
}


def build_system_messages(
    thread: ConversationThread,
    contact: Optional[Contact],
) -> List[Dict[str, str]]:
    """Return the list of system messages to prepend to the agent call.

    Always emits four blocks: main prompt, knowledge base, customer context,
    runtime rules. The runtime rules block is intentionally left as a stub
    so the operator can fill it in later via a SystemPrompt key without
    redeploying.
    """
    messages = [
        {'role': 'system', 'content': main_prompt_block(thread.organization)},
        {'role': 'system', 'content': knowledge_base_block(thread.organization)},
        {'role': 'system', 'content': customer_context_block(thread, contact)},
    ]
    rules = runtime_rules_block(thread.organization)
    if rules.strip():
        messages.append({'role': 'system', 'content': rules})
    return messages


def main_prompt_block(organization) -> str:
    """Read the operator-editable main intake prompt, with a small safe default."""
    default = (
        'You are an SMS intake assistant for a service company. Talk to the '
        'customer through the send_sms tool, collect the details needed to '
        'create a job, and call finish_and_submit when ready. Use close_chat '
        'when the conversation should end without a submission. Keep replies '
        'short and conversational.'
    )
    return SystemPrompt.get('sms_assistant', organization=organization, default=default) or default


def knowledge_base_block(organization) -> str:
    """Concatenate active KnowledgeBaseEntry rows into one context block."""
    entries = list(
        KnowledgeBaseEntry.objects.filter(organization=organization, is_active=True).order_by('topic')
    )
    if not entries:
        return 'Knowledge Base: (empty)'
    parts = []
    total = 0
    for entry in entries:
        topic = (entry.topic or '').strip() or 'General'
        answer = (entry.answer or '').strip()
        if not answer:
            continue
        block_lines = [f'## {topic}']
        question = (entry.question or '').strip()
        if question and question.lower() not in _GENERIC_KB_QUESTION_VALUES:
            block_lines.append(f'Q: {question}')
        tags = (entry.tags or '').strip()
        if tags:
            block_lines.append(f'Tags: {tags}')
        block_lines.append(f'A: {answer}')
        block = '\n'.join(block_lines)
        if total + len(block) > KB_BLOCK_SOFT_CAP:
            break
        parts.append(block)
        total += len(block) + 2
    if not parts:
        return 'Knowledge Base: (empty)'
    return 'Knowledge Base:\n\n' + '\n\n'.join(parts)


def customer_context_block(
    thread: ConversationThread,
    contact: Optional[Contact],
) -> str:
    """Render the dynamic per-turn context: time, customer profile, job history."""
    now_utc = timezone.now()
    lines = [
        f'Now (UTC): {now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")}',
        f'Now (local): {timezone.localtime(now_utc).strftime("%A, %B %d %Y, %H:%M %Z")}',
        '',
    ]

    lines.append('Customer profile:')
    if contact is None:
        lines.append('  (no contact record yet)')
    else:
        masked = _mask_phone(contact.phone or thread.phone)
        lines.append(f'  name:  {contact.name or "(unknown)"}')
        lines.append(f'  email: {contact.email or "(unknown)"}')
        lines.append(f'  phone: {masked}')
        if contact.created_at:
            lines.append(f'  known_since: {contact.created_at.date().isoformat()}')

    lines.append('')
    lines.append(f'Past jobs (most recent first, max {PAST_JOBS_LIMIT}):')
    past_jobs: List[JobRecord] = []
    if contact is not None:
        past_jobs = list(
            JobRecord.objects.filter(organization=thread.organization, contact=contact)
            .order_by('-created_at')[:PAST_JOBS_LIMIT]
        )
    if not past_jobs:
        lines.append('  (none)')
    else:
        for idx, job in enumerate(past_jobs, start=1):
            short_id = str(job.id)[:8]
            site_part = ' / '.join(p for p in [job.community, job.site, job.project] if p)
            scheduled = job.scheduled_date.isoformat() if job.scheduled_date else '—'
            header = (
                f'  {idx}. #{short_id} — {job.service_type or "Job"} | '
                f'{site_part or "—"} | scheduled {scheduled} | status={job.status}'
            )
            lines.append(header)
            scope_text = (job.scope or '').strip()
            if scope_text:
                preview = scope_text[:SCOPE_PREVIEW_CHARS]
                if len(scope_text) > SCOPE_PREVIEW_CHARS:
                    preview += '…'
                lines.append(f'     scope: "{preview}"')

    lines.append('')
    if thread.active_job_id:
        active = thread.active_job
        lines.append(
            f'Active job: #{str(active.id)[:8]} — {active.service_type or "Job"} | '
            f'status={active.status}'
        )
    else:
        lines.append('Active job: (none)')

    state_label = thread.get_state_display()
    msg_count = thread.messages.count()
    opened_for = ''
    if thread.created_at:
        delta = timezone.now() - thread.created_at
        opened_for = f', opened {_format_delta(delta)} ago'
    lines.append(
        f'Thread: {state_label}{opened_for}, {msg_count} messages exchanged so far'
    )

    return '\n'.join(lines)


def runtime_rules_block(organization) -> str:
    """Operator-controlled extra rules; empty by default."""
    return SystemPrompt.get('sms_runtime_rules', organization=organization, default='') or ''


def _mask_phone(phone: str) -> str:
    if not phone:
        return '(unknown)'
    if len(phone) <= 4:
        return phone
    return '***' + phone[-4:]


def _format_delta(delta: datetime.timedelta) -> str:
    secs = int(delta.total_seconds())
    if secs < 60:
        return f'{secs}s'
    if secs < 3600:
        return f'{secs // 60}m'
    if secs < 86400:
        return f'{secs // 3600}h'
    return f'{secs // 86400}d'
