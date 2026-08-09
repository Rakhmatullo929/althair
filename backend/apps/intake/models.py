import logging
import re
from uuid import uuid4

from django.conf import settings
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from core.models import BaseModel
from core.utils.encryption import EncryptedTextField
from organizations.models import OrganizationOwnedModel

logger = logging.getLogger(__name__)


# ── PO / REQ business rule ─────────────────────────────────────────────────
# Client rule: ONLY these builders require a Purchase Order.  Anything else
# defaults to "not required".  Kept as a normalized set + a tolerant matcher so
# spelling variants ("DR Horton", "D.R. Horton Homes", "Foxlane") all resolve.
PO_REQUIRED_BUILDERS = frozenset({
    'dr horton', 'd r horton', 'fox lane', 'foxlane',
})


def _normalize_builder(builder: str) -> str:
    """Lower-case, strip punctuation, collapse whitespace for builder matching."""
    s = re.sub(r'[.,]', '', (builder or '').strip().lower())
    return re.sub(r'\s+', ' ', s)


def builder_requires_po(builder: str) -> bool:
    """True when the builder is D.R. Horton or Fox Lane (the only PO builders)."""
    norm = _normalize_builder(builder)
    if not norm:
        return False
    if norm in PO_REQUIRED_BUILDERS:
        return True
    # Tolerant match for trailing words ("d r horton homes", "fox lane at ...").
    return 'horton' in norm or 'fox lane' in norm or 'foxlane' in norm


_PO_TOKEN_RE = re.compile(r'\bPO\s*:\s*\S', re.IGNORECASE)


def _note_has_po_number(po_rec_note: str) -> bool:
    """True when the freeform PO/REQ note contains a 'PO: <value>' token."""
    return bool(_PO_TOKEN_RE.search(po_rec_note or ''))


class ChannelChoice(models.TextChoices):
    SMS = 'sms', _('SMS')
    EMAIL = 'email', _('Email / Form')
    MANUAL = 'manual', _('Manual Entry')
    VOICE = 'voice', _('Voice / Phone Call')


class InteractionCategory(models.TextChoices):
    IMMEDIATE_RESOLUTION = 'immediate_resolution', _('Immediate Resolution')
    JOB_REQUEST_INTAKE = 'job_request_intake', _('Job Request Intake')
    REVIEW_REQUIRED = 'review_required', _('Review Required')
    ESCALATION = 'escalation', _('Escalation')


class InteractionStatus(models.TextChoices):
    NEW = 'new', _('New')
    IN_PROGRESS = 'in_progress', _('In Progress')
    REVIEW_REQUIRED = 'review_required', _('Review Required')
    RESOLVED = 'resolved', _('Resolved')
    CLOSED = 'closed', _('Closed')


class JobStatus(models.TextChoices):
    NEW = 'new', _('New')
    PENDING_REVIEW = 'pending_review', _('Pending Review')
    PRIORITIZED = 'prioritized', _('Prioritized')
    SCHEDULED = 'scheduled', _('Scheduled')
    IN_PROGRESS = 'in_progress', _('In Progress')
    COMPLETED = 'completed', _('Completed')
    REOPENED = 'reopened', _('Reopened')
    BLOCKED = 'blocked', _('Blocked')
    PENDING_FOLLOW_UP = 'pending_follow_up', _('Pending Follow-Up')
    CANCELLED = 'cancelled', _('Cancelled')


class JobPriority(models.TextChoices):
    LOW = 'low', _('Low')
    NORMAL = 'normal', _('Normal')
    HIGH = 'high', _('High')
    URGENT = 'urgent', _('Urgent')


class PoRecStatus(models.TextChoices):
    NOT_REQUIRED = 'not_required', _('Not Required')
    PENDING = 'pending', _('Pending')
    RECEIVED = 'received', _('Received')
    MISSING = 'missing', _('Missing')


class ContactSource(models.TextChoices):
    SMS = 'sms', _('SMS')
    EMAIL = 'email', _('Email / Form')
    MANUAL = 'manual', _('Manual Entry')
    VOICE = 'voice', _('Voice / Phone Call')


