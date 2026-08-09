"""
Phase 2 job serializers.

All serializers use explicit field whitelists (no `__all__`).
Read/write semantics are split where needed to keep payloads clean.
"""

import json
import re

from rest_framework import serializers

from intake.models import (
    ConversationMessage,
    JobEvent,
    JobNote,
    JobPriority,
    JobRecord,
    JobStatus,
    MessageRole,
    PoRecStatus,
    TeamLead,
)


# Tool calls that actually deliver text to the customer.  Anything else
# (finish_and_submit, enrich_job, …) is internal plumbing and is hidden
# from the SMS conversation view.
_OUTBOUND_SMS_TOOLS = {
    'send_sms': 'text',
    'close_chat': 'farewell_text',
    'escalate_chat': 'ack_text',
}


def _extract_outbound_text(tool_calls) -> str:
    """Pull the customer-visible text out of a stored OpenAI tool_calls list."""
    if not tool_calls:
        return ''
    try:
        first = tool_calls[0] or {}
        fn = first.get('function') or {}
        name = fn.get('name') or ''
        text_field = _OUTBOUND_SMS_TOOLS.get(name)
        if not text_field:
            return ''
        raw_args = fn.get('arguments') or '{}'
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        return (args.get(text_field) or '').strip()
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _UserDisplayField(serializers.SerializerMethodField):
    """Read-only field that returns the username of a FK-to-user field."""

    def __init__(self, user_field: str, **kwargs):
        self._user_field = user_field
        super().__init__(**kwargs)

    def to_representation(self, instance):
        user = getattr(instance, self._user_field, None)
        if user is None:
            return None
        return getattr(user, 'get_full_name', lambda: None)() or getattr(user, 'email', None) or str(user)


# ---------------------------------------------------------------------------
# JobNote
# ---------------------------------------------------------------------------

class JobNoteSerializer(serializers.ModelSerializer):
    created_by_display = serializers.SerializerMethodField()

    class Meta:
        model = JobNote
        fields = [
            'id', 'body', 'is_follow_up',
            'created_at', 'created_by_display',
        ]
        read_only_fields = ['id', 'created_at', 'created_by_display']

    def get_created_by_display(self, obj):
        if obj.created_by is None:
            return None
        user = obj.created_by
        return getattr(user, 'get_full_name', lambda: None)() or getattr(user, 'email', None) or str(user)


class JobNoteCreateSerializer(serializers.Serializer):
    body = serializers.CharField(min_length=1, max_length=10000)
    is_follow_up = serializers.BooleanField(default=False)


# ---------------------------------------------------------------------------
# JobEvent (history)
# ---------------------------------------------------------------------------

class JobEventSerializer(serializers.ModelSerializer):
    event_type_display = serializers.CharField(source='get_event_type_display', read_only=True)
    created_by_display = serializers.SerializerMethodField()

    class Meta:
        model = JobEvent
        fields = [
            'id', 'event_type', 'event_type_display',
            'old_value', 'new_value', 'note',
            'created_at', 'created_by_display',
        ]

    def get_created_by_display(self, obj):
        if obj.created_by is None:
            return 'system'
        user = obj.created_by
        return getattr(user, 'get_full_name', lambda: None)() or getattr(user, 'email', None) or str(user)


# ---------------------------------------------------------------------------
# JobListSerializer  (compact, for list / hotlist views)
# ---------------------------------------------------------------------------

