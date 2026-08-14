from rest_framework import serializers

from control_plane.models import (
    ControlKind,
    DataRequestType,
    OperationalControl,
    OperationalJob,
    OrganizationEntitlement,
    PlatformDataRequest,
    PlatformIncident,
    PlatformRole,
    PlatformStaffAccess,
)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False, write_only=True)


class MFAVerifySerializer(serializers.Serializer):
    code = serializers.CharField(min_length=6, max_length=32, trim_whitespace=True, write_only=True)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=8, max_length=1000)


class ControlMutationSerializer(ReasonSerializer):
    action = serializers.ChoiceField(choices=["activate", "restore"])
    kind = serializers.ChoiceField(choices=ControlKind.choices, required=False)
    control_id = serializers.UUIDField(required=False)
    organization_id = serializers.UUIDField(required=False)
    provider_type = serializers.CharField(max_length=40, required=False, allow_blank=True)
    channel_connection_id = serializers.UUIDField(required=False)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)


class OperationalControlSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", allow_null=True)
    channel_connection = serializers.UUIDField(source="channel_connection_id", allow_null=True)
    activated_by_role = serializers.CharField(source="activated_by.role")

    class Meta:
        model = OperationalControl
        fields = ["id", "kind", "organization", "provider_type", "channel_connection", "active", "reason",
                  "expires_at", "activated_by_role", "created_at", "restored_at"]


class OperationalJobSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", allow_null=True)
    channel_connection = serializers.UUIDField(source="channel_connection_id", allow_null=True)

    class Meta:
        model = OperationalJob
        fields = ["id", "job_type", "organization", "channel_connection", "status", "attempts", "max_attempts",
                  "next_retry_at", "safe_error_code", "idempotency_reference", "idempotent", "created_at", "updated_at"]


class IncidentSerializer(serializers.ModelSerializer):
    affected_organizations = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    linked_jobs = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    assigned_staff = serializers.UUIDField(source="assigned_staff_id", allow_null=True, read_only=True)

    class Meta:
        model = PlatformIncident
        fields = ["id", "severity", "status", "title", "safe_summary", "affected_provider",
                  "affected_organizations", "linked_jobs", "assigned_staff", "created_at", "updated_at", "resolved_at"]


class IncidentWriteSerializer(serializers.Serializer):
    severity = serializers.ChoiceField(choices=PlatformIncident.Severity.choices)
    status = serializers.ChoiceField(choices=PlatformIncident.Status.choices, required=False)
    title = serializers.CharField(min_length=4, max_length=240)
    safe_summary = serializers.CharField(min_length=8, max_length=4000)
    affected_provider = serializers.CharField(max_length=40, required=False, allow_blank=True)
    organization_ids = serializers.ListField(child=serializers.UUIDField(), required=False)
    job_ids = serializers.ListField(child=serializers.UUIDField(), required=False)
    assigned_staff_id = serializers.UUIDField(required=False, allow_null=True)
    reason = serializers.CharField(min_length=8, max_length=1000)


class DataRequestSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id")
    approvals = serializers.SerializerMethodField()

    class Meta:
        model = PlatformDataRequest
        fields = ["id", "organization", "request_type", "status", "reason", "scope", "approval_required",
                  "approvals", "identity_verified_at", "created_at", "completed_at", "expires_at"]

    def get_approvals(self, obj):
        return obj.approved_by.count()


class DataRequestCreateSerializer(serializers.Serializer):
    organization_id = serializers.UUIDField()
    request_type = serializers.ChoiceField(choices=DataRequestType.choices)
    reason = serializers.CharField(min_length=8, max_length=1000)
    scope = serializers.JSONField()
    idempotency_key = serializers.CharField(min_length=8, max_length=200, write_only=True)


class EntitlementSerializer(serializers.ModelSerializer):
    plan = serializers.SlugRelatedField(slug_field="key", read_only=True)
    organization = serializers.UUIDField(source="organization_id")

    class Meta:
        model = OrganizationEntitlement
        fields = ["organization", "plan", "status", "starts_at", "ends_at", "feature_overrides", "limit_overrides", "updated_at"]


class EntitlementWriteSerializer(serializers.Serializer):
    plan = serializers.SlugField(required=False)
    status = serializers.ChoiceField(choices=OrganizationEntitlement.Status.choices, required=False)
    feature_overrides = serializers.DictField(child=serializers.BooleanField(), required=False)
    limit_overrides = serializers.DictField(child=serializers.IntegerField(min_value=0), required=False)
    reason = serializers.CharField(min_length=8, max_length=1000)


class StaffAccessSerializer(serializers.ModelSerializer):
    user = serializers.UUIDField(source="user_id")
    email = serializers.EmailField(source="user.email")
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = PlatformStaffAccess
        fields = ["id", "user", "email", "display_name", "role", "status", "mfa_required",
                  "last_login_at", "last_privileged_action_at", "created_at", "updated_at"]

    def get_display_name(self, obj):
        return obj.user.get_full_name() or obj.user.username


class StaffCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=PlatformRole.choices)
    reason = serializers.CharField(min_length=8, max_length=1000)


class StaffUpdateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=PlatformRole.choices, required=False)
    status = serializers.ChoiceField(choices=PlatformStaffAccess._meta.get_field("status").choices, required=False)
    mfa_required = serializers.BooleanField(required=False)
    reason = serializers.CharField(min_length=8, max_length=1000)