class JobEventType(models.TextChoices):
    CREATED = 'created', _('Created')
    STATUS_CHANGED = 'status_changed', _('Status Changed')
    PRIORITY_CHANGED = 'priority_changed', _('Priority Changed')
    SCHEDULED = 'scheduled', _('Scheduled')
    RESCHEDULED = 'rescheduled', _('Rescheduled')
    UNSCHEDULED = 'unscheduled', _('Unscheduled')
    REOPENED = 'reopened', _('Reopened')
    PO_REC_UPDATED = 'po_rec_updated', _('PO/REQ Updated')
    NOTE_ADDED = 'note_added', _('Note Added')
    ASSIGNED = 'assigned', _('Assigned')
    FIELD_UPDATED = 'field_updated', _('Field Updated')
    URGENT_FLAGGED = 'urgent_flagged', _('Urgent Alert')
    MISSING_AUTH_FLAGGED = 'missing_auth_flagged', _('Missing Auth Alert')
    MESSAGE_RECEIVED = 'message_received', _('Message Received')


class JobNumberCounter(OrganizationOwnedModel):
    """Atomic source of sequential, human-readable job numbers (A-001, A-002 …).

    One row per letter-series prefix.  ``allocate_job_number`` bumps ``last_value``
    under ``select_for_update`` so concurrent intakes never collide — far safer
    than ``max(...)+1`` over JobRecord.
    """
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    prefix = models.CharField(_('prefix'), max_length=4, default='A')
    last_value = models.PositiveIntegerField(_('last value'), default=0)

    class Meta:
        verbose_name = _('Job Number Counter')
        verbose_name_plural = _('Job Number Counters')
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'prefix'],
                name='unique_job_counter_org_prefix',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.prefix}-{self.last_value:03d}'


def allocate_job_number(organization, prefix: str = 'A') -> str:
    """Return the next sequential job number for *prefix* (e.g. ``A-001``).

    Locks the counter row for the duration of the surrounding transaction so two
    simultaneous job creations can never receive the same number.
    """
    with transaction.atomic():
        counter, _created = (
            JobNumberCounter.objects.select_for_update().get_or_create(
                organization=organization,
                prefix=prefix,
            )
        )
        counter.last_value += 1
        counter.save(update_fields=['last_value'])
        return f'{prefix}-{counter.last_value:03d}'


class TeamLead(OrganizationOwnedModel):
    """A field team lead used to group the route calendar's daily schedule.

    Jobs attach to a lead by matching ``JobRecord.assigned_to`` to the lead's
    ``name`` — no extra field on JobRecord is needed.
    """
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    name = models.CharField(_('name'), max_length=120)
    position = models.PositiveIntegerField(_('position'), default=0, db_index=True)
    is_active = models.BooleanField(_('active'), default=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='team_lead',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Team Lead')
        verbose_name_plural = _('Team Leads')
        ordering = ['position', 'created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'name'],
                name='unique_team_lead_name_per_org',
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Contact(OrganizationOwnedModel, BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    name = models.CharField(_('name'), max_length=255, blank=True)
    phone = models.CharField(_('phone'), max_length=30, blank=True, db_index=True)
    email = models.EmailField(_('email'), blank=True, db_index=True)
    source = models.CharField(
        _('source'), max_length=20,
        choices=ContactSource.choices, default=ContactSource.SMS,
    )
    notes = EncryptedTextField(_('notes'), blank=True, default='')
    metadata = models.JSONField(_('metadata'), default=dict, blank=True)

    class Meta:
        verbose_name = _('Contact')
        verbose_name_plural = _('Contacts')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'phone']),
            models.Index(fields=['organization', 'email']),
        ]

    def __str__(self) -> str:
        if self.name:
            return f'{self.name} ({self.phone or self.email})'
        return self.phone or self.email or f'Contact {str(self.id)[:8]}'


