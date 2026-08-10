from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from assistant_context.models import AssistantContextRevision
from channels.models import ChannelConnection
from crm.models import Conversation, Message, validate_plain_text, validate_safe_metadata
from organizations.models import Organization, OrganizationMembership, OrganizationOwnedModel


class RuntimeMode(models.TextChoices):
    OFF = "off", "Off"
    SUGGEST = "suggest", "Suggest"
    AUTOPILOT_TEST = "autopilot_test", "Internal test autopilot"
    AUTOPILOT_WEB_CHAT = "autopilot_web_chat", "Web Chat autopilot"


class RuntimeProvider(models.TextChoices):
    FAKE = "fake", "Deterministic fake"
    OPENAI = "openai", "OpenAI Responses API"


class OrganizationAIRuntimeConfig(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, related_name="ai_runtime_config"
    )
    enabled = models.BooleanField(default=False)
    default_mode = models.CharField(max_length=20, choices=RuntimeMode.choices, default=RuntimeMode.OFF)
    provider = models.CharField(max_length=20, choices=RuntimeProvider.choices, default=RuntimeProvider.FAKE)
    model = models.CharField(max_length=120, default="configured-model", validators=[validate_plain_text])
    max_output_tokens = models.PositiveIntegerField(
        default=600, validators=[MinValueValidator(64), MaxValueValidator(4000)]
    )
    max_tool_rounds = models.PositiveSmallIntegerField(
        default=2, validators=[MinValueValidator(0), MaxValueValidator(5)]
    )
    timeout_seconds = models.PositiveSmallIntegerField(
        default=30, validators=[MinValueValidator(2), MaxValueValidator(120)]
    )
    inbound_debounce_seconds = models.PositiveSmallIntegerField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(30)]
    )
    daily_run_limit = models.PositiveIntegerField(
        default=100, validators=[MinValueValidator(1), MaxValueValidator(10000)]
    )
    monthly_input_token_limit = models.PositiveIntegerField(
        default=500000, validators=[MinValueValidator(1000), MaxValueValidator(100000000)]
    )
    monthly_output_token_limit = models.PositiveIntegerField(
        default=100000, validators=[MinValueValidator(1000), MaxValueValidator(50000000)]
    )
    allowed_channel_connections = models.ManyToManyField(
        ChannelConnection, blank=True, related_name="ai_runtime_configs"
    )
    updated_by = models.ForeignKey(
        OrganizationMembership,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_runtime_configs_updated",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        if self.updated_by_id and self.updated_by.organization_id != self.organization_id:
            raise ValidationError({"updated_by": "Membership belongs to another organization."})
        if self.provider == RuntimeProvider.OPENAI and not self.model.strip():
            raise ValidationError({"model": "A configured model alias is required."})


class ToolExecutionMode(models.TextChoices):
    AUTOMATIC = "automatic", "Automatic"
    REQUIRE_APPROVAL = "require_approval", "Require approval"
    DISABLED = "disabled", "Disabled"


class AIToolPolicy(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tool_name = models.CharField(max_length=80)
    enabled = models.BooleanField(default=False)
    execution_mode = models.CharField(
        max_length=20, choices=ToolExecutionMode.choices, default=ToolExecutionMode.DISABLED
    )
    configuration = models.JSONField(default=dict, blank=True, validators=[validate_safe_metadata])
    version = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(
        OrganizationMembership,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_tool_policies_updated",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tool_name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "tool_name"], name="unique_org_ai_tool_policy")
        ]

    def clean(self):
        super().clean()
        from ai_runtime.tools import TOOL_REGISTRY

        if self.tool_name not in TOOL_REGISTRY:
            raise ValidationError({"tool_name": "Unknown AI tool."})
        if not self.enabled and self.execution_mode != ToolExecutionMode.DISABLED:
            raise ValidationError({"execution_mode": "A disabled tool must use disabled execution mode."})


class AIRunStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    WAITING_FOR_APPROVAL = "waiting_for_approval", "Waiting for approval"
    COMPLETED = "completed", "Completed"
    HANDOFF = "handoff", "Handoff"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"
    SUPERSEDED = "superseded", "Superseded"


class AIRunOutcome(models.TextChoices):
    NONE = "", "Pending"
    DRAFT = "draft", "Draft"
    SENT_TEST_REPLY = "sent_test_reply", "Sent internal test reply"
    SENT_WEB_CHAT_REPLY = "sent_web_chat_reply", "Sent Web Chat reply"
    HANDOFF = "handoff", "Handoff"
    NO_REPLY = "no_reply", "No reply"
    FAILED = "failed", "Failed"


class AIRun(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="ai_runs")
    trigger_message = models.ForeignKey(Message, on_delete=models.PROTECT, related_name="triggered_ai_runs")
    status = models.CharField(max_length=30, choices=AIRunStatus.choices, default=AIRunStatus.QUEUED, db_index=True)
    mode = models.CharField(max_length=20, choices=RuntimeMode.choices)
    provider = models.CharField(max_length=20, choices=RuntimeProvider.choices)
    model = models.CharField(max_length=120, validators=[validate_plain_text])
    ai_context_revision = models.ForeignKey(
        AssistantContextRevision, on_delete=models.PROTECT, related_name="ai_runs"
    )
    prompt_template_version = models.CharField(max_length=40)
    prompt_hash = models.CharField(max_length=64)
    response_id = models.CharField(max_length=120, blank=True)
    provider_request_id = models.CharField(max_length=120, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cached_tokens = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    tool_rounds = models.PositiveSmallIntegerField(default=0)
    outcome = models.CharField(max_length=30, choices=AIRunOutcome.choices, blank=True)
    response_language = models.CharField(max_length=2, blank=True)
    error_category = models.CharField(max_length=60, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    task_key = models.CharField(max_length=160, unique=True)
    retry_count = models.PositiveSmallIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["organization", "status", "-created_at"]),
            models.Index(fields=["organization", "conversation", "-created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation"],
                condition=Q(
                    status__in=[
                        AIRunStatus.QUEUED,
                        AIRunStatus.RUNNING,
                        AIRunStatus.WAITING_FOR_APPROVAL,
                    ]
                ),
                name="one_active_ai_run_per_conversation",
            )
        ]

    def clean(self):
        super().clean()
        if self.trigger_message.conversation_id != self.conversation_id:
            raise ValidationError({"trigger_message": "Trigger message belongs to another conversation."})
        if self.ai_context_revision.organization_id != self.organization_id:
            raise ValidationError({"ai_context_revision": "AI Context belongs to another organization."})


class AIToolCallStatus(models.TextChoices):
    PROPOSED = "proposed", "Proposed"
    AWAITING_APPROVAL = "awaiting_approval", "Awaiting approval"
    APPROVED = "approved", "Approved"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class AIToolCall(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(AIRun, on_delete=models.CASCADE, related_name="tool_calls")
    tool_name = models.CharField(max_length=80, editable=False)
    provider_call_id = models.CharField(max_length=160, editable=False)
    input_redacted = models.JSONField(default=dict, validators=[validate_safe_metadata], editable=False)
    output_redacted = models.JSONField(default=dict, blank=True, validators=[validate_safe_metadata])
    status = models.CharField(
        max_length=30, choices=AIToolCallStatus.choices, default=AIToolCallStatus.PROPOSED, db_index=True
    )
    idempotency_key = models.CharField(max_length=160, unique=True)
    requires_approval = models.BooleanField(default=True)
    approved_by = models.ForeignKey(
        OrganizationMembership, on_delete=models.SET_NULL, null=True, blank=True, related_name="ai_tool_calls_approved"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    error_category = models.CharField(max_length=60, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["run", "provider_call_id"], name="unique_provider_call_per_run")
        ]


class AIDraftStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    EDITED_AND_SENT = "edited_and_sent", "Edited and sent"
    REJECTED = "rejected", "Rejected"
    EXPIRED = "expired", "Expired"
    SUPERSEDED = "superseded", "Superseded"


class AIDraft(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.OneToOneField(AIRun, on_delete=models.CASCADE, related_name="draft")
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="ai_drafts")
    body = models.TextField(max_length=10000, validators=[validate_plain_text])
    language = models.CharField(max_length=2)
    status = models.CharField(max_length=30, choices=AIDraftStatus.choices, default=AIDraftStatus.PENDING, db_index=True)
    approved_by = models.ForeignKey(
        OrganizationMembership, on_delete=models.SET_NULL, null=True, blank=True, related_name="ai_drafts_approved"
    )
    rejected_by = models.ForeignKey(
        OrganizationMembership, on_delete=models.SET_NULL, null=True, blank=True, related_name="ai_drafts_rejected"
    )
    rejection_reason = models.CharField(max_length=500, blank=True, validators=[validate_plain_text])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    acted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class HandoffRequestedBy(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    AI = "ai", "AI"
    POLICY = "policy", "Policy"
    SYSTEM = "system", "System"


class AIHandoffStatus(models.TextChoices):
    OPEN = "open", "Open"
    ACKNOWLEDGED = "acknowledged", "Acknowledged"
    RESOLVED = "resolved", "Resolved"


class AIHandoff(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="ai_handoffs")
    run = models.ForeignKey(AIRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="handoffs")
    reason_code = models.CharField(max_length=80)
    safe_summary = models.TextField(max_length=2000, validators=[validate_plain_text])
    requested_by = models.CharField(max_length=20, choices=HandoffRequestedBy.choices)
    status = models.CharField(max_length=20, choices=AIHandoffStatus.choices, default=AIHandoffStatus.OPEN, db_index=True)
    assigned_membership = models.ForeignKey(
        OrganizationMembership, on_delete=models.SET_NULL, null=True, blank=True, related_name="ai_handoffs"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "status", "-created_at"])]


class AIUsageEvent(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.OneToOneField(AIRun, on_delete=models.CASCADE, related_name="usage_event")
    provider = models.CharField(max_length=20)
    model = models.CharField(max_length=120)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cached_tokens = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    successful = models.BooleanField(default=False)
    date_bucket = models.DateField(db_index=True)
    month_bucket = models.DateField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["organization", "date_bucket"]),
            models.Index(fields=["organization", "month_bucket"]),
        ]

    @classmethod
    def for_run(cls, run: AIRun):
        today = timezone.localdate()
        return cls.objects.update_or_create(
            run=run,
            defaults={
                "organization": run.organization,
                "provider": run.provider,
                "model": run.model,
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "cached_tokens": run.cached_tokens,
                "latency_ms": run.latency_ms,
                "successful": run.status in {AIRunStatus.COMPLETED, AIRunStatus.HANDOFF},
                "date_bucket": today,
                "month_bucket": today.replace(day=1),
            },
        )[0]


class ConversationSummary(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.OneToOneField(Conversation, on_delete=models.CASCADE, related_name="ai_summary")
    start_message = models.ForeignKey(Message, on_delete=models.PROTECT, related_name="summaries_started")
    end_message = models.ForeignKey(Message, on_delete=models.PROTECT, related_name="summaries_ended")
    body = models.TextField(max_length=2000, validators=[validate_plain_text])
    is_ai_generated = models.BooleanField(default=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        if self.start_message.conversation_id != self.conversation_id or self.end_message.conversation_id != self.conversation_id:
            raise ValidationError("Summary message range belongs to another conversation.")
