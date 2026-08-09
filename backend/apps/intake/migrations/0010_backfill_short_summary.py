"""
Backfill / upgrade short JobRecord.summary titles.

1. Replace the default `email_short_summary` SystemPrompt with the new
   "<Trade> Issue / Request" prompt IF the stored prompt still matches the
   previous out-of-the-box text (so any manually-edited prompt is kept
   intact).

2. Rewrite `JobRecord.summary` values that are clearly raw body text
   (over 40 chars and/or starting with a greeting) to a 2-3 word title
   derived locally from `service_type` + `scope`.  No AI call is made
   inside the migration — we only run a deterministic heuristic.
"""
import re

from django.db import migrations


_OLD_SHORT_SUMMARY_PROMPT = (
    "You are a dispatcher assistant for a land management and construction company.\n"
    "Read the following service request and reply with a concise 3-4 word title that "
    "captures the type of work or issue.\n\n"
    "Rules:\n"
    "- Focus on the trade or problem type (e.g. Plumbing, Framing, HVAC, Electrical, Warranty)\n"
    "- Do NOT include the requester's name or address\n"
    "- Reply with ONLY the short title, nothing else\n\n"
    "Good examples: \"Plumbing Leak\", \"Framing Request\", \"HVAC Installation\", "
    "\"Warranty Repair\", \"Punch List\"\n\n"
    "Message:\n{text}"
)

_NEW_SHORT_SUMMARY_PROMPT = (
    "You are a dispatcher assistant for a land management and construction company.\n"
    "Read the following service request and reply with a concise 2-3 word title that "
    "captures the type of work or issue.\n\n"
    "Rules:\n"
    "- Pattern: \"<Trade> Issue\" for maintenance / repair / problem reports.\n"
    "- Pattern: \"<Trade> Request\" for new work, installs, quotes, estimates.\n"
    "- Use one of these trades when applicable: Plumbing, Electrical, HVAC, "
    "Framing, Roofing, Painting, Drywall, Flooring, Concrete, Landscaping, "
    "Foundation, Warranty, Carpentry, Masonry.\n"
    "- NEVER include the requester's greeting (\"Hello\", \"Hi\", etc.), name, or address.\n"
    "- NEVER reply with a full sentence.\n"
    "- Reply with ONLY the 2-3 word title, nothing else.\n\n"
    "Good examples: \"Plumbing Issue\", \"Electrical Issue\", \"HVAC Request\", "
    "\"Framing Request\", \"Warranty Repair\", \"Roof Leak\".\n\n"
    "Message:\n{text}"
)


_GREETING_PREFIX_RE = re.compile(
    r'^\s*(?:hi|hello|hey|dear|good\s+(?:morning|afternoon|evening)|yo|hiya|greetings)\b'
    r'[\s,!.;:\-]*',
    re.IGNORECASE,
)


_TRADE_KEYWORD_MAP = [
    ('Warranty',    ('warranty', 'punch list', 'walkthrough', 'punch-list')),
    ('HVAC',        ('hvac', 'a/c', 'ac unit', 'air condition', 'heating', 'furnace',
                     'thermostat', 'no heat', 'no cooling', 'ventilation')),
    ('Plumbing',    ('plumb', 'leak', 'pipe', 'faucet', 'toilet', 'sink', 'drain',
                     'water heater', 'clogged', 'sewer', 'garbage disposal', 'water pooling')),
    ('Electrical',  ('electric', 'outlet', 'wiring', 'breaker', 'circuit', 'lighting',
                     'power outage', 'short circuit', 'no power')),
    ('Roofing',     ('roof', 'shingle', 'gutter', 'downspout')),
    ('Framing',     ('framing', 'stud', 'rafter', 'joist')),
    ('Drywall',     ('drywall', 'sheetrock', 'wall hole', 'ceiling hole')),
    ('Painting',    ('paint', 'stain', 'touch-up', 'touch up')),
    ('Flooring',    ('flooring', 'tile', 'carpet', 'vinyl plank', 'hardwood floor')),
    ('Concrete',    ('concrete', 'sidewalk', 'driveway', 'slab')),
    ('Landscaping', ('landscap', 'sod', 'mulch', 'irrigation', 'sprinkler', 'grading')),
    ('Foundation',  ('foundation', 'footing', 'crawl space')),
    ('Carpentry',   ('carpenter', 'carpentry', 'trim work', 'baseboard')),
    ('Masonry',     ('masonry', 'brick', 'stone work')),
]

