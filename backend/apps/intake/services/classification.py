import logging
import re
from typing import Optional, Tuple

from django.conf import settings

logger = logging.getLogger(__name__)

CATEGORY_IMMEDIATE_RESOLUTION = 'immediate_resolution'
CATEGORY_JOB_REQUEST_INTAKE = 'job_request_intake'
CATEGORY_REVIEW_REQUIRED = 'review_required'
CATEGORY_ESCALATION = 'escalation'

_ALL_CATEGORIES = {
    CATEGORY_IMMEDIATE_RESOLUTION,
    CATEGORY_JOB_REQUEST_INTAKE,
    CATEGORY_REVIEW_REQUIRED,
    CATEGORY_ESCALATION,
}

_JOB_KEYWORDS = [
    'job', 'work order', 'service', 'repair', 'install', 'installation',
    'fix', 'build', 'quote', 'estimate', 'request', 'need work',
    'schedule work', 'warranty', 'construction', 'scope', 'po', 'rec',
    'community', 'builder', 'lot', 'unit', 'phase', 'project',
    'punch', 'touch up', 'walkthrough', 'inspection', 'site',
]

_INFO_KEYWORDS = [
    'information', 'question', 'hours', 'location', 'price', 'cost',
    'what is', 'when do', 'how do', 'where is', 'who is',
    'status update', 'follow up', 'check on', 'confirm', 'verify',
    'just checking', 'just wondering',
]

# Messages that should bypass the normal workflow and reach a staff member directly.
#
# These trigger Category=Escalation and *suppress* JobRecord creation entirely
# (`process_email_webhook` only creates a job when category == job_request_intake).
# When you add a keyword here, also consider whether it belongs in the
# `_ESCALATION_INTENT_PATTERNS` list below — that one is used for follow-up
# escalation requests on already-open jobs, while this list governs first-touch
# classification.
_ESCALATION_KEYWORDS = [
    'emergency', 'fire', 'flood', 'gas leak', 'no heat', 'no water',
    'not safe', 'unsafe', 'call me', 'speak to', 'speak with', 'talk to',
    'manager', 'supervisor', 'complaint', 'legal', 'lawyer', 'attorney',
    'threatening', 'discrimination', 'harassment',
    'extremely frustrated', 'very upset', 'angry', 'furious',
    'escalate', 'escalation',
    # Customer-dissatisfaction signals that QA flagged as previously
    # ignored — these create complaint-style emails that should NOT
    # produce a job card and instead surface to a human reviewer.
    'unhappy', 'dissatisfied', 'disappointed', 'frustrated',
    'no one showed up', 'no-one showed up', 'nobody showed up',
    "didn't show up", 'did not show up', 'never showed up',
    'no show', 'no-show',
    'unacceptable', 'unprofessional', 'incompetent',
    'will not pay', "won't pay", 'refuse to pay',
    'cancel', 'cancellation', 'terminate contract',
    'bad service', 'poor service', 'terrible service',
]


# ── Priority detection ────────────────────────────────────────────────────────
# We INFER JobPriority from context rather than asking the user — asking is
# unreliable because every customer self-reports "urgent".  Instead we scan
# the free-text body for objective evidence: safety hazards, active emergencies
# (→ Urgent), explicit hard deadlines within the current business window
# (→ High), and deferred-work signals (→ Low).  Anything else stays Normal.
#
# Keep this conservative: a false Urgent is noisy for dispatchers, a false
# Normal is recoverable because a human still reviews every new job card.
_URGENT_PATTERNS = [
    r'\b(?:emergenc(?:y|ies)|emergent)\b',
    r'\basap\b', r'\ba\.?\s?s\.?\s?a\.?\s?p\.?\b',
    # "urgent" / "urgently" — but NOT "non-urgent" / "not urgent"
    r'(?<!non-)(?<!non\s)(?<!not\s)\burgent(?:ly)?\b',
    r'\bimmediately\b',
    r'\bright\s+(?:now|away)\b',
    r'\bflood(?:ed|ing)?\b',
    r'\b(?:gas|water|sewer|sewage)\s+leak(?:ing|s)?\b',
    r'\bsewer\s+back[\s-]?up\b',
    r'\bno\s+(?:heat|hot\s+water|power|electricity|running\s+water)\b',
    r'\bfire\s+(?:hazard|risk|damage)\b',
    r'\b(?:health|safety)\s+(?:hazard|risk|issue|concern)\b',
    r'\blife[\s-]?threatening\b',
    r'\bstructural\s+(?:damage|failure|collapse)\b',
    r'\b(?:ceiling|wall|roof)\s+(?:collapse|falling)\b',
    r'\b(?:active|ongoing|in\s+progress)\s+water\s+damage\b',
    r'\bwater\s+pouring\b',
    r'\bgushing\s+water\b',
    r'\bcarbon\s+monoxide\b',
    r'\blive\s+(?:wire|electrical)\b',
    r'\bsparking\s+outlet\b',
    r'\bexposed\s+wir(?:e|ing)\b',
]

_HIGH_PATTERNS = [
    r'\bas\s+soon\s+as\s+possible\b',
    r'\btop\s+priority\b', r'\bhigh\s+priority\b',
    # Explicit labelled priority field — "Priority: High" / "Priority - Urgent"
    r'\bpriority\s*[:\-]\s*(?:high|urgent|asap|critical|top)\b',
    r'\b(?:end|close)\s+of\s+(?:day|week|business)\b',
    r'\b(?:eod|eob|cob)\b',
    r'\bby\s+(?:today|tonight|tomorrow|end\s+of\s+day)\b',
    r'\btoday\s+(?:if\s+possible|please|would\s+be\s+great)\b',
    # "… out today" / "come out today" — single-day ask is high priority.
    r'\b(?:come|out|stop)\s+(?:out\s+)?today\b',
    r'\btime[\s-]?sensitive\b',
    # "need (it/this/that) (by/before/on) <near-term>"
    # Allow optional ":" / "-" / "—" after the preposition so labelled
    # fields like "Needed by: Thursday" trigger too.
    r'\bneed(?:ed|s)?\s+(?:it|this|that|them|us|that\s+done)?\s*'
    r'(?:by|before|on|to\s+be\s+(?:done|completed)\s+by)\s*[:\-\u2013\u2014]?\s*'
    r'(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|'
    r'this\s+week|tomorrow|today|tonight|end\s+of\s+(?:day|week)|'
    r'mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b',
    # "need it done by Thursday" / "need done Thursday"
    r'\bdone\s+(?:by|before|on)\s*[:\-\u2013\u2014]?\s*'
    r'(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|'
    r'tomorrow|today|tonight|end\s+of\s+(?:day|week))\b',
    # Labelled deadline: "Needed by: Thursday" / "Deadline - Friday" (note the
    # regex above also handles this but we keep a simpler alternative that
    # fires even when the preposition is itself the label, e.g.
    # "Deadline: Thursday" with no "need/needed" verb).
    r'\b(?:needed\s+by|deadline|timeline|due\s+(?:by|on)?|required\s+by)'
    r'\s*[:\-\u2013\u2014]\s*'
    r'(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|'
    r'tomorrow|today|tonight|this\s+(?:week|morning|afternoon|evening)|'
    r'end\s+of\s+(?:day|week)|eod|cob|eob|asap)\b',
    # "inspection Friday" / "inspection on Monday" / "inspection tomorrow"
    r'\binspection\s+(?:today|tomorrow|tonight|this\s+(?:week|morning|afternoon|evening)|'
    r'(?:on\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b',
    r'\bquickly\b', r'\bin\s+a\s+hurry\b',
    r'\bsame[\s-]day\b', r'\bovernight\b',
    r'\bfirst\s+thing\s+(?:tomorrow|in\s+the\s+morning|today)\b',
    # "rush" / "rush job" — but NOT "no rush"
    r'(?<!no\s)\brush(?:\s+job)?\b',
    r'\bexpedit(?:e|ed|ing)\b',
]

