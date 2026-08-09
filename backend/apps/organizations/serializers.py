from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers

from organizations.models import (
    Branch,
    Organization,
    OrganizationMembership,
    OrganizationProfile,
)
from organizations.services import create_organization

User = get_user_model()


class MembershipSummarySerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    organization_slug = serializers.CharField(source="organization.slug", read_only=True)

    class Meta:
        model = OrganizationMembership
        fields = ["id", "organization", "organization_name", "organization_slug", "role", "status", "joined_at"]


class MeSerializer(serializers.ModelSerializer):
    memberships = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email", "phone", "memberships"]

    def get_memberships(self, obj):
        rows = obj.organization_memberships.filter(status="active").select_related("organization")
        return MembershipSummarySerializer(rows, many=True).data


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = [
            "id", "name", "slug", "status", "industry", "default_language", "timezone",
            "logo", "logo_url", "settings", "created_at", "updated_at", "archived_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "archived_at"]

    def create(self, validated_data):
        return create_organization(creator=self.context["request"].user, **validated_data)

    def update(self, instance, validated_data):
        if validated_data.get('status') == 'archived' and not instance.archived_at:
            validated_data['archived_at'] = timezone.now()
        elif 'status' in validated_data and validated_data['status'] != 'archived':
            validated_data['archived_at'] = None
        return super().update(instance, validated_data)


class OrganizationProfileSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)

    class Meta:
        model = OrganizationProfile
        fields = [
            "organization", "public_business_name", "short_description", "target_customers",
            "products_services_summary", "business_rules", "preferred_communication_tone",
            "supported_languages", "response_guidelines", "escalation_instructions",
            "public_contact_information", "onboarding_completion_percentage", "status", "version",
            "created_at", "updated_at", "published_at",
        ]
        read_only_fields = ["organization", "version", "created_at", "updated_at", "published_at"]

    def update(self, instance, validated_data):
        instance.version += 1
        if validated_data.get('status') == 'published' and not instance.published_at:
            validated_data['published_at'] = timezone.now()
        return super().update(instance, validated_data)


class BranchSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)

    class Meta:
        model = Branch
        fields = ["id", "organization", "name", "address", "phone", "email", "timezone", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]


class MembershipSerializer(serializers.ModelSerializer):
    user = serializers.UUIDField(source="user_id", read_only=True)
    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = OrganizationMembership
        fields = ["id", "organization", "user", "user_email", "user_name", "role", "status", "created_at", "updated_at", "joined_at"]
        read_only_fields = ["id", "organization", "user", "created_at", "updated_at", "joined_at"]

    def get_user_name(self, obj):
        return obj.user.get_full_name() or obj.user.username
