import logging

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import (
    Contact,
    ConversationMessage,
    ConversationThread,
    Interaction,
    InternalNote,
    JobEvent,
    JobNote,
    JobNumberCounter,
    JobRecord,
    KnowledgeBaseEntry,
    SystemPrompt,
    TeamLead,
)

logger = logging.getLogger(__name__)


class InternalNoteInline(admin.TabularInline):
    model = InternalNote
    extra = 0
    fields = ['body', 'created_at', 'created_by']
    readonly_fields = ['created_at', 'created_by']
    verbose_name = _('Internal Note')
    verbose_name_plural = _('Internal Notes')


class JobNoteInline(admin.TabularInline):
    model = JobNote
    extra = 0
    fields = ['body', 'is_follow_up', 'created_at', 'created_by']
    readonly_fields = ['created_at', 'created_by']
    verbose_name = _('Job Note')
    verbose_name_plural = _('Job Notes')


class JobEventInline(admin.TabularInline):
    model = JobEvent
    extra = 0
    fields = ['event_type', 'old_value', 'new_value', 'note', 'created_at', 'created_by']
    readonly_fields = ['event_type', 'old_value', 'new_value', 'note', 'created_at', 'created_by']
    can_delete = False
    max_num = 0
    ordering = ['-created_at']
    verbose_name = _('History Event')
    verbose_name_plural = _('History')


class CreatedJobsInline(admin.TabularInline):
    model = JobRecord
    fk_name = 'source_interaction'
    extra = 0
    fields = ['id', 'service_type', 'builder', 'status', 'priority', 'created_at']
    readonly_fields = ['id', 'created_at']
    show_change_link = True
    verbose_name = _('Created Job')
    verbose_name_plural = _('Created Jobs')


class InteractionInline(admin.TabularInline):
    model = Interaction
    fk_name = 'related_job'
    extra = 0
    fields = ['id', 'channel', 'category', 'status', 'caller_phone', 'caller_email', 'created_at']
    readonly_fields = ['id', 'channel', 'category', 'status', 'caller_phone', 'caller_email', 'created_at']
    show_change_link = True
    verbose_name = _('Interaction')
    verbose_name_plural = _('Interactions')


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'email', 'source', 'created_at']
    list_filter = ['source', 'created_at']
    search_fields = ['name', 'phone', 'email']
    readonly_fields = ['id', 'created_at', 'updated_at', 'created_by', 'updated_by']
    fieldsets = (
        (_('Identity'), {
            'fields': ('id', 'name', 'phone', 'email', 'source'),
        }),
        (_('Additional'), {
            'fields': ('notes', 'metadata'),
            'classes': ('collapse',),
        }),
        (_('Audit'), {
            'fields': ('created_at', 'updated_at', 'created_by', 'updated_by'),
            'classes': ('collapse',),
        }),
    )


@admin.register(Interaction)
class InteractionAdmin(admin.ModelAdmin):
    list_display = [
        'id_short', 'channel', 'category_badge', 'status',
        'contact', 'caller_phone', 'caller_email',
        'is_read', 'related_job_link', 'created_at',
    ]
    list_filter = ['channel', 'category', 'status', 'is_read', 'created_at']
    search_fields = [
        'caller_phone', 'caller_email', 'provider_id',
        'contact__name', 'contact__phone', 'contact__email',
        'escalation_reason',
    ]
    readonly_fields = [
        'id', 'created_at', 'updated_at', 'created_by', 'updated_by',
        'related_job_link',
    ]
    raw_id_fields = ['contact', 'related_job']
    inlines = [CreatedJobsInline, InternalNoteInline]
    list_select_related = ['contact', 'related_job']
    actions = ['mark_as_read', 'mark_as_unread']
    fieldsets = (
        (_('Identity'), {
            'fields': ('id', 'channel', 'category', 'status'),
        }),
        (_('Sender'), {
            'fields': ('contact', 'caller_phone', 'caller_email'),
        }),
        (_('Content'), {
            'fields': ('summary', 'raw_content', 'raw_content_reference'),
        }),
        (_('Escalation'), {
            'fields': ('is_read', 'escalation_reason'),
        }),
        (_('Provider Data'), {
            'fields': ('provider_id', 'provider_payload'),
            'classes': ('collapse',),
        }),
        (_('Classification'), {
            'fields': ('classification_confidence', 'classification_reason'),
        }),
        (_('Related Job'), {
            'fields': ('related_job', 'related_job_link'),
        }),
        (_('Audit'), {
            'fields': ('created_at', 'updated_at', 'created_by', 'updated_by'),
            'classes': ('collapse',),
        }),
    )

    @admin.display(description=_('Category'))
    def category_badge(self, obj: Interaction) -> str:
        if obj.category == 'escalation':
            return format_html(
                '<strong style="color:#c0392b">&#9888; {}</strong>',
                obj.get_category_display(),
            )
        return obj.get_category_display()

    @admin.action(description=_('Mark selected as read / acknowledged'))
    def mark_as_read(self, request, queryset):
        updated = queryset.filter(is_read=False).update(is_read=True)
        self.message_user(request, f'{updated} interaction(s) marked as read.')

    @admin.action(description=_('Mark selected as unread'))
    def mark_as_unread(self, request, queryset):
        updated = queryset.filter(is_read=True).update(is_read=False)
        self.message_user(request, f'{updated} interaction(s) marked as unread.')

    @admin.display(description=_('ID'))
    def id_short(self, obj: Interaction) -> str:
        return str(obj.id)[:8]

    @admin.display(description=_('Related Job'))
    def related_job_link(self, obj: Interaction) -> str:
        if obj.related_job_id:
            url = reverse('admin:intake_jobrecord_change', args=[obj.related_job_id])
            return format_html('<a href="{}">{}</a>', url, obj.related_job)
        return '—'