_LOW_PATTERNS = [
    r'\bno\s+rush\b',
    r'\bno\s+hurry\b',
    r'\bwhen(?:\s+you)?\s+(?:have\s+time|get\s+a\s+chance|are\s+available|can)\b',
    r'\bwhenever\s+(?:you\s+can|works|is\s+convenient)\b',
    r'\bat\s+your\s+(?:convenience|leisure)\b',
    r'\bany[\s-]?time\s+(?:works|is\s+fine|this\s+month)\b',
    r'\blow\s+priority\b',
    r'\bnon[\s-]?urgent\b',
    r'\bnext\s+(?:month|quarter|year)\b',
    r'\beventually\b',
    r'\bsometime\s+(?:next|later)\b',
]

_URGENT_RE = re.compile('|'.join(_URGENT_PATTERNS), re.IGNORECASE)
_HIGH_RE = re.compile('|'.join(_HIGH_PATTERNS), re.IGNORECASE)
_LOW_RE = re.compile('|'.join(_LOW_PATTERNS), re.IGNORECASE)

# Priority string constants mirror intake.models.JobPriority to avoid the
# circular import of pulling that model in here.
PRIORITY_LOW = 'low'
PRIORITY_NORMAL = 'normal'
PRIORITY_HIGH = 'high'
PRIORITY_URGENT = 'urgent'


# ── Conversational intent detection (post-ticket follow-ups) ─────────────────
# When a customer is already on an active job thread, every follow-up SMS used
# to be blindly merged into the job's timeline, so asks like "please call a
# team member" or "I want to open a new ticket" were answered with the generic
# "we've added your message" reply.  These two detectors spot those intents
# cheaply and deterministically (no LLM round-trip) so the branching logic in
# the SMS service can escalate or open a fresh thread when appropriate.
#
# Both detectors are a strict superset of each other's concerns:
#   • `detect_escalation_intent` fires on explicit requests for a human
#     (call me, speak to a manager, representative …) and on the same
#     high-signal escalation keywords used by classify_text() on the first
#     message of a thread.  Also catches rage bursts where the customer
#     repeats the escalation request in ALL-CAPS.
#   • `detect_new_ticket_intent` fires when the customer explicitly wants
#     a separate request ("new ticket", "another issue", "open a new case",
#     "start over" …).  Enrichment phrasing such as "also add X" / "please
#     note that" does NOT trigger — those stay as normal enrichments.
_ESCALATION_INTENT_PATTERNS = [
    # "call me" / "call a team member" / "call the office / manager / supervisor"
    r'\bcall\s+(?:me\s+)?'
    r'(?:a\s+|the\s+)?'
    r'(?:team\s+member|human|real\s+person|person|'
    r'manager|supervisor|representative|rep|agent|someone|office|'
    r'customer\s+service|live\s+person)\b',
    r'\bcall\s+me\s+(?:back|please|asap|right\s+now|now|directly)\b',
    r'\bcall\s+me\b',
    # "speak to / talk to / talk with ..."
    r'\b(?:speak|talk|chat)\s+(?:to|with)\s+(?:a\s+|the\s+)?'
    r'(?:human|real\s+person|person|manager|supervisor|representative|rep|'
    r'agent|team\s+member|someone|live\s+(?:person|agent))\b',
    r'\bneed\s+to\s+(?:speak|talk)\b',
    r'\bwant\s+to\s+(?:speak|talk)\s+to\b',
    # "(get|have) a human"
    r'\b(?:get|have|need|want)\s+(?:a\s+)?(?:human|real\s+person|live\s+person)\b',
    # "transfer me" / "connect me"
    r'\b(?:transfer|connect|put)\s+me\s+(?:to|through)\b',
    # "contact me directly / asap"
    r'\bcontact\s+me\s+(?:directly|asap|right\s+away|immediately|please|now)\b',
    # Sentence-level escalation asks
    r'\b(?:please\s+)?(?:escalate|escalation)\b',
    r'\bmanager\s+please\b',
    r'\bi\s+want\s+to\s+(?:file\s+a\s+)?complaint\b',
    r'\bfile\s+a\s+complaint\b',
    # Strong frustration / dissatisfaction signals — these ALWAYS need a
    # human even if the customer didn't explicitly ask for one.
    r'\b(?:i\s+am|i\'?m)\s+(?:so\s+|really\s+|extremely\s+|very\s+)?'
    r'(?:frustrated|upset|angry|furious|pissed(?:\s+off)?|mad|annoyed|fed\s+up)\b',
    r'\bthis\s+is\s+(?:ridiculous|unacceptable|absurd|insane|crazy|outrageous)\b',
    r'\b(?:are\s+you|r\s+u)\s+(?:kidding|serious|joking)\b',
    r'\bwhat\s+a\s+joke\b',
    r'\bworst\s+(?:service|experience|company)\b',
    r'\bnever\s+(?:again|using\s+you)\b',
    # Repeated asks ("again" + frustration) — customer asked before and
    # didn't get help.
    r'\b(?:i\'?ve|i\s+have)\s+(?:already|been)\s+(?:asked|trying|waiting|told)\b',
    r'\b(?:still|still\s+no)\s+(?:no\s+answer|response|help|update)\b',
    # Litigation / regulator threats
    r'\b(?:bbb|better\s+business\s+bureau|attorney\s+general|small\s+claims)\b',
    r'\b(?:i\'?ll|i\s+will|going\s+to)\s+(?:sue|take\s+legal\s+action|report\s+you)\b',
]

_ESCALATION_INTENT_RE = re.compile(
    '|'.join(_ESCALATION_INTENT_PATTERNS),
    re.IGNORECASE,
)


def detect_escalation_intent(text: str) -> bool:
    """
    Return True if the SMS body is asking for human contact / manager
    escalation (rather than just adding information).

    Triggers on direct phrasing ("please call a team member",
    "speak to a manager", "transfer me") and on the high-signal keywords
    already used by `classify_text()` for first-message escalation.
    Case-insensitive; also fires on ALL-CAPS rage bursts like
    "NO CALL A TEAM MEMBER!!!".
    """
    if not text or not text.strip():
        return False

    if _ESCALATION_INTENT_RE.search(text):
        return True

    # Fall back to the existing escalation keyword list so we stay
    # consistent with first-message classification.
    text_lower = text.lower()
    for kw in _ESCALATION_KEYWORDS:
        if kw in text_lower:
            return True

    return False