class JobListSerializer(serializers.ModelSerializer):
    contact_name = serializers.SerializerMethodField()
    contact_phone = serializers.SerializerMethodField()
    contact_email = serializers.SerializerMethodField()
    po_number = serializers.SerializerMethodField()
    rec_number = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    source_channel_display = serializers.CharField(source='get_source_channel_display', read_only=True)
    po_status_display = serializers.CharField(source='get_po_status_display', read_only=True)
    rec_status_display = serializers.CharField(source='get_rec_status_display', read_only=True)
    assigned_user_id = serializers.UUIDField(source='assigned_user.id', read_only=True, default=None)
    assigned_user_display = _UserDisplayField(user_field='assigned_user')

    class Meta:
        model = JobRecord
        fields = [
            'id', 'job_number', 'status', 'status_display',
            'priority', 'priority_display',
            'source_channel', 'source_channel_display',
            'service_type', 'install_type', 'builder', 'project', 'site', 'community',
            'contact_name', 'contact_phone', 'contact_email',
            'assigned_to', 'assigned_user_id', 'assigned_user_display',
            'due_date', 'scheduled_date', 'scheduled_time',
            'po_required', 'rec_required', 'po_status', 'po_status_display',
            'rec_status', 'rec_status_display',
            'po_number', 'rec_number',
            'urgency_notes', 'division', 'scope', 'is_recurring',
            'summary', 'is_seen',
            'reopened_count',
            'created_at', 'updated_at',
        ]

    def get_contact_name(self, obj):
        return obj.contact.name if obj.contact else ''

    def get_contact_phone(self, obj):
        # See JobDetailSerializer.get_contact_phone for rationale — prefer the
        # sender's number captured in the source_interaction.
        si = getattr(obj, 'source_interaction', None)
        caller = (getattr(si, 'caller_phone', '') or '').strip() if si else ''
        if caller:
            return caller
        return obj.contact.phone if obj.contact else ''

    def get_contact_email(self, obj):
        si = getattr(obj, 'source_interaction', None)
        caller = (getattr(si, 'caller_email', '') or '').strip() if si else ''
        if caller:
            return caller
        return obj.contact.email if obj.contact else ''

    def _split_po_rec(self, obj):
        """
        The data model stores a single ``po_rec_note`` string, but the UI has
        separate PO and REC inputs.  We expose computed fields so the frontend
        can render each independently.
        """
        note = (getattr(obj, 'po_rec_note', '') or '').strip()
        if not note:
            return ('', '')
        # Common intake format: "PO: PO-123 | REC: REC-456 | <freeform...>"
        po = ''
        rec = ''
        m_po = re.search(r'\bPO\s*:\s*([^\|]+)', note, flags=re.IGNORECASE)
        if m_po:
            po = m_po.group(1).strip()
        m_rec = re.search(r'\bRE[QC]\s*:\s*([^\|]+)', note, flags=re.IGNORECASE)
        if m_rec:
            rec = m_rec.group(1).strip()
        return (po, rec)

    def get_po_number(self, obj):
        po, _ = self._split_po_rec(obj)
        return po

    def get_rec_number(self, obj):
        _, rec = self._split_po_rec(obj)
        return rec


# ---------------------------------------------------------------------------
# JobDetailSerializer  (full, for single-record endpoint)
# ---------------------------------------------------------------------------

