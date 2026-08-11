from __future__ import annotations

from django.conf import settings
from rest_framework import serializers

from ai_runtime.models import (
    AIDraft,
    AIHandoff,
    AIRun,
    AIToolCall,
    AIToolPolicy,
    OrganizationAIRuntimeConfig,
)
from ai_runtime.tools import TOOL_REGISTRY
from assistant_context.models import AssistantContextRevision
from channels.models import ChannelConnection
from crm.services import is_internal_test_connection


def _member_name(member):
    if not member:
        return None
    return member.user.get_full_name().strip() or member.user.email


class RuntimeConfigSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)
    allowed_channel_connections = serializers.PrimaryKeyRelatedField(
        many=True, queryset=ChannelConnection.objects.all(), required=False
    )
    published_context_version = serializers.SerializerMethodField()
    provider_status = serializers.SerializerMethodField()
    real_openai_enabled = serializers.BooleanField(source="_real_openai_enabled", read_only=True, default=False)

    class Meta:
        model = OrganizationAIRuntimeConfig
        fields = [
            "id", "organization", "enabled", "default_mode", "provider", "model",
            "max_output_tokens", "max_tool_rounds", "timeout_seconds", "inbound_debounce_seconds",
            "daily_run_limit", "monthly_input_token_limit", "monthly_output_token_limit",
            "allowed_channel_connections", "published_context_version", "provider_status",
            "real_openai_enabled", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "organization", "provider_status", "created_at", "updated_at"]

    def get_published_context_version(self, obj):
        revision = AssistantContextRevision.objects.filter(organization=obj.organization).order_by("-version").first()
        return revision.version if revision else None

    def get_provider_status(self, obj):
        if obj.provider == "fake":
            return "fake_ready"
        if not settings.AI_RUNTIME_ENABLE_REAL_OPENAI:
            return "real_openai_disabled"
        return "openai_ready" if settings.OPENAI_API_KEY else "openai_key_missing"

    def validate_allowed_channel_connections(self, values):
        organization = self.context["request"].organization
        for value in values:
            is_public_web_chat = value.type == "webchat" and value.provider == "public_web_chat"
            is_supported_external = (value.type, value.provider) in {
                ("instagram", "meta_instagram"),
                ("telegram", "telegram_bot_api"),
                ("gmail", "google_gmail"),
            }
            if value.organization_id != organization.id or not (
                is_internal_test_connection(value)
                or is_public_web_chat
                or is_supported_external
            ):
                raise serializers.ValidationError(
                    "Only this organization's supported CRM messaging channels are allowed."
                )
        return values

    def validate(self, attrs):
        mode = attrs.get("default_mode", getattr(self.instance, "default_mode", "off"))
        provider = attrs.get("provider", getattr(self.instance, "provider", "fake"))
        if mode == "autopilot_test" and not (
            settings.AI_INTERNAL_TEST_AUTOPILOT and (settings.DEBUG or settings.TESTING or settings.E2E_TESTING)
        ):
            raise serializers.ValidationError({"default_mode": "Internal test autopilot is not enabled in this environment."})
        if provider == "openai" and not settings.AI_RUNTIME_ENABLE_REAL_OPENAI:
            raise serializers.ValidationError({"provider": "Real OpenAI calls require AI_RUNTIME_ENABLE_REAL_OPENAI=true."})
        return attrs


class ToolPolicySerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)
    description = serializers.SerializerMethodField()
    mutating = serializers.SerializerMethodField()

    class Meta:
        model = AIToolPolicy
        fields = [
            "id", "organization", "tool_name", "description", "mutating", "enabled",
            "execution_mode", "configuration", "version", "updated_at",
        ]
        read_only_fields = ["id", "organization", "tool_name", "description", "mutating", "version", "updated_at"]

    def get_description(self, obj):
        return TOOL_REGISTRY[obj.tool_name].description

    def get_mutating(self, obj):
        return TOOL_REGISTRY[obj.tool_name].mutating


class ToolCallSerializer(serializers.ModelSerializer):
    approved_by_name = serializers.SerializerMethodField()

    class Meta:
        model = AIToolCall
        fields = [
            "id", "tool_name", "provider_call_id", "input_redacted", "output_redacted", "status",
            "requires_approval", "approved_by", "approved_by_name", "approved_at", "error_category",
            "duration_ms", "created_at", "completed_at",
        ]
        read_only_fields = fields

    def get_approved_by_name(self, obj):
        return _member_name(obj.approved_by)


class DraftSerializer(serializers.ModelSerializer):
    approved_by_name = serializers.SerializerMethodField()
    rejected_by_name = serializers.SerializerMethodField()

    class Meta:
        model = AIDraft
        fields = [
            "id", "run", "conversation", "body", "language", "status", "approved_by",
            "approved_by_name", "rejected_by", "rejected_by_name", "rejection_reason",
            "created_at", "updated_at", "acted_at",
        ]
        read_only_fields = fields

    def get_approved_by_name(self, obj):
        return _member_name(obj.approved_by)

    def get_rejected_by_name(self, obj):
        return _member_name(obj.rejected_by)


class HandoffSerializer(serializers.ModelSerializer):
    assigned_name = serializers.SerializerMethodField()

    class Meta:
        model = AIHandoff
        fields = [
            "id", "conversation", "run", "reason_code", "safe_summary", "requested_by", "status",
            "assigned_membership", "assigned_name", "created_at", "acknowledged_at", "resolved_at",
        ]
        read_only_fields = fields

    def get_assigned_name(self, obj):
        return _member_name(obj.assigned_membership)


class RunSerializer(serializers.ModelSerializer):
    tool_calls = ToolCallSerializer(many=True, read_only=True)
    draft = serializers.SerializerMethodField()
    handoffs = HandoffSerializer(many=True, read_only=True)

    class Meta:
        model = AIRun
        fields = [
            "id", "conversation", "trigger_message", "status", "mode", "provider", "model",
            "ai_context_revision", "prompt_template_version", "prompt_hash", "response_id",
            "provider_request_id", "input_tokens", "output_tokens", "cached_tokens", "latency_ms",
            "tool_rounds", "outcome", "response_language", "error_category", "error_code",
            "started_at", "completed_at", "created_at", "tool_calls", "draft", "handoffs",
        ]
        read_only_fields = fields

    def get_draft(self, obj):
        try:
            return DraftSerializer(obj.draft).data
        except AIDraft.DoesNotExist:
            return None