# New-ticket intent: phrasing that signals a DIFFERENT request, not an
# enrichment of the currently-active one.  Kept conservative — "also add X"
# and "update: …" stay as enrichments.
_NEW_TICKET_PATTERNS = [
    # "open/start/create a new ticket / request / case / job / order"
    r'\b(?:open|start|create|file|submit|log|raise)\s+'
    r'(?:up\s+)?(?:a\s+)?(?:new\s+|another\s+|second\s+|separate\s+)?'
    r'(?:ticket|request|case|job|order|issue)\b',
    # "I want/need a new ticket/request..."
    r'\b(?:i\s+)?(?:want|need|would\s+like)\s+(?:to\s+)?'
    r'(?:open\s+|start\s+|create\s+|file\s+|submit\s+|log\s+|raise\s+)?'
    r'(?:a\s+)?(?:new\s+|another\s+|second\s+|separate\s+|different\s+)'
    r'(?:ticket|request|case|job|order|issue)\b',
    # "new ticket / new request / new job / new issue" (standalone)
    r'\bnew\s+(?:ticket|request|case|job|order)\b',
    # "another ticket / separate issue / different request"
    r'\banother\s+(?:ticket|request|case|job|issue|problem)\b',
    r'\bseparate\s+(?:ticket|request|case|job|issue|problem)\b',
    r'\bdifferent\s+(?:ticket|request|case|job|issue|problem)\b',
    r'\bunrelated\s+(?:ticket|request|case|job|issue|problem)\b',
    r'\bstart\s+over\b',
]

_NEW_TICKET_INTENT_RE = re.compile(
    '|'.join(_NEW_TICKET_PATTERNS),
    re.IGNORECASE,
)


def detect_new_ticket_intent(text: str) -> bool:
    """
    Return True if the SMS body explicitly asks to open a separate
    request rather than add info to the existing one.
    """
    if not text or not text.strip():
        return False
    return bool(_NEW_TICKET_INTENT_RE.search(text))


# ── Vague request detection ───────────────────────────────────────────────────
# Phrases that pass the basic `_is_meaningful_scope` length / word checks but
# carry no actionable content — a dispatcher cannot turn "Something is going
# on" into a service order.  We use this to *block* job creation even when
# other heuristics say the message looks job-like, so vague reports land in
# the Needs Review queue instead of becoming bogus job cards.
#
# Each entry is matched whole-phrase, case-insensitive, with normalised
# whitespace.  Keep them tight: a too-broad match would suppress real
# requests like "I'm having an issue with my driveway sealing".
_VAGUE_PHRASES = (
    'something is going on', 'something is wrong', 'something is happening',
    "something's going on", "something's wrong",
    'something happening', 'something going on',
    'something is up', 'something seems off',
    'there is an issue', "there's an issue", 'there is a problem',
    "there's a problem",
    'we have an issue', 'i have an issue', 'we have a problem',
    'i have a problem',
    'need help', 'please help', 'can you help', 'help us',
    'need assistance', 'need some help',
    'just checking', 'just wondering', 'just curious',
    'fyi', 'heads up', 'quick question',
    'not sure what', 'not really sure', 'no idea what',
)

_VAGUE_PHRASE_RE = re.compile(
    r'^\s*'
    # Optional greeting prefix — "hi", "hello team,", "dear staff,".  The
    # three branches cover (a) bare greetings followed by punctuation/space,
    # (b) "Dear …," forms, and (c) plain "hi <name>".
    r'(?:'
        r'(?:hi|hello|hey|good\s+(?:morning|afternoon|evening))'
        r'[\s,.!]+(?:[A-Za-z]+[\s,.!]+)?'
        r'|dear[^,]*,\s*'
    r')?'
    r'(?:' + '|'.join(re.escape(p) for p in _VAGUE_PHRASES) + r')'
    r'[\s.!?,…]*$',                              # optional trailing punctuation only
    re.IGNORECASE,
)


def is_vague_request(text: str) -> bool:
    """Return True when the message is a placeholder phrase with no actionable
    detail (e.g. "Something is going on", "Need help please").

    Used by the email intake's sufficiency gate to keep vague reports out of
    the job queue — they go to Needs Review instead.

    Conservative on purpose: any concrete signal (a trade keyword, a
    quantity, a location, a deadline word) co-occurring in the message
    means the text isn't vague — even if a vague phrase was used as a
    lead-in.  This is enforced at the call site, not here.
    """
    if not text:
        return False
    # Collapse internal whitespace so "Something  is\tgoing\non" still matches.
    flat = re.sub(r'\s+', ' ', text).strip()
    if not flat:
        return False
    return bool(_VAGUE_PHRASE_RE.match(flat))


# ── Lot / phase range normalisation ───────────────────────────────────────────
# Customers express the same range three different ways:
#   "Lots 14 through 22"   /   "Lots 14-22"   /   "Lots 14–22"
# We normalise them to a single canonical form ("Lots 14–22", en-dash) so
# downstream search / dedupe / display stays consistent.  Single-lot values
# ("Lot 14", "Lot 14B") are returned unchanged.
_LOT_RANGE_RE = re.compile(
    r'\b(lots?)\s+'
    r'(\d+[A-Za-z]?)\s*'
    r'(?:through|thru|-|–|—|to)\s*'
    r'(\d+[A-Za-z]?)\b',
    re.IGNORECASE,
)


def normalize_lot_phase(value: str) -> str:
    """Canonicalise lot ranges to "Lots N–M" (en-dash).

    Examples
    ────────
        "Lots 14 through 22" → "Lots 14–22"
        "lot 14-22"          → "Lots 14–22"   (also pluralised)
        "Lots 7B-12A"        → "Lots 7B–12A"
        "Lot 14"             → "Lot 14"        (single lot, unchanged)
        "Phase 3 Lots 30-45" → "Phase 3 Lots 30–45"
    """
    if not value:
        return value

    def _replace(m: 're.Match[str]') -> str:
        start = m.group(2)
        end = m.group(3)
        # Always pluralise the label when there's a range — "Lot 14-22" reads
        # awkwardly; "Lots 14–22" is the field label we want.
        return f'Lots {start}–{end}'

    out = _LOT_RANGE_RE.sub(_replace, value)
    return re.sub(r'\s{2,}', ' ', out).strip()


def detect_priority(text: str, urgency_notes: str = '') -> str:
    """
    Infer a JobPriority value from the free-text context.

    Returns one of 'low' | 'normal' | 'high' | 'urgent' (matching the
    `intake.models.JobPriority.value` strings).

    Rules, evaluated in order:
      1. Any Urgent signal (active emergency / safety hazard / severe keyword)
         → 'urgent'.
      2. Any explicit Low signal ("no rush", "whenever", "non-urgent",
         "next month") that is NOT overridden by Urgent → 'low'.
         Low is checked BEFORE High so that isolated HIGH keywords that
         appear inside a phrase like "no rush on this — whenever you have
         time" don't falsely upgrade the priority.
      3. Any High signal (hard deadline this week, ASAP-equivalents,
         "needed by <weekday>", scheduled inspection within days)
         → 'high'.
      4. Otherwise → 'normal'.

    Safe by design: a blank or meaningless input returns 'normal' — we never
    upgrade without evidence.
    """
    combined = ' '.join(filter(None, [(text or '').strip(), (urgency_notes or '').strip()]))
    if not combined:
        return PRIORITY_NORMAL

    if _URGENT_RE.search(combined):
        return PRIORITY_URGENT
    if _LOW_RE.search(combined):
        return PRIORITY_LOW
    if _HIGH_RE.search(combined):
        return PRIORITY_HIGH
    return PRIORITY_NORMAL