class JobDetailSerializer(serializers.ModelSerializer):
    contact_name = serializers.SerializerMethodField()
    contact_phone = serializers.SerializerMethodField()
    contact_email = serializers.SerializerMethodField()
    contact_preferred_callback = serializers.SerializerMethodField()
    contact_city_state = serializers.SerializerMethodField()
    po_number = serializers.SerializerMethodField()
    rec_number = serializers.SerializerMethodField()
    contact_id = serializers.UUIDField(source='contact.id', read_only=True, default=None)
    source_interaction_id = serializers.UUIDField(
        source='source_interaction.id', read_only=True, default=None,
    )
    source_interaction_channel = serializers.SerializerMethodField()
    source_transcript = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    source_channel_display = serializers.CharField(source='get_source_channel_display', read_only=True)
    po_status_display = serializers.CharField(source='get_po_status_display', read_only=True)
    rec_status_display = serializers.CharField(source='get_rec_status_display', read_only=True)
    job_notes = JobNoteSerializer(many=True, read_only=True)
    recent_history = serializers.SerializerMethodField()
    sms_messages = serializers.SerializerMethodField()
    is_active = serializers.BooleanField(read_only=True)
    assigned_user_id = serializers.UUIDField(source='assigned_user.id', read_only=True, default=None)
    assigned_user_display = _UserDisplayField(user_field='assigned_user')

    class Meta:
        model = JobRecord
        fields = [
            'id', 'job_number', 'status', 'status_display',
            'priority', 'priority_display',
            'source_channel', 'source_channel_display',
            'is_seen', 'seen_at',
            'contact_id', 'contact_name', 'contact_phone', 'contact_email',
            'contact_preferred_callback', 'contact_city_state',
            'source_interaction_id', 'source_interaction_channel', 'source_transcript',
            'service_type', 'install_type', 'builder', 'project', 'site', 'community',
            'scope', 'quantity', 'urgency_notes', 'division', 'po_rec_note', 'is_recurring',
            'summary', 'ai_summary', 'completion_notes',
            'assigned_to', 'assigned_user_id', 'assigned_user_display',
            'due_date', 'scheduled_date', 'scheduled_time', 'assignment_notes',
            'po_required', 'rec_required',
            'po_status', 'po_status_display',
            'rec_status', 'rec_status_display',
            'po_number', 'rec_number',
            'reopened_at', 'reopened_count',
            'is_active',
            'job_notes', 'recent_history', 'sms_messages',
            'created_at', 'updated_at',
        ]

    def get_contact_name(self, obj):
        return obj.contact.name if obj.contact else ''

    def get_contact_phone(self, obj):
        # Prefer the sender's phone captured at intake time: that guarantees
        # we surface the customer's number rather than the mailbox/Twilio line
        # that may have ended up saved on the Contact row in earlier tests.
        si = getattr(obj, 'source_interaction', None)
        caller = (getattr(si, 'caller_phone', '') or '').strip() if si else ''
        if caller:
            return caller
        return obj.contact.phone if obj.contact else ''

    def get_contact_email(self, obj):
        si = getattr(obj, 'source_interaction', None)
        caller = (getattr(si, 'caller_email', '') or '').strip() if si else ''
        if caller:
            return caller
        return obj.contact.email if obj.contact else ''

    def get_contact_preferred_callback(self, obj):
        if not obj.contact:
            return ''
        meta = obj.contact.metadata or {}
        return str(meta.get('preferred_callback') or '')

    def get_contact_city_state(self, obj):
        if not obj.contact:
            return ''
        meta = obj.contact.metadata or {}
        return str(meta.get('city_state') or '')

    def _split_po_rec(self, obj):
        note = (getattr(obj, 'po_rec_note', '') or '').strip()
        if not note:
            return ('', '')
        po = ''
        rec = ''
        m_po = re.search(r'\bPO\s*:\s*([^\|]+)', note, flags=re.IGNORECASE)
        if m_po:
            po = m_po.group(1).strip()
        m_rec = re.search(r'\bRE[QC]\s*:\s*([^\|]+)', note, flags=re.IGNORECASE)
        if m_rec:
            rec = m_rec.group(1).strip()
        return (po, rec)

    def get_po_number(self, obj):
        po, _ = self._split_po_rec(obj)
        return po

    def get_rec_number(self, obj):
        _, rec = self._split_po_rec(obj)
        return rec

    def get_source_interaction_channel(self, obj):
        if obj.source_interaction:
            return obj.source_interaction.channel
        return None

    def get_source_transcript(self, obj):
        si = getattr(obj, 'source_interaction', None)
        if not si:
            return ''
        return getattr(si, 'raw_content', '') or ''

    def get_recent_history(self, obj):
        # Enough room for a full multi-message conversation + status events.
        events = obj.events.order_by('-created_at')[:50]
        return JobEventSerializer(events, many=True).data

    def get_sms_messages(self, obj):
        # Pull the SMS conversation directly from ConversationMessage so both
        # inbound (user) and outbound (assistant) sides appear.  Internal tool
        # plumbing — finish_and_submit, enrich_job, raw tool results — is
        # excluded; only turns that produced visible SMS text are returned.
        thread_ids = list(
            obj.conversation_threads.values_list('id', flat=True)
        )
        if not thread_ids:
            return []
        rows = (
            ConversationMessage.objects
            .filter(thread_id__in=thread_ids)
            .order_by('created_at', 'sequence')
        )
        out = []
        for row in rows:
            if row.role == MessageRole.USER:
                text = (row.content or '').strip()
                role = 'user'
            elif row.role == MessageRole.ASSISTANT:
                text = (row.content or '').strip()
                if not text:
                    text = _extract_outbound_text(row.tool_calls)
                role = 'assistant'
            else:
                # MessageRole.TOOL — internal tool execution result.
                continue
            if not text:
                continue
            out.append({
                'id': str(row.id),
                'role': role,
                'content': text,
                'created_at': row.created_at,
            })
        return out


# ---------------------------------------------------------------------------
# ManualJobCreateSerializer
# ---------------------------------------------------------------------------

class ManualJobCreateSerializer(serializers.Serializer):
    # Contact (optional – create or look up)
    contact_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    contact_name = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    contact_phone = serializers.CharField(required=False, allow_blank=True, default='', max_length=30)
    contact_email = serializers.EmailField(required=False, allow_blank=True, default='')

    # Job details
    service_type = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    install_type = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    builder = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    project = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    site = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    community = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    scope = serializers.CharField(required=False, allow_blank=True, default='', max_length=10000)
    quantity = serializers.CharField(required=False, allow_blank=True, default='', max_length=100)
    urgency_notes = serializers.CharField(required=False, allow_blank=True, default='', max_length=500)
    division = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    po_rec_note = serializers.CharField(required=False, allow_blank=True, default='', max_length=500)
    summary = serializers.CharField(required=False, allow_blank=True, default='', max_length=10000)
    notes_text = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=10000,
        help_text='Optional first note to add to the job.',
    )

    priority = serializers.ChoiceField(
        choices=JobPriority.choices, default=JobPriority.NORMAL,
    )
    due_date = serializers.DateField(required=False, allow_null=True)
    po_required = serializers.BooleanField(default=False)
    rec_required = serializers.BooleanField(default=False)

    # NOTE: all fields are intentionally optional so staff can drop a quick
    # placeholder onto the schedule without filling every box. No cross-field
    # "at least one required" validation is enforced here on purpose.


