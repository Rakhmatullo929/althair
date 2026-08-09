from rest_framework import serializers

from assistant_context.models import AssistantContextRevision, OrganizationAssistantProfile


CONTEXT_FIELDS = [
    "assistant_name",
    "business_summary",
    "business_description",
    "target_customers",
    "products_services",
    "service_area",
    "supported_languages",
    "default_language",
    "tone_of_voice",
    "introduction",
    "escalation_instructions",
    "prohibited_topics",
    "prohibited_actions",
    "fallback_response",
    "additional_instructions",
]


class AssistantProfileSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)
    updated_by_name = serializers.SerializerMethodField()

    class Meta:
        model = OrganizationAssistantProfile
        fields = [
            "id", "organization", *CONTEXT_FIELDS, "status", "version",
            "published_snapshot", "published_at", "created_at", "updated_at",
            "updated_by_name",
        ]
        read_only_fields = [
            "id", "organization", "status", "version", "published_snapshot",
            "published_at", "created_at", "updated_at", "updated_by_name",
        ]

    def get_updated_by_name(self, obj):
        if not obj.updated_by:
            return None
        return obj.updated_by.get_full_name() or obj.updated_by.email or obj.updated_by.username

    def update(self, instance, validated_data):
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.status = "draft"
        instance.updated_by = self.context["request"].user
        instance.full_clean()
        instance.save()
        return instance


class AssistantRevisionSerializer(serializers.ModelSerializer):
    published_by_name = serializers.SerializerMethodField()

    class Meta:
        model = AssistantContextRevision
        fields = ["id", "version", "snapshot", "published_by_name", "published_at"]

    def get_published_by_name(self, obj):
        return obj.published_by.get_full_name() or obj.published_by.email or obj.published_by.username