@admin.register(JobRecord)
class JobRecordAdmin(admin.ModelAdmin):
    list_display = [
        'job_number', 'service_type', 'builder', 'source_channel',
        'status', 'priority', 'is_seen', 'scheduled_date', 'assigned_to',
        'po_rec_flags', 'contact', 'created_at',
    ]
    list_filter = [
        'status', 'priority', 'source_channel', 'is_seen',
        'po_required', 'rec_required', 'po_status', 'rec_status',
        'service_type', 'created_at', 'scheduled_date',
    ]
    search_fields = [
        'job_number', 'service_type', 'builder', 'project', 'site', 'community',
        'assigned_to', 'contact__name', 'contact__phone', 'contact__email',
    ]
    readonly_fields = [
        'id', 'job_number', 'created_at', 'updated_at', 'created_by', 'updated_by',
        'source_interaction_link', 'reopened_at', 'reopened_count', 'seen_at',
    ]
    raw_id_fields = ['contact', 'source_interaction']
    inlines = [InteractionInline, JobNoteInline, JobEventInline, InternalNoteInline]
    list_select_related = ['contact', 'source_interaction']
    fieldsets = (
        (_('Identity'), {
            'fields': ('id', 'job_number', 'status', 'priority', 'source_channel',
                       'is_seen', 'seen_at'),
        }),
        (_('Requester'), {
            'fields': ('contact', 'source_interaction', 'source_interaction_link'),
        }),
        (_('Job Details'), {
            'fields': ('service_type', 'install_type', 'builder', 'project', 'site', 'community', 'division'),
        }),
        (_('Scope & Urgency'), {
            'fields': ('scope', 'quantity', 'urgency_notes', 'po_rec_note'),
        }),
        (_('Summary & Notes'), {
            'fields': ('summary', 'ai_summary', 'completion_notes'),
        }),
        (_('Scheduling'), {
            'fields': ('scheduled_date', 'scheduled_time', 'assigned_to', 'assignment_notes'),
        }),
        (_('PO / REQ'), {
            'fields': ('po_required', 'rec_required', 'po_status', 'rec_status'),
        }),
        (_('Reopen History'), {
            'fields': ('reopened_at', 'reopened_count'),
            'classes': ('collapse',),
        }),
        (_('Audit'), {
            'fields': ('created_at', 'updated_at', 'created_by', 'updated_by'),
            'classes': ('collapse',),
        }),
    )

    @admin.display(description=_('ID'))
    def id_short(self, obj: JobRecord) -> str:
        return str(obj.id)[:8]

    @admin.display(description=_('Source Interaction'))
    def source_interaction_link(self, obj: JobRecord) -> str:
        if obj.source_interaction_id:
            url = reverse('admin:intake_interaction_change', args=[obj.source_interaction_id])
            return format_html('<a href="{}">{}</a>', url, obj.source_interaction)
        return '—'

    @admin.display(description=_('PO / REQ'))
    def po_rec_flags(self, obj: JobRecord) -> str:
        flags = []
        if obj.po_required and obj.po_status != 'received':
            flags.append('PO⚠')
        if obj.rec_required and obj.rec_status != 'received':
            flags.append('REQ⚠')
        return ' '.join(flags) if flags else '✓'


@admin.register(InternalNote)
class InternalNoteAdmin(admin.ModelAdmin):
    list_display = ['id', 'interaction', 'job', 'created_at', 'created_by']
    list_filter = ['created_at']
    search_fields = ['interaction__provider_id', 'job__service_type']
    readonly_fields = ['id', 'created_at', 'updated_at', 'created_by', 'updated_by']
    raw_id_fields = ['interaction', 'job']
    fieldsets = (
        (_('Note'), {
            'fields': ('id', 'body', 'interaction', 'job'),
        }),
        (_('Audit'), {
            'fields': ('created_at', 'updated_at', 'created_by', 'updated_by'),
            'classes': ('collapse',),
        }),
    )


@admin.register(JobNote)
class JobNoteAdmin(admin.ModelAdmin):
    list_display = ['id', 'job', 'is_follow_up', 'created_at', 'created_by']
    list_filter = ['is_follow_up', 'created_at']
    search_fields = ['job__service_type', 'job__builder']
    readonly_fields = ['id', 'created_at', 'updated_at', 'created_by', 'updated_by']
    raw_id_fields = ['job']
    fieldsets = (
        (_('Note'), {
            'fields': ('id', 'body', 'is_follow_up', 'job'),
        }),
        (_('Audit'), {
            'fields': ('created_at', 'updated_at', 'created_by', 'updated_by'),
            'classes': ('collapse',),
        }),
    )