_INSTALL_HINTS = (
    'install', 'new install', 'add ', 'upgrade', 'quote', 'estimate', 'build',
    'request for', 'need new', 'would like to add',
)
_REPAIR_HINTS = (
    'repair', 'fix', 'leak', 'broken', 'damage', 'crack', 'issue', 'problem',
    'not working', 'stopped', 'clogged', 'emergency', 'urgent', 'hole',
    'not safe', 'unsafe',
)


def _strip_greetings(text: str) -> str:
    if not text:
        return ''
    lines = []
    for line in text.splitlines():
        cleaned = _GREETING_PREFIX_RE.sub('', line).strip(' ,!.:;\t')
        if cleaned:
            lines.append(cleaned)
    return '\n'.join(lines).strip()


def _pick_suffix(text_lower: str) -> str:
    if any(h in text_lower for h in _REPAIR_HINTS):
        return 'Issue'
    if any(h in text_lower for h in _INSTALL_HINTS):
        return 'Request'
    return 'Issue'


def _derive_title(service_type: str, body: str) -> str:
    body = _strip_greetings(body or '')
    body_lower = body.lower()

    if service_type:
        trade = service_type.strip()
        if trade.lower() == 'warranty':
            return 'Warranty Repair'
        return f'{trade} {_pick_suffix(body_lower)}'

    for trade, kws in _TRADE_KEYWORD_MAP:
        for kw in kws:
            if kw in body_lower:
                if trade == 'Warranty':
                    return 'Warranty Repair'
                return f'{trade} {_pick_suffix(body_lower)}'

    if 'maintenance' in body_lower:
        return 'Maintenance Request'
    return 'Service Request'


_FIRST_PERSON_RE = re.compile(
    r'^\s*(?:i|we|my|our|you|please)\b',
    re.IGNORECASE,
)


def _looks_like_raw_body(summary: str) -> bool:
    """True if a stored summary looks like it was copied from the email body.

    A healthy short title is at most ~30 chars, contains no sentence-ending
    punctuation, and does NOT start with a greeting / first-person pronoun
    (which are tell-tale signs of a full sentence being stored).
    """
    if not summary:
        return False
    s = summary.strip()
    if len(s) > 30:
        return True
    if s.endswith(('.', '!', '?')):
        return True
    if _GREETING_PREFIX_RE.match(s):
        return True
    if _FIRST_PERSON_RE.match(s):
        return True
    return False


def update_prompt_and_backfill(apps, schema_editor):
    SystemPrompt = apps.get_model('intake', 'SystemPrompt')
    JobRecord = apps.get_model('intake', 'JobRecord')

    # 1. Upgrade the prompt only if it was never customised.
    prompt = SystemPrompt.objects.filter(key='email_short_summary').first()
    if prompt and prompt.text.strip() == _OLD_SHORT_SUMMARY_PROMPT.strip():
        prompt.text = _NEW_SHORT_SUMMARY_PROMPT
        prompt.save(update_fields=['text'])

    # 2. Rewrite obviously-bad summaries.  Runs in batches to be migration-safe.
    qs = JobRecord.objects.all().only('id', 'summary', 'service_type', 'scope')
    for job in qs.iterator(chunk_size=200):
        if not _looks_like_raw_body(job.summary):
            continue
        new_title = _derive_title(
            service_type=(job.service_type or '').strip(),
            body=(job.scope or job.summary or ''),
        )
        if new_title and new_title != job.summary:
            job.summary = new_title
            job.save(update_fields=['summary'])


def noop_reverse(apps, schema_editor):
    # Reversing a textual rewrite is not meaningful — leave summaries as they are.
    return


class Migration(migrations.Migration):

    dependencies = [
        ('intake', '0009_add_message_received_event'),
    ]

    operations = [
        migrations.RunPython(update_prompt_and_backfill, noop_reverse),
    ]
