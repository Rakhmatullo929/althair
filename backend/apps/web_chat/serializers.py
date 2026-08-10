from __future__ import annotations

import hashlib

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q
from rest_framework import serializers

from assistant_context.models import AssistantContextRevision
from channels.models import ChannelConnection, ChannelStatus, ChannelType
from crm.services import record_activity
from web_chat.models import (
    InstallationAIMode,
    InstallationStatus,
    WebChatInstallation,
    WebChatKeyRotation,
    WebChatSession,
    generate_public_key,
)
from web_chat.services import normalize_allowed_origins


def _validate_model(instance):
    try:
        instance.full_clean()
    except DjangoValidationError as exc:
        raise serializers.ValidationError(
            exc.message_dict if hasattr(exc, "message_dict") else exc.messages
        ) from exc


class WebChatInstallationSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)
    health = serializers.SerializerMethodField()
    session_counts = serializers.SerializerMethodField()
    embed_snippet = serializers.SerializerMethodField()
    public_key = serializers.CharField(read_only=True)
    production_approved = serializers.BooleanField(read_only=True)

    class Meta:
        model = WebChatInstallation
        fields = [
            "id", "organization", "channel_connection", "public_key", "status",
            "display_name", "assistant_label", "greeting", "offline_message",
            "human_handoff_message", "privacy_policy_url", "terms_url", "consent_text",
            "consent_version", "require_consent", "require_prechat_form", "collect_name",
            "collect_email", "collect_phone", "default_language", "supported_languages",
            "default_branch", "allowed_origins", "theme_config", "ai_mode", "retention_days",
            "production_approved", "live_ai_opt_in", "health", "session_counts", "embed_snippet",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "organization", "channel_connection", "public_key", "status",
            "production_approved", "health", "session_counts", "embed_snippet",
            "created_at", "updated_at",
        ]

    def get_health(self, obj):
        return {
            "status": obj.status,
            "origin_count": len(obj.allowed_origins),
            "published_context": AssistantContextRevision.objects.filter(
                organization=obj.organization
            ).exists(),
            "public_api_enabled": bool(self.context.get("public_api_enabled", False)),
            "last_session_at": obj.sessions.order_by("-started_at").values_list("started_at", flat=True).first(),
        }

    def get_session_counts(self, obj):
        values = obj.sessions.aggregate(
            total=Count("id"),
            active=Count("id", filter=Q(status__in=["active", "handed_off"])),
            blocked=Count("id", filter=Q(status="blocked")),
        )
        return values

    def get_embed_snippet(self, obj):
        widget_base = self.context.get("widget_base", "").rstrip("/")
        return (
            f'<script src="{widget_base}/widget.js?v=1" '
            f'data-installation-key="{obj.public_key}" async></script>'
        )

    def validate_allowed_origins(self, value):
        try:
            return normalize_allowed_origins(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc

    def validate_default_branch(self, value):
        if value and value.organization_id != self.context["request"].organization.id:
            raise serializers.ValidationError("Branch was not found.")
        return value

    def validate(self, attrs):
        languages = attrs.get("supported_languages", getattr(self.instance, "supported_languages", ["ru"]))
        default = attrs.get("default_language", getattr(self.instance, "default_language", "ru"))
        if default not in languages:
            raise serializers.ValidationError({"default_language": "Default language must be supported."})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        request = self.context["request"]
        public_key = generate_public_key()
        connection = ChannelConnection(
            organization=request.organization,
            branch=validated_data.get("default_branch"),
            type=ChannelType.WEBCHAT,
            provider="public_web_chat",
            display_name=validated_data.get("display_name", "Website chat"),
            external_identifier=public_key,
            status=ChannelStatus.DRAFT,
            configuration={"provider_version": "v1", "attachments": False},
        )
        connection.full_clean()
        connection.save()
        instance = WebChatInstallation(
            organization=request.organization,
            channel_connection=connection,
            public_key=public_key,
            created_by=request.organization_membership,
            updated_by=request.organization_membership,
            **validated_data,
        )
        _validate_model(instance)
        instance.save()
        record_activity(
            organization=request.organization,
            actor_membership=request.organization_membership,
            event_type="web_chat.installation_created",
            summary="Web Chat installation created",
            metadata={"installation_id": str(instance.id)},
        )
        return instance

    def update(self, instance, validated_data):
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.updated_by = self.context["request"].organization_membership
        _validate_model(instance)
        instance.save()
        instance.channel_connection.branch = instance.default_branch
        instance.sync_connection_status()
        record_activity(
            organization=instance.organization,
            actor_membership=self.context["request"].organization_membership,
            event_type="web_chat.installation_updated",
            summary="Web Chat installation settings updated",
            metadata={"installation_id": str(instance.id)},
        )
        return instance


def activate_installation(installation, actor):
    if not installation.allowed_origins:
        raise serializers.ValidationError({"allowed_origins": "Add an allowed origin before activation."})
    if installation.ai_mode != InstallationAIMode.MANUAL and not AssistantContextRevision.objects.filter(
        organization=installation.organization
    ).exists():
        raise serializers.ValidationError({"ai_mode": "Publish AI Context before enabling AI."})
    installation.status = InstallationStatus.ACTIVE
    installation.updated_by = actor
    _validate_model(installation)
    installation.save()
    installation.sync_connection_status()
    return installation


def rotate_public_key(installation, actor):
    previous = installation.public_key
    WebChatKeyRotation.objects.create(
        organization=installation.organization,
        installation=installation,
        previous_key_hash=hashlib.sha256(previous.encode()).hexdigest(),
        rotated_by=actor,
    )
    installation.public_key = generate_public_key()
    installation.updated_by = actor
    installation.save(update_fields=["public_key", "updated_by", "updated_at"])
    installation.sync_connection_status()
    return installation


class WebChatSessionStaffSerializer(serializers.ModelSerializer):
    conversation = serializers.UUIDField(source="conversation_id", read_only=True)
    contact = serializers.UUIDField(source="contact_id", read_only=True)

    class Meta:
        model = WebChatSession
        fields = [
            "public_session_id", "status", "language", "origin", "conversation", "contact",
            "consented_at", "started_at", "last_seen_at", "expires_at", "closed_at",
            "abuse_score", "first_message_at", "first_response_at",
        ]