@admin.register(JobEvent)
class JobEventAdmin(admin.ModelAdmin):
    list_display = [
        'event_type_badge', 'job_link', 'note', 'old_value', 'new_value',
        'created_at', 'created_by',
    ]
    list_filter = ['event_type', 'created_at']
    search_fields = ['job__service_type', 'job__builder', 'job__site', 'note']
    readonly_fields = [
        'id', 'job', 'event_type', 'old_value', 'new_value',
        'note', 'created_at', 'created_by',
    ]
    raw_id_fields = ['job']
    ordering = ['-created_at']

    ALERT_TYPES = {'urgent_flagged', 'missing_auth_flagged'}

    @admin.display(description='Event type')
    def event_type_badge(self, obj):
        from django.utils.html import format_html
        if obj.event_type in self.ALERT_TYPES:
            return format_html(
                '<strong style="color:#c0392b">{}</strong>',
                obj.get_event_type_display(),
            )
        return obj.get_event_type_display()

    @admin.display(description='Job')
    def job_link(self, obj):
        from django.urls import reverse
        from django.utils.html import format_html
        url = reverse('admin:intake_jobrecord_change', args=[obj.job_id])
        return format_html('<a href="{}">{}</a>', url, str(obj.job))

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(TeamLead)
class TeamLeadAdmin(admin.ModelAdmin):
    list_display = ['name', 'position', 'is_active', 'updated_at']
    list_filter = ['is_active']
    search_fields = ['name']
    ordering = ['position']


@admin.register(JobNumberCounter)
class JobNumberCounterAdmin(admin.ModelAdmin):
    list_display = ['prefix', 'last_value']
    readonly_fields = ['prefix']

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SystemPrompt)
class SystemPromptAdmin(admin.ModelAdmin):
    list_display = ['key', 'is_active', 'description']
    list_filter = ['is_active']
    search_fields = ['key', 'description']
    readonly_fields = []
    fieldsets = (
        (_('Prompt'), {
            'fields': ('key', 'is_active', 'description', 'text'),
        }),
    )


@admin.register(KnowledgeBaseEntry)
class KnowledgeBaseEntryAdmin(admin.ModelAdmin):
    list_display = ['topic', 'question_short', 'is_active', 'updated_at']
    list_filter = ['topic', 'is_active']
    search_fields = ['topic', 'question', 'answer', 'tags']
    readonly_fields = ['id', 'created_at', 'updated_at']
    fieldsets = (
        (_('Entry'), {
            'fields': ('id', 'topic', 'is_active', 'tags'),
        }),
        (_('Content'), {
            'fields': ('question', 'answer'),
        }),
        (_('Audit'), {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    @admin.display(description=_('Question'))
    def question_short(self, obj: KnowledgeBaseEntry) -> str:
        return obj.question[:80]


class ConversationMessageInline(admin.TabularInline):
    model = ConversationMessage
    extra = 0
    fields = ['sequence', 'role', 'tool_name', 'content', 'tool_call_id', 'created_at']
    readonly_fields = ['sequence', 'role', 'tool_name', 'content', 'tool_call_id', 'created_at']
    can_delete = False
    max_num = 0
    ordering = ['sequence']
    verbose_name = _('Message')
    verbose_name_plural = _('Messages')


@admin.register(ConversationThread)
class ConversationThreadAdmin(admin.ModelAdmin):
    list_display = ['phone_masked', 'contact', 'state', 'is_active', 'active_job_link', 'last_message_at']
    list_filter = ['state', 'is_active', 'created_at']
    search_fields = ['contact__name', 'contact__phone']
    readonly_fields = ['id', 'phone', 'contact', 'active_job', 'state', 'is_active', 'last_message_at', 'created_at']
    raw_id_fields = []
    inlines = [ConversationMessageInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description=_('Phone'))
    def phone_masked(self, obj: ConversationThread) -> str:
        p = obj.phone or ''
        return p[:3] + '***' + p[-3:] if len(p) >= 7 else '***'

    @admin.display(description=_('Active Job'))
    def active_job_link(self, obj: ConversationThread) -> str:
        if obj.active_job_id:
            url = reverse('admin:intake_jobrecord_change', args=[obj.active_job_id])
            return format_html('<a href="{}">{}</a>', url, str(obj.active_job))
        return '—'


@admin.register(ConversationMessage)
class ConversationMessageAdmin(admin.ModelAdmin):
    list_display = ['thread', 'sequence', 'role', 'tool_name', 'created_at']
    list_filter = ['role', 'created_at']
    search_fields = ['thread__phone', 'tool_call_id', 'tool_name']
    readonly_fields = [
        'id', 'thread', 'sequence', 'role', 'content',
        'tool_calls', 'tool_call_id', 'tool_name', 'created_at',
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
