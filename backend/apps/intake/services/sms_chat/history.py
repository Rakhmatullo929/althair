"""Read and write ConversationMessage rows in the OpenAI message format."""

import json
import logging
from typing import Any, Dict, List, Optional

from django.db.models import Max

from intake.models import (
    ConversationMessage,
    ConversationThread,
    MessageRole,
)

logger = logging.getLogger(__name__)


def next_sequence(thread: ConversationThread) -> int:
    """Return the next sequence number for a new message in *thread*."""
    current = thread.messages.aggregate(m=Max('sequence'))['m']
    return 0 if current is None else current + 1


def load_history(thread: ConversationThread) -> List[Dict[str, Any]]:
    """Return all persisted messages in OpenAI's chat-completions format."""
    rows = list(thread.messages.order_by('sequence'))
    out: List[Dict[str, Any]] = []
    for row in rows:
        if row.role == MessageRole.USER:
            out.append({'role': 'user', 'content': row.content or ''})
        elif row.role == MessageRole.ASSISTANT:
            entry: Dict[str, Any] = {'role': 'assistant'}
            if row.content:
                entry['content'] = row.content
            else:
                entry['content'] = None
            tool_calls = row.tool_calls or []
            if tool_calls:
                entry['tool_calls'] = tool_calls
            out.append(entry)
        elif row.role == MessageRole.TOOL:
            out.append({
                'role': 'tool',
                'tool_call_id': row.tool_call_id or '',
                'content': row.content or '',
            })
    return out


def persist_user_message(thread: ConversationThread, body: str) -> ConversationMessage:
    return ConversationMessage.objects.create(
        organization=thread.organization,
        thread=thread,
        sequence=next_sequence(thread),
        role=MessageRole.USER,
        content=body or '',
    )


def persist_assistant(
    thread: ConversationThread,
    text_content: Optional[str],
    tool_calls: Optional[List[Dict[str, Any]]],
) -> ConversationMessage:
    """Persist an assistant turn. *tool_calls* is the OpenAI-shaped list."""
    return ConversationMessage.objects.create(
        organization=thread.organization,
        thread=thread,
        sequence=next_sequence(thread),
        role=MessageRole.ASSISTANT,
        content=text_content or '',
        tool_calls=tool_calls or [],
        tool_name=(tool_calls[0]['function']['name'] if tool_calls else ''),
    )


def persist_tool_result(
    thread: ConversationThread,
    tool_call_id: str,
    tool_name: str,
    payload: Dict[str, Any],
) -> ConversationMessage:
    return ConversationMessage.objects.create(
        organization=thread.organization,
        thread=thread,
        sequence=next_sequence(thread),
        role=MessageRole.TOOL,
        content=json.dumps(payload, ensure_ascii=False),
        tool_call_id=tool_call_id or '',
        tool_name=tool_name or '',
    )