class JobRecord(OrganizationOwnedModel, BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    job_number = models.CharField(
        _('job number'), max_length=12, blank=True,
        help_text=_('Human-readable tracking ID (e.g. A-001), assigned automatically on creation.'),
    )
    source_channel = models.CharField(
        _('source channel'), max_length=20, choices=ChannelChoice.choices,
    )
    contact = models.ForeignKey(
        Contact, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='job_records', verbose_name=_('contact / requester'),
    )
    source_interaction = models.ForeignKey(
        'Interaction', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_jobs', verbose_name=_('source interaction'),
    )

    service_type = models.CharField(_('service type'), max_length=255, blank=True)
    install_type = models.CharField(
        _('install type'), max_length=255, blank=True,
        help_text=_('Specific type of install when a job is an "install" request '
                    '(e.g. silt fence, filter sock, inlet protection).'),
    )
    builder = models.CharField(_('builder'), max_length=255, blank=True)
    project = models.CharField(_('project'), max_length=255, blank=True)
    site = models.CharField(_('site'), max_length=255, blank=True)
    community = models.CharField(_('community'), max_length=255, blank=True)
    scope = EncryptedTextField(_('scope / description'), blank=True, default='')
    quantity = models.CharField(_('quantity'), max_length=100, blank=True)
    urgency_notes = models.CharField(_('urgency / deadline notes'), max_length=500, blank=True)
    division = models.CharField(_('division / routing context'), max_length=255, blank=True)
    po_rec_note = models.CharField(_('PO / REQ note'), max_length=500, blank=True)
    summary = EncryptedTextField(_('summary / notes'), blank=True, default='')
    ai_summary = EncryptedTextField(_('AI / system summary'), blank=True, default='')

    status = models.CharField(
        _('status'), max_length=30,
        choices=JobStatus.choices, default=JobStatus.NEW, db_index=True,
    )

    priority = models.CharField(
        _('priority'), max_length=20,
        choices=JobPriority.choices, default=JobPriority.NORMAL, db_index=True,
    )

    due_date = models.DateField(_('due date'), null=True, blank=True, db_index=True)
    scheduled_date = models.DateField(_('scheduled date'), null=True, blank=True, db_index=True)
    scheduled_time = models.TimeField(_('scheduled time'), null=True, blank=True)
    assigned_to = models.CharField(_('assigned to'), max_length=255, blank=True)
    is_recurring = models.BooleanField(_('recurring weekly'), default=False, db_index=True)
    assignment_notes = models.CharField(_('assignment notes'), max_length=500, blank=True)
    # Dashboard user the job is assigned to (for "show only my tasks" filtering).
    assigned_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='assigned_jobs',
        verbose_name=_('assigned to user'),
    )

    po_required = models.BooleanField(_('PO required'), default=False)
    rec_required = models.BooleanField(_('REQ required'), default=False)
    po_status = models.CharField(
        _('PO status'), max_length=20,
        choices=PoRecStatus.choices, default=PoRecStatus.NOT_REQUIRED, db_index=True,
    )
    rec_status = models.CharField(
        _('REQ status'), max_length=20,
        choices=PoRecStatus.choices, default=PoRecStatus.NOT_REQUIRED, db_index=True,
    )

    # Lightweight "a human has seen this / it's in the works" acknowledgement,
    # independent of the heavier lifecycle `status`.
    is_seen = models.BooleanField(
        _('seen / in the works'), default=False, db_index=True,
        help_text=_('A staff member has acknowledged this job request.'),
    )
    seen_at = models.DateTimeField(_('seen at'), null=True, blank=True)

    reopened_at = models.DateTimeField(_('last reopened at'), null=True, blank=True)
    reopened_count = models.PositiveIntegerField(_('reopen count'), default=0)
    completion_notes = EncryptedTextField(_('completion notes'), blank=True, default='')

    class Meta:
        verbose_name = _('Job Record')
        verbose_name_plural = _('Job Records')
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'job_number'],
                name='unique_job_number_per_org',
            ),
        ]
        indexes = [
            models.Index(fields=['organization', 'status', 'created_at']),
            models.Index(fields=['organization', 'scheduled_date']),
        ]

    def __str__(self) -> str:
        parts = [
            self.service_type or 'Job',
            self.builder or self.project or '',
            self.job_number or f'#{str(self.id)[:8]}',
        ]
        return ' | '.join(p for p in parts if p)

    def save(self, *args, **kwargs):
        # Assign a sequential human-readable job number on first save, across
        # every channel (SMS, voice, email, manual) — see allocate_job_number.
        if self._state.adding and not self.job_number:
            if not self.organization_id:
                raise ValueError('organization is required before allocating a job number')
            self.job_number = allocate_job_number(self.organization)

        # Enforce the builder-based PO rule uniformly, regardless of which
        # intake path created/edited the job.  Keeps the rule out of the AI/
        # voice conversation code.
        before = (self.po_required, self.po_status)
        self._apply_po_policy()
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and (self.po_required, self.po_status) != before:
            kwargs['update_fields'] = set(update_fields) | {'po_required', 'po_status'}

        super().save(*args, **kwargs)

    def _apply_po_policy(self) -> None:
        """Only D.R. Horton & Fox Lane require a PO (client rule, items 21-22).

        Runs on every save.  It only *adds* the requirement for those two
        builders and never clears a PO requirement a human set manually for some
        other builder.  When the builder requires a PO but none was captured, the
        status is flagged MISSING so the gap surfaces in the dashboard / hotlist.
        """
        if not (self.builder and builder_requires_po(self.builder)):
            return
        self.po_required = True
        # Only recompute the auto-managed states; respect a human-set
        # PENDING / RECEIVED.
        if self.po_status in (PoRecStatus.NOT_REQUIRED, PoRecStatus.MISSING):
            self.po_status = (
                PoRecStatus.RECEIVED if _note_has_po_number(self.po_rec_note)
                else PoRecStatus.MISSING
            )

    @property
    def is_active(self) -> bool:
        """True when the job is in an operational (non-terminal) state."""
        return self.status in (
            JobStatus.NEW, JobStatus.PENDING_REVIEW, JobStatus.PRIORITIZED,
            JobStatus.SCHEDULED, JobStatus.IN_PROGRESS, JobStatus.REOPENED,
            JobStatus.BLOCKED, JobStatus.PENDING_FOLLOW_UP,
        )