def classify_text(text: str, *, organization=None) -> Tuple[str, float, str]:
    if not text or not text.strip():
        return CATEGORY_REVIEW_REQUIRED, 0.0, 'no-content'

    if _llm_available():
        try:
            result = _classify_with_llm(text, organization=organization)
            if result:
                return result
        except Exception as exc:
            logger.warning(
                'LLM classification failed (%s); falling back to heuristics',
                type(exc).__name__,
            )

    return _classify_heuristic(text)


def _classify_heuristic(text: str) -> Tuple[str, float, str]:
    text_lower = text.lower()

    # Escalation check runs first — highest priority.
    esc_hits = sum(1 for kw in _ESCALATION_KEYWORDS if kw in text_lower)
    if esc_hits >= 1:
        return CATEGORY_ESCALATION, min(0.65 + esc_hits * 0.10, 0.95), f'escalation-keywords({esc_hits})'

    job_hits = sum(1 for kw in _JOB_KEYWORDS if kw in text_lower)
    info_hits = sum(1 for kw in _INFO_KEYWORDS if kw in text_lower)

    if job_hits >= 3:
        confidence = min(0.60 + job_hits * 0.05, 0.95)
        return CATEGORY_JOB_REQUEST_INTAKE, confidence, f'job-keywords({job_hits})'

    if job_hits >= 1 and job_hits >= info_hits:
        return CATEGORY_REVIEW_REQUIRED, 0.45, f'weak-job-keywords({job_hits})'

    if info_hits >= 2:
        confidence = min(0.60 + info_hits * 0.05, 0.90)
        return CATEGORY_IMMEDIATE_RESOLUTION, confidence, f'info-keywords({info_hits})'

    if info_hits == 1:
        return CATEGORY_IMMEDIATE_RESOLUTION, 0.50, 'single-info-keyword'

    return CATEGORY_REVIEW_REQUIRED, 0.30, 'no-clear-category'


def _llm_available() -> bool:
    return bool(
        getattr(settings, 'OPENAI_API_KEY', '') or
        getattr(settings, 'GEMINI_API_KEY', '')
    )


def _classify_with_llm(text: str, *, organization=None) -> Optional[Tuple[str, float, str]]:
    # Pull the classification prompt from Django Admin (SystemPrompt) if present.
    # We avoid Python's str.format() here because prompt bodies often contain
    # JSON braces `{ ... }`, which would need escaping; we only substitute the
    # special `{text}` placeholder.
    default_template = (
        "You are a work-order intake classifier for a land management / construction company.\n"
        "Classify the following message into exactly one category:\n"
        "  - immediate_resolution  (informational, no job needed)\n"
        "  - job_request_intake    (service request, repair, install, work order)\n"
        "  - review_required       (ambiguous, complex, or unclear)\n"
        "  - escalation            (emergency, safety issue, emotionally distressed sender, "
        "legal threat, complaint requiring manager, or anything outside normal scope)\n\n"
        "Reply with ONLY a JSON object: "
        '{"category": "<category>", "confidence": <0.0-1.0>, "reason": "<brief reason>"}\n\n'
        "Message: {text}"
    )
    try:
        from intake.models import SystemPrompt  # local import — avoids circular import
        template = SystemPrompt.get(
            'classification', organization=organization, default=default_template,
        ) or default_template
    except Exception:
        template = default_template

    prompt = (template or default_template).replace('{text}', (text or '')[:1000])

    openai_key = getattr(settings, 'OPENAI_API_KEY', '')
    if openai_key:
        return _classify_openai(prompt, openai_key)

    gemini_key = getattr(settings, 'GEMINI_API_KEY', '')
    if gemini_key:
        return _classify_gemini(prompt, gemini_key)

    return None


def _classify_openai(prompt: str, api_key: str) -> Optional[Tuple[str, float, str]]:
    import json
    import openai

    client = openai.OpenAI(api_key=api_key, timeout=5.0)
    resp = client.chat.completions.create(
        model='gpt-4o-mini',
        messages=[{'role': 'user', 'content': prompt}],
        max_tokens=120,
        temperature=0,
    )
    raw = resp.choices[0].message.content.strip()
    data = json.loads(raw)
    category = data.get('category', CATEGORY_REVIEW_REQUIRED)
    if category not in _ALL_CATEGORIES:
        category = CATEGORY_REVIEW_REQUIRED
    return category, float(data.get('confidence', 0.5)), str(data.get('reason', 'llm-openai'))


def _classify_gemini(prompt: str, api_key: str) -> Optional[Tuple[str, float, str]]:
    import json
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    resp = model.generate_content(prompt, request_options={'timeout': 5})
    raw = resp.text.strip()
    if raw.startswith('```'):
        raw = raw.split('```')[1].lstrip('json').strip()
    data = json.loads(raw)
    category = data.get('category', CATEGORY_REVIEW_REQUIRED)
    if category not in _ALL_CATEGORIES:
        category = CATEGORY_REVIEW_REQUIRED
    return category, float(data.get('confidence', 0.5)), str(data.get('reason', 'llm-gemini'))


# ── Short-summary generation ──────────────────────────────────────────────────

_DEFAULT_SHORT_SUMMARY_PROMPT = (
    "You are a dispatcher assistant for a land management and construction company.\n"
    "Read the following service request and reply with a concise 2-3 word title that "
    "captures the type of work or issue.\n\n"
    "Rules (in priority order):\n"
    "1. If the text explicitly names a specific activity — e.g. 'street sweeping', "
    "'tree removal', 'snow removal', 'pressure washing', 'window cleaning', "
    "'trash pickup', 'lawn mowing', 'gutter cleaning', 'carpet cleaning', "
    "'duct cleaning', 'power washing', 'sod install', 'irrigation repair', "
    "'fence repair' — use that activity VERBATIM (title-cased) as the title. "
    "Example: 'Street Sweeping', 'Tree Removal'. Do NOT bucket it into a "
    "generic trade such as 'Landscaping'.\n"
    "2. Otherwise, fall back to a trade bucket using this pattern:\n"
    "   - \"<Trade> Issue\" for maintenance / repair / problem reports.\n"
    "   - \"<Trade> Request\" for new work, installs, quotes, estimates.\n"
    "   Allowed trades: Plumbing, Electrical, HVAC, Framing, Roofing, Painting, "
    "Drywall, Flooring, Concrete, Landscaping, Foundation, Warranty, "
    "Carpentry, Masonry.\n"
    "3. NEVER include the requester's greeting (\"Hello\", \"Hi\", etc.), name, "
    "or address.\n"
    "4. NEVER reply with a full sentence.\n"
    "5. Reply with ONLY the 2-3 word title, nothing else.\n\n"
    "Good examples: 'Street Sweeping', 'Tree Removal', 'Plumbing Issue', "
    "'Electrical Issue', 'HVAC Request', 'Framing Request', 'Warranty Repair', "
    "'Roof Leak'.\n\n"
    "Message:\n{text}"
)


