from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from crm.models import (
    Contact,
    ContactIdentity,
    ContactNote,
    Conversation,
    CrmActivity,
    FollowUpTask,
    Lead,
    Message,
    Pipeline,
    PipelineStage,
    Tag,
)
from crm.services import CrmConflict, add_identity, duplicate_suggestions, normalize_identity


def model_validation(instance):
    try:
        instance.full_clean()
    except DjangoValidationError as exc:
        raise serializers.ValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages) from exc


def membership_name(membership):
    if not membership:
        return None
    return membership.user.get_full_name().strip() or membership.user.email


class TagSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)

    class Meta:
        model = Tag
        fields = ["id", "organization", "name", "color_token", "created_at"]
        read_only_fields = ["id", "organization", "created_at"]

    def create(self, validated_data):
        instance = Tag(organization=self.context["request"].organization, **validated_data)
        model_validation(instance)
        instance.save()
        return instance


class ContactIdentitySerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)

    class Meta:
        model = ContactIdentity
        fields = [
            "id", "organization", "contact", "type", "raw_value", "normalized_value",
            "external_user_id", "channel_connection", "is_primary", "is_verified", "metadata",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "organization", "contact", "normalized_value", "created_at", "updated_at"]
        extra_kwargs = {
            "channel_connection": {"required": False, "allow_null": True},
        }

    def validate_channel_connection(self, value):
        if value and value.organization_id != self.context["request"].organization.id:
            raise serializers.ValidationError("Channel connection was not found.")
        return value

    def create(self, validated_data):
        try:
            return add_identity(
                organization=self.context["request"].organization,
                contact=self.context["contact"],
                identity_type=validated_data.pop("type"),
                raw_value=validated_data.pop("raw_value"),
                **validated_data,
            )
        except CrmConflict as exc:
            raise serializers.ValidationError({"raw_value": str(exc)}, code="conflict") from exc

    def update(self, instance, validated_data):
        for key, value in validated_data.items():
            setattr(instance, key, value)
        if "raw_value" in validated_data or "type" in validated_data:
            instance.normalized_value = normalize_identity(instance.type, instance.raw_value)
        model_validation(instance)
        instance.save()
        return instance


class ContactNoteSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = ContactNote
        fields = ["id", "contact", "author_membership", "author_name", "body", "created_at", "updated_at"]
        read_only_fields = ["id", "contact", "author_membership", "author_name", "created_at", "updated_at"]

    def get_author_name(self, obj):
        return membership_name(obj.author_membership)


class ContactSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)
    identities = ContactIdentitySerializer(many=True, read_only=True)
    tags = TagSerializer(many=True, read_only=True)
    duplicate_suggestions = serializers.SerializerMethodField()

    class Meta:
        model = Contact
        fields = [
            "id", "organization", "display_name", "first_name", "last_name", "company_name",
            "preferred_language", "timezone", "notes_summary", "status", "merged_into",
            "identities", "tags", "duplicate_suggestions", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "organization", "merged_into", "created_at", "updated_at"]

    def get_duplicate_suggestions(self, obj):
        if not self.context.get("include_duplicates"):
            return []
        return [
            {"id": str(item.id), "display_name": item.display_name, "company_name": item.company_name}
            for item in duplicate_suggestions(obj)
        ]

    def update(self, instance, validated_data):
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.updated_by = self.context["request"].organization_membership
        model_validation(instance)
        instance.save()
        return instance


class MessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            "id", "conversation", "direction", "sender_type", "sender_membership", "sender_name",
            "provider_message_id", "client_message_id", "content_type", "body", "status", "error_code",
            "reply_to", "metadata", "occurred_at", "created_at", "updated_at",
        ]
        read_only_fields = fields

    def get_sender_name(self, obj):
        if obj.sender_type == "customer":
            return obj.conversation.contact.display_name
        if obj.sender_type == "system":
            return "System"
        if obj.sender_type == "ai":
            return "AI assistant"
        return membership_name(obj.sender_membership)


class ConversationSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)
    contact_name = serializers.CharField(source="contact.display_name", read_only=True)
    contact_status = serializers.CharField(source="contact.status", read_only=True)
    channel_name = serializers.CharField(source="channel_connection.display_name", read_only=True)
    channel_provider = serializers.CharField(source="channel_connection.provider", read_only=True)
    assigned_name = serializers.SerializerMethodField()
    last_message_preview = serializers.CharField(read_only=True, default="")
    can_send = serializers.SerializerMethodField()
    provider_context = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id", "organization", "channel_connection", "channel_type", "channel_name", "channel_provider",
            "external_thread_id", "contact", "contact_name", "contact_status", "status", "priority",
            "assignment_state", "assigned_membership", "assigned_name", "automation_state", "handoff_reason",
            "ai_state", "ai_state_updated_at",
            "unread_count", "last_message_preview", "can_send", "provider_context", "last_message_at", "last_inbound_at",
            "last_outbound_at", "subject", "created_at", "updated_at", "resolved_at",
        ]
        read_only_fields = [
            "id", "organization", "channel_connection", "channel_type", "channel_name", "channel_provider",
            "external_thread_id", "contact", "contact_name", "contact_status", "assignment_state",
            "assigned_membership", "assigned_name", "unread_count", "last_message_preview", "can_send", "provider_context",
            "last_message_at", "last_inbound_at", "last_outbound_at", "created_at", "updated_at", "resolved_at",
            "ai_state_updated_at",
        ]

    def get_assigned_name(self, obj):
        return membership_name(obj.assigned_membership)

    def get_can_send(self, obj):
        from django.conf import settings
        from crm.services import is_internal_test_connection

        if settings.ENABLE_CRM_TEST_CHANNEL and is_internal_test_connection(obj.channel_connection):
            return True
        if obj.channel_type == "instagram":
            from instagram.services import window_eligibility

            policy = window_eligibility(obj)
            return bool(policy.get("can_send") or policy.get("human_agent_available"))
        if obj.channel_type == "telegram":
            from telegram.services import can_send_telegram

            return can_send_telegram(obj)
        if obj.channel_type == "gmail":
            from gmail_integration.services import can_send_gmail

            return can_send_gmail(obj)
        if obj.channel_type == "sms":
            from sms.services import can_send_sms

            return can_send_sms(obj)
        try:
            from web_chat.services import can_send_public_web_chat

            return can_send_public_web_chat(obj)
        except ImportError:
            return False

    def get_provider_context(self, obj):
        if obj.channel_type == "instagram":
            from instagram.services import serialize_conversation_policy

            return serialize_conversation_policy(obj)
        if obj.channel_type == "telegram":
            from telegram.services import conversation_policy

            return conversation_policy(obj)
        if obj.channel_type == "gmail":
            from gmail_integration.services import conversation_policy

            return conversation_policy(obj)
        if obj.channel_type == "sms":
            from sms.services import conversation_policy

            return conversation_policy(obj)
        return {}


class PipelineStageSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)

    class Meta:
        model = PipelineStage
        fields = ["id", "organization", "pipeline", "name", "position", "color_token", "stage_type", "is_active"]
        read_only_fields = ["id", "organization", "pipeline"]

    def update(self, instance, validated_data):
        for key, value in validated_data.items():
            setattr(instance, key, value)
        model_validation(instance)
        instance.save()
        return instance


class PipelineSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)
    stages = PipelineStageSerializer(many=True, read_only=True)

    class Meta:
        model = Pipeline
        fields = ["id", "organization", "name", "is_default", "is_active", "stages", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "stages", "created_at", "updated_at"]


class LeadSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)
    contact_name = serializers.CharField(source="contact.display_name", read_only=True)
    stage_name = serializers.CharField(source="stage.name", read_only=True)
    stage_type = serializers.CharField(source="stage.stage_type", read_only=True)
    pipeline_name = serializers.CharField(source="pipeline.name", read_only=True)
    assigned_name = serializers.SerializerMethodField()

    class Meta:
        model = Lead
        fields = [
            "id", "organization", "contact", "contact_name", "source_conversation", "source_channel_type",
            "pipeline", "pipeline_name", "stage", "stage_name", "stage_type", "title", "description",
            "assigned_membership", "assigned_name", "estimated_value", "currency", "status", "lost_reason",
            "next_follow_up_at", "created_at", "updated_at", "won_at", "lost_at",
        ]
        read_only_fields = [
            "id", "organization", "contact_name", "source_channel_type", "pipeline_name", "stage_name",
            "stage_type", "assigned_name", "status", "created_at", "updated_at", "won_at", "lost_at",
        ]

    def get_assigned_name(self, obj):
        return membership_name(obj.assigned_membership)

    def validate(self, attrs):
        organization = self.context["request"].organization
        for key in ("contact", "source_conversation", "pipeline", "stage", "assigned_membership"):
            value = attrs.get(key)
            if value and value.organization_id != organization.id:
                raise serializers.ValidationError({key: "The selected resource was not found."})
        pipeline = attrs.get("pipeline", getattr(self.instance, "pipeline", None))
        stage = attrs.get("stage", getattr(self.instance, "stage", None))
        if pipeline and stage and stage.pipeline_id != pipeline.id:
            raise serializers.ValidationError({"stage": "Stage does not belong to this pipeline."})
        return attrs

    def update(self, instance, validated_data):
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.updated_by = self.context["request"].organization_membership
        model_validation(instance)
        instance.save()
        return instance


class FollowUpTaskSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)
    assigned_name = serializers.SerializerMethodField()
    contact_name = serializers.CharField(source="related_contact.display_name", read_only=True, default=None)
    lead_title = serializers.CharField(source="related_lead.title", read_only=True, default=None)

    class Meta:
        model = FollowUpTask
        fields = [
            "id", "organization", "title", "due_at", "status", "assigned_membership", "assigned_name",
            "related_contact", "contact_name", "related_lead", "lead_title", "related_conversation",
            "completed_at", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "organization", "assigned_name", "contact_name", "lead_title", "completed_at",
            "created_at", "updated_at",
        ]

    def get_assigned_name(self, obj):
        return membership_name(obj.assigned_membership)

    def validate(self, attrs):
        organization = self.context["request"].organization
        for key in ("assigned_membership", "related_contact", "related_lead", "related_conversation"):
            value = attrs.get(key)
            if value and value.organization_id != organization.id:
                raise serializers.ValidationError({key: "The selected resource was not found."})
        return attrs


class CrmActivitySerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = CrmActivity
        fields = [
            "id", "event_type", "actor_membership", "actor_name", "contact_id", "conversation_id",
            "lead_id", "task_id", "summary", "metadata", "created_at",
        ]
        read_only_fields = fields

    def get_actor_name(self, obj):
        return membership_name(obj.actor_membership)