class Interaction(OrganizationOwnedModel, BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    channel = models.CharField(
        _('channel'), max_length=20, choices=ChannelChoice.choices, db_index=True,
    )
    contact = models.ForeignKey(
        Contact, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='interactions', verbose_name=_('contact'),
    )
    category = models.CharField(
        _('category / route'), max_length=40,
        choices=InteractionCategory.choices,
        default=InteractionCategory.REVIEW_REQUIRED, db_index=True,
    )
    status = models.CharField(
        _('status'), max_length=30,
        choices=InteractionStatus.choices,
        default=InteractionStatus.NEW, db_index=True,
    )

    summary = EncryptedTextField(_('summary'), blank=True, default='')
    raw_content = EncryptedTextField(_('raw content'), blank=True, default='')
    raw_content_reference = models.CharField(
        _('raw content reference'), max_length=500, blank=True,
    )

    provider_payload = models.JSONField(_('provider payload'), default=dict, blank=True)
    provider_id = models.CharField(
        _('provider ID'), max_length=255, blank=True, db_index=True,
    )
    channel_connection = models.ForeignKey(
        'channels.ChannelConnection',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='interactions',
    )

    caller_phone = models.CharField(_('sender phone'), max_length=30, blank=True)
    caller_email = models.CharField(_('sender email'), max_length=254, blank=True)

    classification_confidence = models.FloatField(
        _('classification confidence'), null=True, blank=True,
    )
    classification_reason = models.CharField(
        _('classification reason'), max_length=500, blank=True,
    )

    related_job = models.ForeignKey(
        JobRecord, on_delete=models.CASCADE, null=True, blank=True,
        related_name='interactions', verbose_name=_('related job'),
    )

    # ── Escalation tracking ───────────────────────────────────────────────────
    is_read = models.BooleanField(
        _('read / acknowledged'), default=False, db_index=True,
        help_text=_('Staff has acknowledged this escalation / review item.'),
    )
    escalation_reason = models.CharField(
        _('escalation reason'), max_length=500, blank=True,
        help_text=_('Why this was escalated — filled automatically or by staff.'),
    )

    class Meta:
        verbose_name = _('Interaction')
        verbose_name_plural = _('Interactions')
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'channel_connection', 'provider_id'],
                condition=~models.Q(provider_id=''),
                name='unique_provider_message_per_org_connection',
            ),
        ]
        indexes = [models.Index(fields=['organization', 'channel', 'created_at'])]

    def __str__(self) -> str:
        return (
            f'{self.get_channel_display()} | '
            f'{self.get_category_display()} | '
            f'{str(self.id)[:8]}'
        )