# ── Heuristic (no-AI) short-title generation ──────────────────────────────────

_GREETING_PREFIX_RE = re.compile(
    r'^\s*(?:hi|hello|hey|dear|good\s+(?:morning|afternoon|evening)|yo|hiya|greetings)\b'
    r'[\s,!.;:\-]*',
    re.IGNORECASE,
)

# Specific named activities that should win *verbatim* over the coarser trade
# bucket.  Each entry is (canonical_title, regex) — regex is a word-boundary
# pattern applied to the lower-cased body.  Canonical title is what we emit.
#
# Rule of thumb: if an operator would normally scribble this phrase on the
# job card ("Street Sweeping"), we should not bucket it into "Landscaping".
_SPECIFIC_ACTIVITIES = [
    ('Street Sweeping',    re.compile(r'\bstreet\s+(?:sweep|sweeping|cleaning)\b', re.IGNORECASE)),
    ('Tree Removal',       re.compile(r'\btree\s+(?:removal|remove|cut(?:ting)?|trim(?:ming)?)\b', re.IGNORECASE)),
    ('Stump Removal',      re.compile(r'\bstump\s+(?:removal|remove|grind(?:ing)?)\b', re.IGNORECASE)),
    ('Snow Removal',       re.compile(r'\b(?:snow\s+(?:removal|plow(?:ing)?)|plow\s+snow)\b', re.IGNORECASE)),
    ('Pressure Washing',   re.compile(r'\b(?:pressure|power)\s+wash(?:ing)?\b', re.IGNORECASE)),
    ('Window Cleaning',    re.compile(r'\bwindow\s+(?:cleaning|washing)\b', re.IGNORECASE)),
    ('Gutter Cleaning',    re.compile(r'\bgutter\s+cleaning\b', re.IGNORECASE)),
    ('Trash Pickup',       re.compile(r'\b(?:trash|garbage|debris)\s+(?:pickup|pick-?up|removal|haul(?:ing)?)\b', re.IGNORECASE)),
    ('Lawn Mowing',        re.compile(r'\b(?:lawn\s+mow(?:ing)?|grass\s+cut(?:ting)?|mow\s+(?:the\s+)?lawn)\b', re.IGNORECASE)),
    ('Carpet Cleaning',    re.compile(r'\bcarpet\s+cleaning\b', re.IGNORECASE)),
    ('Duct Cleaning',      re.compile(r'\bduct\s+cleaning\b', re.IGNORECASE)),
    ('Sod Install',        re.compile(r'\bsod\s+(?:install(?:ation)?|replacement|lay(?:ing)?)\b', re.IGNORECASE)),
    # Covers both "irrigation repair" and prose like "irrigation system is
    # broken" / "sprinkler heads leaking" — we allow up to 3 intervening words
    # between the subject and the damage verb.
    ('Irrigation Repair',  re.compile(
        r'\b(?:irrigation|sprinkler(?:s|\s+heads?|\s+system)?)'
        r'(?:\s+\w+){0,3}?\s+'
        r'(?:repair|fix|broken|leak(?:ing|s)?|not\s+working|spraying)\b',
        re.IGNORECASE,
    )),
    ('Fence Repair',       re.compile(r'\bfence\s+(?:repair|fix|broken|damaged?)\b', re.IGNORECASE)),
    ('Fence Install',      re.compile(r'\bfence\s+(?:install(?:ation)?|new)\b', re.IGNORECASE)),
    ('Debris Cleanup',     re.compile(r'\bdebris\s+(?:cleanup|clean-?up|removal)\b', re.IGNORECASE)),
    ('Leaf Removal',       re.compile(r'\bleaf\s+(?:removal|blow(?:ing)?|cleanup)\b', re.IGNORECASE)),
    # Civil / sitework activities — common MMC Land Management requests.
    ('Erosion Control',    re.compile(r'\berosion\s+control\b', re.IGNORECASE)),
    ('Silt Fence Install', re.compile(r'\bsilt\s+fence\b', re.IGNORECASE)),
    ('Filter Sock Install',re.compile(r'\bfilter\s+sock(?:s)?\b', re.IGNORECASE)),
    ('Inlet Protection',   re.compile(r'\binlet\s+protection(?:s)?\b', re.IGNORECASE)),
    ('Grading',            re.compile(r'\b(?:rough|fine|final|site)\s+grading\b', re.IGNORECASE)),
    ('Hydroseeding',       re.compile(r'\bhydro[\-\s]?seed(?:ing)?\b', re.IGNORECASE)),
    ('Drainage Repair',    re.compile(r'\bdrainage\s+(?:repair|fix|issue|problem)\b', re.IGNORECASE)),
    ('Mulch Install',      re.compile(r'\bmulch\s+(?:install(?:ation)?|delivery|top[\-\s]?off)\b', re.IGNORECASE)),
]


# Loose co-occurrence checks — when the body mentions a system ("irrigation")
# and a damage verb ("broken", "leaking") anywhere within the same paragraph
# we still classify the trade, even if there are many words between them.
_COOCCURRENCE_ACTIVITIES: list[tuple[str, re.Pattern, re.Pattern]] = [
    (
        'Irrigation Repair',
        re.compile(r'\b(?:irrigation|sprinkler)s?\b', re.IGNORECASE),
        re.compile(r'\b(?:broken|break(?:ing)?|leak(?:ing|s)?|spraying|'
                   r'not\s+working|malfunction(?:ing)?|fail(?:ing|ure)?|'
                   r'damaged?|needs?\s+repair|needs?\s+fix(?:ing)?)\b',
                   re.IGNORECASE),
    ),
    (
        'Drainage Repair',
        re.compile(r'\b(?:drain(?:age|s)?|storm\s+drain|catch\s+basin)\b', re.IGNORECASE),
        re.compile(r'\b(?:clog(?:ged|ging)?|block(?:ed|age)?|overflow(?:ing)?|'
                   r'backed\s+up|flooding|broken|damaged?)\b', re.IGNORECASE),
    ),
]


def _detect_specific_activity(text_lower: str) -> Optional[str]:
    """Return a canonical activity title like 'Street Sweeping' if the body
    explicitly names one of the activities above, else None."""
    if not text_lower:
        return None
    for title, pattern in _SPECIFIC_ACTIVITIES:
        if pattern.search(text_lower):
            return title
    # Fallback: co-occurrence within the same body (the strict patterns above
    # require subject + verb to sit next to each other, which misses prose
    # like "the irrigation system on the main entrance is broken").
    for title, subject_re, verb_re in _COOCCURRENCE_ACTIVITIES:
        if subject_re.search(text_lower) and verb_re.search(text_lower):
            return title
    return None


