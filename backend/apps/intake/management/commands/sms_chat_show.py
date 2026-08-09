"""Print the most recent ConversationMessage rows for a phone, JSON per line.

Used by the scripts/sms_chat.sh bash REPL to display what the bot said after
each webhook call.  Output one JSON object per message so the bash side can
pretty-print it.
"""

import json

from django.core.management.base import BaseCommand

from intake.models import (
    ConversationMessage,
    ConversationThread,
    MessageRole,
)
from intake.services.sms_chat.utils import normalize_phone


class Command(BaseCommand):
    help = (
        'Print recent ConversationMessage rows for a phone as JSON-per-line. '
        'Use --since-sequence N to print only rows with sequence > N.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--phone', required=True)
        parser.add_argument('--since-sequence', type=int, default=-1)
        parser.add_argument(
            '--limit', type=int, default=20,
            help='Maximum messages to print when no --since-sequence is given.',
        )

    def handle(self, *args, **opts):
        phone = normalize_phone(opts['phone'])
        thread = (
            ConversationThread.objects
            .filter(phone=phone)
            .order_by('-last_message_at').first()
        )
        if thread is None:
            return

        qs = (
            thread.messages
            .filter(sequence__gt=opts['since_sequence'])
            .order_by('sequence')
        )
        rows = list(qs[: opts['limit']])

        for msg in rows:
            row = {
                'sequence': msg.sequence,
                'role': msg.role,
                'content': msg.content or '',
            }
            if msg.role == MessageRole.ASSISTANT and msg.tool_calls:
                row['tool_calls'] = [
                    {
                        'name': (tc.get('function') or {}).get('name', ''),
                        'arguments': _decode_args(
                            (tc.get('function') or {}).get('arguments', '{}')
                        ),
                        'id': tc.get('id', ''),
                    }
                    for tc in msg.tool_calls
                ]
            if msg.role == MessageRole.TOOL:
                row['tool_name'] = msg.tool_name
                row['tool_call_id'] = msg.tool_call_id
            self.stdout.write(json.dumps(row, ensure_ascii=False))


def _decode_args(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value
