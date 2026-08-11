from rest_framework import serializers

from telegram.models import TelegramAutomationMode, TelegramBotConnection, TelegramManagedBotRequest, TelegramUserLink
from telegram.services import connection_health


class TelegramUserLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = TelegramUserLink
        fields = ["id", "telegram_user_id", "telegram_username", "status", "expires_at", "linked_at", "created_at"]
        read_only_fields = fields


class TelegramManagedBotRequestSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)

    class Meta:
        model = TelegramManagedBotRequest
        fields = ["id", "organization", "linked_telegram_user_id", "suggested_username", "suggested_name", "status", "expires_at", "created_bot_user_id", "created_bot_username", "error_code", "created_at", "updated_at"]
        read_only_fields = fields


class TelegramBotConnectionSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)
    channel_connection = serializers.UUIDField(source="channel_connection_id", read_only=True)
    has_encrypted_token = serializers.SerializerMethodField()
    health = serializers.SerializerMethodField()
    customer_start_url = serializers.SerializerMethodField()

    class Meta:
        model = TelegramBotConnection
        fields = ["id", "organization", "channel_connection", "connection_type", "bot_user_id", "bot_username", "bot_name", "owner_telegram_user_id", "status", "token_version", "webhook_status", "allowed_updates", "access_restricted", "permitted_telegram_user_ids", "default_language", "supported_languages", "privacy_url", "automation_mode", "last_update_at", "last_send_at", "last_health_check_at", "last_error_code", "connected_at", "disconnected_at", "has_encrypted_token", "health", "customer_start_url", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "channel_connection", "connection_type", "bot_user_id", "bot_username", "bot_name", "owner_telegram_user_id", "status", "token_version", "webhook_status", "allowed_updates", "last_update_at", "last_send_at", "last_health_check_at", "last_error_code", "connected_at", "disconnected_at", "has_encrypted_token", "health", "customer_start_url", "created_at", "updated_at"]

    def validate_automation_mode(self, value):
        if value == TelegramAutomationMode.AUTOPILOT:
            from assistant_context.models import OrganizationAssistantProfile
            from ai_runtime.services import ensure_runtime_config
            organization = self.context["request"].organization
            if not OrganizationAssistantProfile.objects.filter(
                organization=organization,
                status="published",
                published_at__isnull=False,
            ).exists():
                raise serializers.ValidationError("Publish AI Context before enabling Telegram autopilot.")
            if not ensure_runtime_config(organization).enabled:
                raise serializers.ValidationError("Enable the existing AI Runtime before Telegram autopilot.")
        return value

    def validate_permitted_telegram_user_ids(self, value):
        if len(value) > 10 or any(not isinstance(item, int) or item <= 0 for item in value):
            raise serializers.ValidationError("Use up to 10 numeric Telegram user IDs.")
        return value

    def get_has_encrypted_token(self, obj):
        return bool(obj.channel_connection.encrypted_credentials)

    def get_health(self, obj):
        return connection_health(obj)

    def get_customer_start_url(self, obj):
        return f"https://t.me/{obj.bot_username}?start=support" if obj.bot_username else ""