class InternalNote(OrganizationOwnedModel, BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    interaction = models.ForeignKey(
        Interaction, on_delete=models.CASCADE, null=True, blank=True,
        related_name='notes', verbose_name=_('interaction'),
    )
    job = models.ForeignKey(
        JobRecord, on_delete=models.CASCADE, null=True, blank=True,
        related_name='notes', verbose_name=_('job record'),
    )
    body = EncryptedTextField(_('note body'))

    class Meta:
        verbose_name = _('Internal Note')
        verbose_name_plural = _('Internal Notes')
        ordering = ['-created_at']

    def __str__(self) -> str:
        target = self.interaction or self.job
        return f'Note on {target} ({str(self.id)[:8]})'


class JobNote(OrganizationOwnedModel, BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    job = models.ForeignKey(
        JobRecord, on_delete=models.CASCADE,
        related_name='job_notes', verbose_name=_('job record'),
    )
    body = EncryptedTextField(_('note body'))
    is_follow_up = models.BooleanField(_('is follow-up'), default=False)

    class Meta:
        verbose_name = _('Job Note')
        verbose_name_plural = _('Job Notes')
        ordering = ['-created_at']

    def __str__(self) -> str:
        flag = ' [follow-up]' if self.is_follow_up else ''
        return f'Note on {self.job}{flag} ({str(self.id)[:8]})'


class JobEvent(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    job = models.ForeignKey(
        JobRecord, on_delete=models.CASCADE,
        related_name='events', verbose_name=_('job record'),
    )
    event_type = models.CharField(
        _('event type'), max_length=30, choices=JobEventType.choices,
    )
    old_value = models.CharField(_('old value'), max_length=500, blank=True)
    new_value = models.CharField(_('new value'), max_length=500, blank=True)
    note = models.CharField(_('note'), max_length=1000, blank=True)
    created_at = models.DateTimeField(_('created at'), auto_now_add=True, db_index=True)
    created_by = models.ForeignKey(
        'users.User', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='job_events', verbose_name=_('created by'),
    )

    class Meta:
        verbose_name = _('Job Event')
        verbose_name_plural = _('Job Events')
        ordering = ['-created_at']
        indexes = [models.Index(fields=['organization', 'created_at'])]

    def __str__(self) -> str:
        return f'{self.get_event_type_display()} — {self.job} @ {self.created_at}'


class SystemPrompt(OrganizationOwnedModel):
    """Configurable AI prompts editable by staff in Django Admin.

    Used keys
    ─────────
      - ``sms_assistant``      — main prompt for the SMS chat agent.  Loaded
         every webhook hit; its output drives the customer dialogue through
         the ``send_sms``/``finish_and_submit``/``close_chat`` tools.
      - ``sms_runtime_rules``  — optional small in-code rules block
         (language, formatting, etc.).  Empty by default.
      - ``email_short_summary`` — 3-4 word title extraction for email/form intake.
      - ``sms_short_summary``   — 2-3 word title used when a JobRecord is
         created from SMS.
    """

    key = models.CharField(
        _('key'), max_length=100,
        help_text=_('Identifier used in code, e.g. "email_short_summary". Do not change once deployed.'),
    )
    text = models.TextField(
        _('prompt text'),
        help_text=_(
            'Full prompt. Use {text} as the placeholder for the incoming message. '
            'Example: "Summarise in 3-4 words: {text}"'
        ),
    )
    description = models.CharField(
        _('description'), max_length=500, blank=True,
        help_text=_('Human-readable note for staff — what this prompt is used for.'),
    )
    is_active = models.BooleanField(_('active'), default=True)

    class Meta:
        verbose_name = _('System Prompt')
        verbose_name_plural = _('System Prompts')
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'key'],
                name='unique_system_prompt_key_per_org',
            ),
        ]

    def __str__(self) -> str:
        status = '' if self.is_active else ' [inactive]'
        return f'{self.key}{status}'

    @classmethod
    def get(cls, key: str, *, organization=None, default: str = '') -> str:
        """Return the active prompt text for *key*, or *default* if not found / inactive."""
        if organization is None:
            # Never fall back to a global/first tenant prompt.
            return default
        try:
            obj = cls.objects.filter(
                organization=organization,
                key=key,
                is_active=True,
            ).only('text').first()
            return obj.text if obj else default
        except Exception:
            return default


