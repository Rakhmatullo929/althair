from __future__ import annotations

from rest_framework import serializers

from instagram.models import InstagramAutomationMode, InstagramConnection
from instagram.services import app_review_checklist, connection_health


class InstagramConnectionSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)
    channel_connection = serializers.UUIDField(source="channel_connection_id", read_only=True)
    has_encrypted_token = serializers.SerializerMethodField()
    health = serializers.SerializerMethodField()
    app_review_checklist = serializers.SerializerMethodField()

    class Meta:
        model = InstagramConnection
        fields = [
            "id",
            "organization",
            "channel_connection",
            "instagram_user_id",
            "username",
            "account_type",
            "profile_name",
            "profile_picture_url",
            "profile_picture_expires_at",
            "graph_api_version",
            "permission_snapshot",
            "webhook_subscription_status",
            "connection_status",
            "automation_mode",
            "human_agent_approved",
            "token_expires_at",
            "last_webhook_at",
            "last_successful_send_at",
            "last_health_check_at",
            "last_error_code",
            "connected_at",
            "disconnected_at",
            "has_encrypted_token",
            "health",
            "app_review_checklist",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id", "organization", "channel_connection", "instagram_user_id", "username",
            "account_type", "profile_name", "profile_picture_url", "profile_picture_expires_at",
            "graph_api_version", "permission_snapshot", "webhook_subscription_status",
            "connection_status", "human_agent_approved", "token_expires_at", "last_webhook_at",
            "last_successful_send_at", "last_health_check_at", "last_error_code", "connected_at",
            "disconnected_at", "has_encrypted_token", "health", "app_review_checklist",
            "created_at", "updated_at",
        ]

    def validate_automation_mode(self, value):
        if value == InstagramAutomationMode.AUTOPILOT:
            from assistant_context.models import AssistantContextRevision
            from ai_runtime.services import ensure_runtime_config

            if not AssistantContextRevision.objects.filter(
                organization=self.context["request"].organization
            ).exists():
                raise serializers.ValidationError(
                    "Publish AI Context before enabling Instagram autopilot."
                )
            config = ensure_runtime_config(self.context["request"].organization)
            if not config.enabled:
                raise serializers.ValidationError(
                    "Enable the existing AI Runtime before Instagram autopilot."
                )
        return value

    def get_has_encrypted_token(self, obj):
        return bool(obj.channel_connection.encrypted_credentials)

    def get_health(self, obj):
        return connection_health(obj)

    def get_app_review_checklist(self, obj):
        return app_review_checklist(obj)