# ---------------------------------------------------------------------------
# JobUpdateSerializer  (PATCH — content fields only, not lifecycle)
# ---------------------------------------------------------------------------

class JobUpdateSerializer(serializers.Serializer):
    service_type = serializers.CharField(required=False, allow_blank=True, max_length=255)
    install_type = serializers.CharField(required=False, allow_blank=True, max_length=255)
    builder = serializers.CharField(required=False, allow_blank=True, max_length=255)
    project = serializers.CharField(required=False, allow_blank=True, max_length=255)
    site = serializers.CharField(required=False, allow_blank=True, max_length=255)
    community = serializers.CharField(required=False, allow_blank=True, max_length=255)
    scope = serializers.CharField(required=False, allow_blank=True, max_length=10000)
    quantity = serializers.CharField(required=False, allow_blank=True, max_length=100)
    urgency_notes = serializers.CharField(required=False, allow_blank=True, max_length=500)
    division = serializers.CharField(required=False, allow_blank=True, max_length=255)
    po_rec_note = serializers.CharField(required=False, allow_blank=True, max_length=500)
    summary = serializers.CharField(required=False, allow_blank=True, max_length=10000)
    ai_summary = serializers.CharField(required=False, allow_blank=True, max_length=10000)
    completion_notes = serializers.CharField(required=False, allow_blank=True, max_length=10000)
    assigned_to = serializers.CharField(required=False, allow_blank=True, max_length=255)
    assignment_notes = serializers.CharField(required=False, allow_blank=True, max_length=500)
    due_date = serializers.DateField(required=False, allow_null=True)
    is_recurring = serializers.BooleanField(required=False)
    contact_phone = serializers.CharField(required=False, allow_blank=True, max_length=30)
    contact_email = serializers.EmailField(required=False, allow_blank=True)
    contact_preferred_callback = serializers.CharField(required=False, allow_blank=True, max_length=255)


# ---------------------------------------------------------------------------
# Action serializers
# ---------------------------------------------------------------------------

class JobStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=JobStatus.choices)
    note = serializers.CharField(required=False, allow_blank=True, default='', max_length=1000)


class JobPriorityUpdateSerializer(serializers.Serializer):
    priority = serializers.ChoiceField(choices=JobPriority.choices)


class JobScheduleSerializer(serializers.Serializer):
    scheduled_date = serializers.DateField()
    scheduled_time = serializers.TimeField(required=False, allow_null=True, default=None)
    assigned_to = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    assignment_notes = serializers.CharField(required=False, allow_blank=True, default='', max_length=500)


class JobUnscheduleSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default='', max_length=1000)


class JobReopenSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default='', max_length=1000)


class JobPoRecSerializer(serializers.Serializer):
    po_required = serializers.BooleanField(required=False)
    rec_required = serializers.BooleanField(required=False)
    po_status = serializers.ChoiceField(choices=PoRecStatus.choices, required=False)
    rec_status = serializers.ChoiceField(choices=PoRecStatus.choices, required=False)
    po_rec_note = serializers.CharField(required=False, allow_blank=True, max_length=500)


class JobSeenSerializer(serializers.Serializer):
    """Toggle the lightweight 'seen / in the works' acknowledgement on a job."""
    is_seen = serializers.BooleanField()


class TeamLeadSerializer(serializers.ModelSerializer):
    """Field team lead used to group the route-calendar daily schedule."""

    class Meta:
        model = TeamLead
        fields = ['id', 'name', 'position', 'is_active']
        read_only_fields = ['id']

    def validate_name(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Name is required.')
        return value


# ---------------------------------------------------------------------------
# Job assign
# ---------------------------------------------------------------------------

class JobAssignSerializer(serializers.Serializer):
    """POST /api/v1/jobs/<pk>/assign/  — assign job to a user (or clear)."""
    user_id = serializers.UUIDField(required=False, allow_null=True)


class AssignableUserSerializer(serializers.Serializer):
    """Compact user shape for the assign-job dropdown."""
    id = serializers.UUIDField()
    display_name = serializers.CharField()
    role = serializers.CharField()
