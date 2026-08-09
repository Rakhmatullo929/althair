from rest_framework import serializers

from channels.models import ChannelConnection


class ChannelConnectionSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)
    credentials = serializers.JSONField(write_only=True, required=False)
    webhook_secret = serializers.CharField(write_only=True, required=False, min_length=16, max_length=512)
    has_credentials = serializers.SerializerMethodField()
    has_webhook_secret = serializers.SerializerMethodField()

    class Meta:
        model = ChannelConnection
        fields = [
            "id", "organization", "branch", "type", "provider", "display_name",
            "external_identifier", "status", "configuration", "credentials",
            "webhook_secret", "has_credentials", "has_webhook_secret", "last_error_code",
            "last_error_message", "last_synced_at", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "organization", "has_credentials", "has_webhook_secret",
            "last_error_code", "last_error_message", "last_synced_at", "created_at", "updated_at",
        ]

    def get_has_credentials(self, obj):
        return bool(obj.encrypted_credentials)

    def get_has_webhook_secret(self, obj):
        return bool(obj.webhook_secret_hash)

    def validate_branch(self, branch):
        organization = self.context["request"].organization
        if branch and branch.organization_id != organization.id:
            raise serializers.ValidationError("Branch belongs to another organization.")
        return branch

    def create(self, validated_data):
        credentials = validated_data.pop("credentials", None)
        webhook_secret = validated_data.pop("webhook_secret", None)
        instance = ChannelConnection(organization=self.context["request"].organization, **validated_data)
        if credentials is not None:
            instance.set_credentials(credentials)
        if webhook_secret:
            instance.set_webhook_secret(webhook_secret)
        instance.full_clean()
        instance.save()
        return instance

    def update(self, instance, validated_data):
        credentials = validated_data.pop("credentials", None)
        webhook_secret = validated_data.pop("webhook_secret", None)
        for key, value in validated_data.items():
            setattr(instance, key, value)
        if credentials is not None:
            instance.set_credentials(credentials)
        if webhook_secret:
            instance.set_webhook_secret(webhook_secret)
        instance.full_clean()
        instance.save()
        return instance
