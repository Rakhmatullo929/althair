from rest_framework import serializers

from sms.models import SMSConnection
from sms.services import connection_health, webhook_urls


class SMSConnectionSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)
    channel_connection = serializers.UUIDField(source="channel_connection_id", read_only=True)
    has_auth_token = serializers.SerializerMethodField()
    has_api_key_secret = serializers.SerializerMethodField()
    webhook_urls = serializers.SerializerMethodField()
    health = serializers.SerializerMethodField()

    class Meta:
        model = SMSConnection
        fields = [
            "id", "organization", "channel_connection", "provider", "ownership_mode", "status",
            "account_sid", "messaging_service_sid", "phone_number_sid", "sender_address", "sender_country",
            "sender_capabilities", "api_key_sid", "inbound_webhook_status", "status_callback_status",
            "advanced_opt_out_enabled", "allow_inbound_support", "default_language", "supported_languages",
            "ai_mode", "last_inbound_at", "last_send_at", "last_status_callback_at", "last_health_check_at",
            "last_error_code", "connected_at", "disconnected_at", "created_at", "updated_at",
            "has_auth_token", "has_api_key_secret", "webhook_urls", "health",
        ]
        read_only_fields = [
            "id", "organization", "channel_connection", "provider", "ownership_mode", "status", "account_sid",
            "messaging_service_sid", "phone_number_sid", "sender_address", "sender_country", "sender_capabilities",
            "api_key_sid", "inbound_webhook_status", "status_callback_status", "last_inbound_at", "last_send_at",
            "last_status_callback_at", "last_health_check_at", "last_error_code", "connected_at", "disconnected_at",
            "created_at", "updated_at", "has_auth_token", "has_api_key_secret", "webhook_urls", "health",
        ]

    def get_has_auth_token(self, obj):
        return bool(obj.auth_token_encrypted)

    def get_has_api_key_secret(self, obj):
        return bool(obj.api_key_secret_encrypted)

    def get_webhook_urls(self, obj):
        return webhook_urls(obj)

    def get_health(self, obj):
        return connection_health(obj)


class SMSConnectionCreateSerializer(serializers.Serializer):
    display_name = serializers.CharField(max_length=160, required=False)
    provider = serializers.ChoiceField(choices=["fake", "twilio"], default="fake")
    ownership_mode = serializers.ChoiceField(
        choices=["platform_managed", "customer_owned"], default="platform_managed"
    )
    sender_address = serializers.CharField(max_length=64, required=False, default="+15550109999")
    sender_country = serializers.CharField(max_length=2, required=False, allow_blank=True)
    account_sid = serializers.CharField(max_length=64, required=False, allow_blank=True, write_only=True)
    messaging_service_sid = serializers.CharField(max_length=64, required=False, allow_blank=True)
    phone_number_sid = serializers.CharField(max_length=64, required=False, allow_blank=True)
    api_key_sid = serializers.CharField(max_length=64, required=False, allow_blank=True, write_only=True)
    api_key_secret = serializers.CharField(max_length=512, required=False, allow_blank=True, write_only=True)
    auth_token = serializers.CharField(max_length=512, required=False, allow_blank=True, write_only=True)
    advanced_opt_out_enabled = serializers.BooleanField(default=False)
    allow_inbound_support = serializers.BooleanField(default=True)
    default_language = serializers.ChoiceField(choices=["ru", "uz", "en"], required=False)
    supported_languages = serializers.ListField(
        child=serializers.ChoiceField(choices=["ru", "uz", "en"]), required=False
    )
    ai_mode = serializers.ChoiceField(choices=["manual", "suggest", "autopilot"], default="manual")