_TRADE_KEYWORD_MAP = [
    # Order matters — more specific trades win over generic ones.
    ('Warranty',    ('warranty', 'punch list', 'walkthrough', 'punch-list')),
    # Civil / sitework trades come up a lot in land-management requests
    # ("erosion control", "silt fence", "filter sock", "inlet protection").
    # Listed high so they win over generic Landscaping for the same body.
    ('Sitework',    ('erosion control', 'erosion', 'silt fence', 'filter sock',
                     'inlet protection', 'stormwater', 'sediment control',
                     'site work', 'sitework', 'bmp install', 'stabilization')),
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


def _detect_trade(text_lower: str) -> Optional[str]:
    for trade, keywords in _TRADE_KEYWORD_MAP:
        for kw in keywords:
            if kw in text_lower:
                return trade
    return None


def _pick_suffix(text_lower: str, fallback: str = 'Issue') -> str:
    # Repair wins over install when both appear — repair is the 90% case.
    if any(h in text_lower for h in _REPAIR_HINTS):
        return 'Issue'
    if any(h in text_lower for h in _INSTALL_HINTS):
        return 'Request'
    return fallback


def _strip_greetings(text: str) -> str:
    """Remove common greetings from the start of every line."""
    if not text:
        return ''
    cleaned_lines = []
    for line in text.splitlines():
        stripped = _GREETING_PREFIX_RE.sub('', line).strip(' ,!.:;\t')
        if stripped:
            cleaned_lines.append(stripped)
    return '\n'.join(cleaned_lines).strip()


def _heuristic_short_title(text: str, service_type: str = '') -> str:
    """
    Derive a 2-3 word title without any AI.

    Priority:
      1. Specific named activity in the body ("Street Sweeping", "Tree Removal", …)
         — wins over both service_type and trade bucket because operators want
         the *actual* work on the job card, not the broad trade.
      2. service_type + smart suffix  ("Plumbing Issue" / "Framing Request")
      3. Trade keyword detected in body + smart suffix
      4. Short generic fallback — never a raw greeting sentence.
    """
    body = _strip_greetings(text or '')
    body_lower = body.lower()

    # 1. Named activity always wins — "Street Sweeping" beats "Landscaping Request".
    activity = _detect_specific_activity(body_lower)
    if activity:
        return activity

    if service_type:
        trade = service_type.strip()
        if trade.lower() == 'warranty':
            return 'Warranty Repair'
        return f'{trade} {_pick_suffix(body_lower)}'

    trade = _detect_trade(body_lower)
    if trade:
        if trade == 'Warranty':
            return 'Warranty Repair'
        return f'{trade} {_pick_suffix(body_lower)}'

    # No trade detectable — generic but never the raw greeting sentence.
    if any(h in body_lower for h in _REPAIR_HINTS):
        return 'Service Request'
    if any(h in body_lower for h in _INSTALL_HINTS):
        return 'Service Request'
    if 'maintenance' in body_lower:
        return 'Maintenance Request'

    # Absolute last resort — take the first short phrase of the de-greeted body.
    first_line = body.split('\n')[0].strip(' ,.!:')
    if first_line and len(first_line) <= 40:
        return first_line
    if first_line:
        words = first_line.split()
        return ' '.join(words[:4])[:40].strip(' ,.')
    return 'Service Request'


def get_short_summary(
    text: str,
    service_type: str = '',
    *,
    organization=None,
    prompt_key: str = 'email_short_summary',
) -> str:
    """
    Return a concise 2-3 word title for a service request.

    Priority:
      1. Named activity detected in the body  ("Street Sweeping", "Tree Removal", …)
         — deterministic and verbatim; operators want the *actual* work on the
         job card, not a broad trade bucket.
      2. Strong heuristic based on service_type — cheap, no AI, always available.
      3. AI call (OpenAI / Gemini) with a prompt that enforces the "<Trade> Issue"
         format, used when more nuance is needed and a key is configured.
      4. Heuristic fallback based on the body text — never a raw greeting sentence.
    """
    # 1. Specific named activity wins outright — applies even when an LLM key
    #    is configured, because LLMs routinely bucket "street sweeping" into
    #    "Landscaping Request" despite the prompt.
    body_lower = (text or '').lower()
    activity = _detect_specific_activity(body_lower)
    if activity:
        return activity

    # 2. If we already know the trade, build the title locally — fast + deterministic.
    if service_type:
        return _heuristic_short_title(text or '', service_type=service_type)

    if not text or not text.strip():
        return ''

    # 2. AI-based (only if an API key is configured).  Returns something like
    #    "Plumbing Issue" or "HVAC Request" per the prompt above.
    if _llm_available():
        try:
            from intake.models import SystemPrompt  # local import — avoids circular import
            prompt_template = SystemPrompt.get(
                prompt_key, organization=organization, default='',
            ) if prompt_key else ''
        except Exception:
            prompt_template = ''
        # Back-compat: if the channel-specific key isn't set, fall back to the
        # existing `email_short_summary` and then to the hard-coded default.
        if not (prompt_template or '').strip():
            try:
                from intake.models import SystemPrompt  # noqa: F811
                prompt_template = SystemPrompt.get(
                    'email_short_summary',
                    organization=organization,
                    default=_DEFAULT_SHORT_SUMMARY_PROMPT,
                )
            except Exception:
                prompt_template = _DEFAULT_SHORT_SUMMARY_PROMPT

        # Same substitution rule as classification: only replace `{text}` so
        # staff can paste JSON examples without escaping braces.
        prompt = (prompt_template or _DEFAULT_SHORT_SUMMARY_PROMPT).replace('{text}', (text or '')[:600])

        openai_key = getattr(settings, 'OPENAI_API_KEY', '')
        if openai_key:
            try:
                result = _summarize_openai(prompt, openai_key)
                if result:
                    return _sanitize_ai_title(result)
            except Exception as exc:
                logger.warning('Short-summary OpenAI call failed: %s', type(exc).__name__)

        gemini_key = getattr(settings, 'GEMINI_API_KEY', '')
        if gemini_key:
            try:
                result = _summarize_gemini(prompt, gemini_key)
                if result:
                    return _sanitize_ai_title(result)
            except Exception as exc:
                logger.warning('Short-summary Gemini call failed: %s', type(exc).__name__)

    # 3. Body-based heuristic — always skips greetings.
    return _heuristic_short_title(text)


def _sanitize_ai_title(raw: str) -> str:
    """Trim, strip quotes / trailing punctuation, cap length, reject empty output."""
    if not raw:
        return ''
    cleaned = raw.strip().strip('"\'').strip(' .!?,:;')
    cleaned = re.sub(r'\s+', ' ', cleaned)
    # An LLM occasionally ignores instructions and returns a sentence.  If the
    # result is longer than ~40 chars, trim to the first 4 words.
    if len(cleaned) > 40:
        cleaned = ' '.join(cleaned.split()[:4])
    return cleaned[:60]


def _summarize_openai(prompt: str, api_key: str) -> Optional[str]:
    import openai
    client = openai.OpenAI(api_key=api_key, timeout=5.0)
    resp = client.chat.completions.create(
        model='gpt-4o-mini',
        messages=[{'role': 'user', 'content': prompt}],
        max_tokens=20,
        temperature=0,
    )
    raw = resp.choices[0].message.content.strip().strip('"\'')
    return raw if raw else None


def _summarize_gemini(prompt: str, api_key: str) -> Optional[str]:
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    resp = model.generate_content(prompt, request_options={'timeout': 5})
    raw = resp.text.strip().strip('"\'')
    return raw if raw else None


# ── Unstructured-body field extraction (email fallback) ───────────────────────

_EXTRACTION_FIELDS = [
    'requester_name', 'requester_email', 'requester_phone',
    'builder', 'project', 'community', 'site', 'lot_phase',
    'city_state', 'service_type', 'scope', 'urgency',
    'preferred_callback', 'po_number', 'rec_number',
]

_EXTRACTION_PROMPT = (
    "You are an intake parser for a construction / land-management company. "
    "You receive the raw body of one customer email or web-form submission and "
    "must extract the structured fields listed below.\n\n"
    "Fields (return only the ones you can find):\n"
    "  - requester_name        the person writing the request\n"
    "                          (labels: 'Name', 'Requester', 'Requester Name',\n"
    "                           'Requested by', 'Requestor', 'Submitted by',\n"
    "                           'Contact name', 'Full name', 'Client name',\n"
    "                           'From')\n"
    "  - requester_email       email address of the requester\n"
    "  - requester_phone       phone of the requester (keep original formatting)\n"
    "  - builder               building / development / general-contractor name\n"
    "                          (labels: 'Builder', 'Builder name', 'Developer',\n"
    "                           'GC', 'General Contractor')\n"
    "  - project               project name or internal code, if distinct from builder\n"
    "  - community             community / subdivision / HOA / neighborhood\n"
    "                          name ('Community', 'Subdivision', 'Property name')\n"
    "  - site                  street address of the job site — ONLY the\n"
    "                          street + number (city/state goes in city_state)\n"
    "                          (labels: 'Site', 'Address', 'Property address',\n"
    "                           'Street', 'Street address', 'Job address')\n"
    "  - lot_phase             lot number / unit number / phase (labels: 'Lot',\n"
    "                          'Lot #', 'Lot / phase', 'Unit', 'Apt', 'Phase',\n"
    "                          'Suite')\n"
    "  - city_state            city + state / ZIP combined ('City', 'State',\n"
    "                          'City / state', 'City, State ZIP')\n"
    "  - service_type          the trade / work category (Plumbing, Electrical,\n"
    "                          HVAC, Framing, Roofing, Painting, Drywall,\n"
    "                          Flooring, Concrete, Landscaping, Erosion Control,\n"
    "                          Street Sweeping, Warranty, etc.)  Prefer the\n"
    "                          customer's own wording when it names a specific\n"
    "                          activity.\n"
    "  - scope                 1-3 sentence description of the work / issue —\n"
    "                          copy the customer's own words.  Use the body of\n"
    "                          the 'Message' / 'Request' / 'Scope' / 'Description'\n"
    "                          section when present, otherwise the free-form\n"
    "                          prose of the email.\n"
    "  - urgency               deadline or priority hint ('ASAP', 'by Thursday',\n"
    "                          'end of week', 'High', 'inspection Friday').\n"
    "                          Labels: 'Urgency', 'Priority', 'Deadline',\n"
    "                          'Timeline', 'Needed by'.\n"
    "  - preferred_callback    how the customer wants to be reached ('Phone',\n"
    "                          'Text', 'Email', 'Mornings', 'After 5pm').\n"
    "                          Labels: 'Preferred callback', 'Best time to call',\n"
    "                          'Callback window', 'Availability'.\n"
    "  - po_number             purchase-order identifier, e.g. 'PO-2024-0871'.\n"
    "                          Accept either a labelled field ('PO number',\n"
    "                          'Purchase order') or prose ('PO is PO-2024-0871').\n"
    "  - rec_number            REC identifier, e.g. 'REC-8845'.\n\n"
    "RULES:\n"
    " 1. Reply with ONLY a JSON object — no prose, no code fences, no preamble.\n"
    " 2. Omit any field you cannot find confidently.  Never invent, guess or\n"
    "    'assume' values.  If the customer did not supply it, leave it out.\n"
    " 3. Copy values verbatim from the email when possible.  Strip labels,\n"
    "    bullet markers ('•', '-', '*'), and surrounding quotes from the value.\n"
    " 4. Normalise clearly-blank fields (e.g. 'Address: '  → omit).\n"
    " 5. Prefer the body-parsed requester over the envelope sender.\n"
    " 6. Keep each value under 300 characters.\n"
    " 7. For ``site`` return ONLY the street + number.  Put city/state in\n"
    "    ``city_state`` and lot/phase in ``lot_phase`` even if the customer\n"
    "    concatenated them onto the Address line.\n\n"
    "Examples:\n\n"
    "Email:\n"
    "  Name: Mike Torres\n"
    "  Phone: 412-555-0193\n"
    "  Email: mike@greatstonehomes.com\n"
    "  Builder: Great Stone Homes\n"
    "  Community: Ridgeview Estates\n"
    "  Lot: 18\n"
    "  Service Type: Erosion Control\n"
    "  Address: 1240 Ridgeview Drive, Pittsburgh\n"
    "  Message: Need ~800 ft of filter sock plus 6 inlet protections by Thu. "
    "PO is PO-2024-0871.\n"
    "Output:\n"
    '  {"requester_name":"Mike Torres","requester_phone":"412-555-0193",'
    '"requester_email":"mike@greatstonehomes.com","builder":"Great Stone Homes",'
    '"community":"Ridgeview Estates","lot_phase":"18","service_type":"Erosion Control",'
    '"site":"1240 Ridgeview Drive","city_state":"Pittsburgh",'
    '"scope":"Need ~800 ft of filter sock plus 6 inlet protections by Thursday.",'
    '"urgency":"by Thursday","po_number":"PO-2024-0871"}\n\n'
    "Email:\n"
    "  Hi team, we need street sweeping restarted at Lakeview Commons, same as "
    "last season. Full site, every other Friday. REC number is REC-8845.\n"
    "  Thanks,\n  Sandra Bloom\n"
    "Output:\n"
    '  {"requester_name":"Sandra Bloom","service_type":"Street Sweeping",'
    '"community":"Lakeview Commons",'
    '"scope":"Street sweeping restarted at Lakeview Commons, same as last season. '
    'Full site, every other Friday.",'
    '"rec_number":"REC-8845"}\n\n'
    "Email text to parse:\n{text}"
)


def extract_fields_from_text(text: str) -> dict:
    """
    Best-effort structured-field extraction from unstructured email/SMS text.

    Returns an empty dict if no LLM is configured or the call fails — callers
    should merge the result over what they already parsed heuristically.
    """
    if not text or not text.strip():
        return {}
    if not _llm_available():
        return {}

    # ``str.format`` would choke on the JSON braces in the in-prompt examples,
    # so substitute only the {text} placeholder explicitly.
    prompt = _EXTRACTION_PROMPT.replace('{text}', (text or '')[:3000])

    openai_key = getattr(settings, 'OPENAI_API_KEY', '')
    if openai_key:
        try:
            return _extract_openai(prompt, openai_key)
        except Exception as exc:
            logger.warning('Field-extraction OpenAI call failed: %s', type(exc).__name__)

    gemini_key = getattr(settings, 'GEMINI_API_KEY', '')
    if gemini_key:
        try:
            return _extract_gemini(prompt, gemini_key)
        except Exception as exc:
            logger.warning('Field-extraction Gemini call failed: %s', type(exc).__name__)

    return {}


_LEADING_ARTICLE_RE = re.compile(
    r'^(?:our|my|your|their|the|a|an)\s+',
    re.IGNORECASE,
)

# Fields where we strip "our ", "the ", etc. from the front.  Leaving
# "Willow Ridge" instead of "our Willow Ridge community" lets the UI filter
# and sort predictably.
_STRIP_ARTICLE_FIELDS = {'builder', 'community', 'project', 'site'}

# Trailing nouns that describe the *kind* of location — not part of the name
# itself.  Stripping them gives us "Willow Ridge" from "Willow Ridge community".
_TRAILING_LOCATION_SUFFIX_RE = re.compile(
    r'\s+(?:community|subdivision|development|estates?|village|ranch|farms?|'
    r'property|complex|phase|neighborhood|hoa)\b[\s,.]*$',
    re.IGNORECASE,
)


def _clean_extracted(data: dict) -> dict:
    out: dict = {}
    for k in _EXTRACTION_FIELDS:
        v = data.get(k)
        if isinstance(v, str):
            v = v.strip().strip('"\'')
            if not v or v.lower() in {'n/a', 'none', 'unknown', 'null', '-', '--'}:
                continue
            if k in _STRIP_ARTICLE_FIELDS:
                # Strip leading articles / possessives ("our Willow Ridge" → "Willow Ridge").
                v = _LEADING_ARTICLE_RE.sub('', v).strip()
            if k == 'community':
                # "Willow Ridge community" → "Willow Ridge" — but only trim if
                # there's still a real name left afterwards.
                stripped = _TRAILING_LOCATION_SUFFIX_RE.sub('', v).strip()
                if stripped and len(stripped) >= 3:
                    v = stripped
            out[k] = v[:300] if v else ''
    return {k: v for k, v in out.items() if v}


def _extract_openai(prompt: str, api_key: str) -> dict:
    import json
    import openai
    client = openai.OpenAI(api_key=api_key, timeout=6.0)
    resp = client.chat.completions.create(
        model='gpt-4o-mini',
        messages=[{'role': 'user', 'content': prompt}],
        max_tokens=400,
        temperature=0,
        response_format={'type': 'json_object'},
    )
    raw = resp.choices[0].message.content.strip()
    return _clean_extracted(json.loads(raw))


def _extract_gemini(prompt: str, api_key: str) -> dict:
    import json
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    resp = model.generate_content(prompt, request_options={'timeout': 6})
    raw = resp.text.strip()
    if raw.startswith('```'):
        raw = raw.split('```')[1].lstrip('json').strip()
    return _clean_extracted(json.loads(raw))


# ── Knowledge-base free-form answering ────────────────────────────────────────
#
# `answer_from_kb` is used by the outbound SMS helper so that staff can simply
# paste a company brief / FAQ document into a single KnowledgeBaseEntry and
# have the AI answer general questions from that context — without having to
# hand-craft trigger phrases or tags for every possible wording.
#
# The LLM is instructed to answer STRICTLY from the provided knowledge base.
# If the KB does not cover the question, the model must return the single
# sentinel token ``NOANSWER`` and we fall back to the normal intake flow.

_DEFAULT_KB_ANSWER_PROMPT = (
    "You are a friendly SMS assistant for a land management and construction "
    "company. Answer the customer's question using ONLY the Knowledge Base "
    "below — do not invent facts, phone numbers, addresses, prices, or hours "
    "that are not present in the Knowledge Base.\n\n"
    "Formatting rules:\n"
    " • Reply in 1-3 short sentences, friendly and conversational.\n"
    " • Stay under 600 characters total. Never include URLs on their own line.\n"
    " • Do not ask the user to call or text another number unless the KB "
    "explicitly provides one.\n"
    " • Do not add sign-offs, disclaimers, or marketing copy.\n\n"
    "If the Knowledge Base does not contain a clear answer to the question, "
    "reply with the single token NOANSWER (all caps, no quotes, no other "
    "words, no punctuation).\n\n"
    "Knowledge Base:\n"
    "-----\n"
    "{context}\n"
    "-----\n\n"
    "Customer question:\n"
    "{question}"
)

_KB_NO_ANSWER_SENTINELS = ('noanswer', 'no answer', 'no-answer', 'i don\'t know',
                            'i do not know', 'cannot answer', 'can not answer',
                            'not enough information', 'no information available')


def _kb_looks_like_no_answer(raw: str) -> bool:
    """True if the LLM signalled it could not answer from the KB context."""
    if not raw:
        return True
    stripped = raw.strip()
    if not stripped:
        return True
    lower = stripped.lower()
    # Exact / leading sentinel.
    if lower.startswith('noanswer'):
        return True
    # Short refusal — treat as no-answer to avoid leaking meta replies.
    if len(stripped) < 6:
        return True
    for s in _KB_NO_ANSWER_SENTINELS:
        if s in lower:
            return True
    return False


def answer_from_kb(question: str, context: str, *, organization=None) -> Optional[str]:
    """
    Return a short, grounded answer to ``question`` using ``context`` as
    the sole source of truth.

    Safe by design:
      • Returns ``None`` when the LLM is not configured, the call fails,
        the context is empty, or the model could not answer.
      • Never raises — any exception is swallowed and logged, so the SMS
        intake path can fall back to the normal collecting flow.
    """
    if not question or not question.strip():
        return None
    if not context or not context.strip():
        return None
    if not _llm_available():
        return None

    default_template = _DEFAULT_KB_ANSWER_PROMPT
    try:
        from intake.models import SystemPrompt  # local import — avoids circular
        template = SystemPrompt.get(
            'kb_answer', organization=organization, default=default_template,
        ) or default_template
    except Exception:
        template = default_template

    prompt = (
        (template or default_template)
        .replace('{context}', (context or '')[:8000])
        .replace('{question}', (question or '')[:1000])
    )

    openai_key = getattr(settings, 'OPENAI_API_KEY', '')
    if openai_key:
        try:
            return _kb_answer_openai(prompt, openai_key)
        except Exception as exc:
            logger.warning('KB OpenAI answer failed: %s', type(exc).__name__)

    gemini_key = getattr(settings, 'GEMINI_API_KEY', '')
    if gemini_key:
        try:
            return _kb_answer_gemini(prompt, gemini_key)
        except Exception as exc:
            logger.warning('KB Gemini answer failed: %s', type(exc).__name__)

    return None


def _kb_answer_openai(prompt: str, api_key: str) -> Optional[str]:
    import openai
    client = openai.OpenAI(api_key=api_key, timeout=6.0)
    resp = client.chat.completions.create(
        model='gpt-4o-mini',
        messages=[{'role': 'user', 'content': prompt}],
        max_tokens=220,
        temperature=0.2,
    )
    raw = (resp.choices[0].message.content or '').strip()
    if _kb_looks_like_no_answer(raw):
        return None
    return raw


def _kb_answer_gemini(prompt: str, api_key: str) -> Optional[str]:
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    resp = model.generate_content(prompt, request_options={'timeout': 6})
    raw = (resp.text or '').strip()
    if raw.startswith('```'):
        # Strip accidental code fences.
        raw = raw.strip('`').strip()
    if _kb_looks_like_no_answer(raw):
        return None
    return raw
