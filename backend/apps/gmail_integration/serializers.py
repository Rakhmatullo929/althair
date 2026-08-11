from rest_framework import serializers

from gmail_integration.models import (
    GmailAutomationMode,
    GmailConnection,
    GmailNotificationStatus,
    GmailOutboundStatus,
    GmailSyncRun,
)
from gmail_integration.services import (
    connection_health,
    renew_watch,
    verification_checklist,
)


class GmailSyncRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = GmailSyncRun
        fields = [
            "id", "sync_type", "status", "start_history_id", "end_history_id",
            "imported_count", "ignored_count", "fallback_reason", "safe_error_code",
            "started_at", "completed_at",
        ]
        read_only_fields = fields


class GmailConnectionSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)
    channel_connection = serializers.UUIDField(source="channel_connection_id", read_only=True)
    health = serializers.SerializerMethodField()
    verification_checklist = serializers.SerializerMethodField()
    recent_sync_runs = serializers.SerializerMethodField()
    has_encrypted_access_token = serializers.SerializerMethodField()
    has_encrypted_refresh_token = serializers.SerializerMethodField()
    operations = serializers.SerializerMethodField()

    class Meta:
        model = GmailConnection
        fields = [
            "id", "organization", "channel_connection", "mailbox_email", "mailbox_name",
            "google_user_id", "scope_snapshot", "connection_status", "automation_mode",
            "initial_sync_mode", "initial_sync_status", "initial_sync_max_messages",
            "included_label_ids", "excluded_label_ids", "sync_start_at",
            "initial_sync_cancel_requested_at", "retention_days",
            "history_id", "watch_expiration_at", "last_notification_at", "last_incremental_sync_at",
            "last_full_sync_at", "last_successful_send_at", "last_health_check_at", "last_error_code",
            "connected_at", "disconnected_at", "has_encrypted_access_token",
            "has_encrypted_refresh_token", "health", "verification_checklist", "recent_sync_runs", "operations",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "organization", "channel_connection", "mailbox_email", "mailbox_name",
            "google_user_id", "scope_snapshot", "connection_status", "initial_sync_status",
            "sync_start_at", "initial_sync_cancel_requested_at", "history_id",
            "watch_expiration_at", "last_notification_at", "last_incremental_sync_at",
            "last_full_sync_at", "last_successful_send_at", "last_health_check_at", "last_error_code",
            "connected_at", "disconnected_at", "has_encrypted_access_token",
            "has_encrypted_refresh_token", "health", "verification_checklist", "recent_sync_runs", "operations",
            "created_at", "updated_at",
        ]

    def validate_automation_mode(self, value):
        if value == GmailAutomationMode.AUTOPILOT:
            from assistant_context.models import AssistantContextRevision
            from ai_runtime.services import ensure_runtime_config

            organization = self.context["request"].organization
            if not AssistantContextRevision.objects.filter(organization=organization).exists():
                raise serializers.ValidationError("Publish AI Context before enabling Gmail autopilot.")
            if not ensure_runtime_config(organization).enabled:
                raise serializers.ValidationError("Enable the AI Runtime before Gmail autopilot.")
        return value

    def get_health(self, obj):
        return connection_health(obj)

    def get_verification_checklist(self, obj):
        return verification_checklist(obj)

    def get_recent_sync_runs(self, obj):
        return GmailSyncRunSerializer(obj.sync_runs.all()[:5], many=True).data

    def get_has_encrypted_access_token(self, obj):
        try:
            return bool(obj.channel_connection.get_credentials().get("access_token"))
        except (TypeError, ValueError):
            return False

    def get_has_encrypted_refresh_token(self, obj):
        try:
            return bool(obj.channel_connection.get_credentials().get("refresh_token"))
        except (TypeError, ValueError):
            return False

    def get_operations(self, obj):
        return {
            "failed_notifications": obj.notifications.filter(
                status__in=[
                    GmailNotificationStatus.FAILED,
                    GmailNotificationStatus.DEAD_LETTER,
                ]
            ).count(),
            "queued_sends": obj.outbound_attempts.filter(
                status=GmailOutboundStatus.QUEUED
            ).count(),
            "failed_sends": obj.outbound_attempts.filter(
                status__in=[
                    GmailOutboundStatus.FAILED,
                    GmailOutboundStatus.DEAD_LETTER,
                ]
            ).count(),
        }

    def update(self, instance, validated_data):
        labels_changed = any(
            key in validated_data
            for key in ("included_label_ids", "excluded_label_ids")
        )
        for key, value in validated_data.items():
            setattr(instance, key, value)
        try:
            instance.full_clean()
        except Exception as exc:
            detail = getattr(exc, "message_dict", None) or {"detail": [str(exc)]}
            raise serializers.ValidationError(detail) from exc
        instance.save()
        if labels_changed and instance.connection_status != "disconnected":
            renew_watch(instance)
        return instance