class KnowledgeBaseEntry(OrganizationOwnedModel):
    """
    Staff-editable Q&A entries used by the AI to answer informational SMS/email queries.

    The intake classification service searches this table when a message is classified
    as `immediate_resolution` to find a matching prepared answer before falling back
    to a generic LLM response.
    """

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    topic = models.CharField(
        _('topic'), max_length=255,
        help_text=_('Short category label, e.g. "Hours", "Pricing", "Warranty".'),
    )
    question = models.TextField(
        _('question / trigger'),
        help_text=_('Example question or phrase that should match this entry.'),
    )
    answer = models.TextField(
        _('answer'),
        help_text=_('The prepared answer the AI or staff will use.'),
    )
    tags = models.CharField(
        _('tags'), max_length=500, blank=True,
        help_text=_('Comma-separated keywords to help retrieval, e.g. "hours,schedule,open".'),
    )
    is_active = models.BooleanField(_('active'), default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Knowledge Base Entry')
        verbose_name_plural = _('Knowledge Base')
        ordering = ['topic', 'question']
        indexes = [models.Index(fields=['organization', 'is_active', 'topic'])]

    def __str__(self) -> str:
        return f'[{self.topic}] {self.question[:60]}'


class ThreadState(models.TextChoices):
    OPEN = 'open', _('Open')
    CLOSED = 'closed', _('Closed')


class MessageRole(models.TextChoices):
    USER = 'user', _('User')
    ASSISTANT = 'assistant', _('Assistant')
    TOOL = 'tool', _('Tool')


class ConversationThread(OrganizationOwnedModel):
    SESSION_TIMEOUT_HOURS = 4

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    phone = models.CharField(
        _('phone'), max_length=30, db_index=True,
    )
    contact = models.ForeignKey(
        Contact, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='conversation_threads',
    )
    active_job = models.ForeignKey(
        JobRecord, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='conversation_threads',
    )
    channel_connection = models.ForeignKey(
        'channels.ChannelConnection',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='conversation_threads',
    )
    state = models.CharField(
        _('state'), max_length=20,
        choices=ThreadState.choices, default=ThreadState.OPEN,
        db_index=True,
    )
    is_active = models.BooleanField(_('active'), default=True, db_index=True)
    last_message_at = models.DateTimeField(_('last message at'), auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Conversation Thread')
        verbose_name_plural = _('Conversation Threads')
        ordering = ['-last_message_at']
        indexes = [models.Index(fields=['organization', 'phone', 'state', 'is_active'])]

    def __str__(self) -> str:
        return f'Thread {self.phone[:6]}*** [{self.get_state_display()}]'

    def is_timed_out(self) -> bool:
        from django.utils import timezone
        import datetime
        if not self.last_message_at:
            return True
        cutoff = timezone.now() - datetime.timedelta(hours=self.SESSION_TIMEOUT_HOURS)
        return self.last_message_at < cutoff


class ConversationMessage(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    thread = models.ForeignKey(
        ConversationThread, on_delete=models.CASCADE,
        related_name='messages',
    )
    sequence = models.PositiveIntegerField(_('sequence'))
    role = models.CharField(
        _('role'), max_length=20, choices=MessageRole.choices, db_index=True,
    )
    content = EncryptedTextField(_('content'), blank=True, default='')
    tool_calls = models.JSONField(_('tool calls'), default=list, blank=True)
    tool_call_id = models.CharField(_('tool call id'), max_length=128, blank=True)
    tool_name = models.CharField(_('tool name'), max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _('Conversation Message')
        verbose_name_plural = _('Conversation Messages')
        ordering = ['thread', 'sequence']
        unique_together = [('thread', 'sequence')]
        indexes = [
            models.Index(fields=['thread', 'sequence']),
            models.Index(fields=['organization', 'created_at']),
        ]

    def __str__(self) -> str:
        return f'{self.thread_id} #{self.sequence} {self.role}'
