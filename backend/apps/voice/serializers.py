from rest_framework import serializers

from voice.models import VoiceConnection, VoiceTransferDestination
from voice.services import connection_health, serialize_call_detail


class VoiceTransferDestinationSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)
    voice_connection = serializers.UUIDField(source="voice_connection_id", read_only=True)
    destination = serializers.CharField(write_only=True, required=False, max_length=500)
    has_destination = serializers.SerializerMethodField()

    class Meta:
        model = VoiceTransferDestination
        fields = [
            "id", "organization", "voice_connection", "key", "display_name", "destination_type",
            "destination", "has_destination", "branch", "priority", "active", "business_hours",
            "fallback_behavior", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "organization", "voice_connection", "has_destination", "created_at", "updated_at"]

    def get_has_destination(self, obj):
        return bool(obj.destination_encrypted)


class VoiceConnectionSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)
    channel_connection = serializers.UUIDField(source="channel_connection_id", read_only=True)
    has_carrier_api_key_secret = serializers.SerializerMethodField()
    has_carrier_auth_token = serializers.SerializerMethodField()
    health = serializers.SerializerMethodField()
    transfer_destinations = VoiceTransferDestinationSerializer(many=True, read_only=True)

    class Meta:
        model = VoiceConnection
        fields = [
            "id", "organization", "channel_connection", "carrier", "ownership_mode", "status",
            "phone_number_e164", "phone_number_sid", "sip_trunk_sid", "carrier_account_sid",
            "carrier_api_key_sid", "openai_project_id", "sip_destination", "default_language",
            "supported_languages", "ai_mode", "realtime_model_alias", "voice_name", "reasoning_effort",
            "greeting", "business_hours_behavior", "business_hours", "after_hours_message",
            "disclosure_mode", "transcript_retention_mode", "recording_mode", "max_call_seconds",
            "max_concurrent_calls", "daily_minute_limit", "monthly_minute_limit", "max_tools_per_call",
            "max_transfer_attempts", "last_inbound_at", "last_call_at", "last_health_check_at",
            "last_error_code", "connected_at", "disconnected_at", "created_at", "updated_at",
            "has_carrier_api_key_secret", "has_carrier_auth_token", "health", "transfer_destinations",
        ]
        read_only_fields = fields

    def get_has_carrier_api_key_secret(self, obj):
        return bool(obj.carrier_api_key_secret_encrypted)

    def get_has_carrier_auth_token(self, obj):
        return bool(obj.carrier_auth_token_encrypted)

    def get_health(self, obj):
        return connection_health(obj)


class VoiceConnectionCreateSerializer(serializers.Serializer):
    display_name = serializers.CharField(max_length=160, required=False)
    branch = serializers.UUIDField(required=False, allow_null=True)
    carrier = serializers.ChoiceField(choices=["fake", "twilio_sip"], default="fake")
    ownership_mode = serializers.ChoiceField(choices=["platform_managed", "customer_owned"], default="platform_managed")
    phone_number_e164 = serializers.CharField(max_length=32, default="+15550107777")
    phone_number_sid = serializers.CharField(max_length=64, required=False, allow_blank=True)
    sip_trunk_sid = serializers.CharField(max_length=64, required=False, allow_blank=True)
    carrier_account_sid = serializers.CharField(max_length=64, required=False, allow_blank=True, write_only=True)
    carrier_api_key_sid = serializers.CharField(max_length=64, required=False, allow_blank=True, write_only=True)
    carrier_api_key_secret = serializers.CharField(max_length=512, required=False, allow_blank=True, write_only=True)
    carrier_auth_token = serializers.CharField(max_length=512, required=False, allow_blank=True, write_only=True)
    openai_project_id = serializers.CharField(max_length=120, required=False, allow_blank=True)
    sip_destination = serializers.CharField(max_length=255, required=False, allow_blank=True)
    default_language = serializers.ChoiceField(choices=["ru", "uz", "en"], required=False)
    supported_languages = serializers.ListField(child=serializers.ChoiceField(choices=["ru", "uz", "en"]), required=False)
    ai_mode = serializers.ChoiceField(choices=["manual", "suggest", "autopilot"], default="autopilot")
    realtime_model_alias = serializers.CharField(max_length=120, required=False, allow_blank=True)
    voice_name = serializers.CharField(max_length=80, required=False)
    reasoning_effort = serializers.ChoiceField(choices=["low", "medium", "high"], default="low")
    greeting = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    business_hours_behavior = serializers.ChoiceField(choices=["accept", "callback", "reject"], default="callback")
    business_hours = serializers.JSONField(required=False)
    after_hours_message = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    disclosure_mode = serializers.ChoiceField(
        choices=["ai_disclosure", "ai_and_transcript_disclosure", "explicit_transcript_consent"],
        default="ai_and_transcript_disclosure",
    )
    transcript_retention_mode = serializers.ChoiceField(
        choices=["disabled", "30_days", "90_days", "indefinite"], default="30_days"
    )
    max_call_seconds = serializers.IntegerField(min_value=30, max_value=3600, required=False)
    max_concurrent_calls = serializers.IntegerField(min_value=1, max_value=100, required=False)
    daily_minute_limit = serializers.IntegerField(min_value=1, required=False)
    monthly_minute_limit = serializers.IntegerField(min_value=1, required=False)


class VoiceCallSerializer(serializers.Serializer):
    def to_representation(self, instance):
        return serialize_call_detail(instance)
