from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import NotFound

from booking.models import (
    Appointment,
    AppointmentEvent,
    AppointmentHold,
    AppointmentReminder,
    BookableResource,
    BookableStaffProfile,
    BookingPolicy,
    PublicBookingProfile,
    ScheduleBreak,
    ScheduleException,
    Service,
    ServiceCategory,
    ServiceResourceRequirement,
    StaffBranchAssignment,
    StaffService,
    WaitlistEntry,
    WeeklyScheduleRule,
)


class TenantModelSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        attrs = super().validate(attrs)
        organization = self.context["request"].organization
        for value in attrs.values():
            related_organization_id = getattr(value, "organization_id", organization.id)
            if related_organization_id != organization.id:
                raise NotFound("The requested booking resource was not found.")
        return attrs

    def create(self, validated_data):
        validated_data["organization"] = self.context["request"].organization
        instance = self.Meta.model(**validated_data)
        try:
            instance.full_clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                getattr(exc, "message_dict", {"detail": exc.messages})
            ) from exc
        instance.save()
        return instance

    def update(self, instance, validated_data):
        for key, value in validated_data.items():
            setattr(instance, key, value)
        try:
            instance.full_clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                getattr(exc, "message_dict", {"detail": exc.messages})
            ) from exc
        instance.save()
        return instance


class ServiceCategorySerializer(TenantModelSerializer):
    class Meta:
        model = ServiceCategory
        fields = ("id", "name", "description", "position", "active", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")


class ServiceSerializer(TenantModelSerializer):
    class Meta:
        model = Service
        fields = (
            "id", "category", "name", "public_description", "internal_description",
            "duration_minutes", "buffer_before_minutes", "buffer_after_minutes", "price_minor",
            "currency", "booking_mode", "customer_can_choose_staff", "minimum_notice_minutes",
            "maximum_advance_days", "cancellation_notice_minutes", "active", "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class StaffBranchAssignmentSerializer(TenantModelSerializer):
    class Meta:
        model = StaffBranchAssignment
        fields = ("id", "staff_profile", "branch", "created_at")
        read_only_fields = ("id", "created_at")


class StaffServiceSerializer(TenantModelSerializer):
    class Meta:
        model = StaffService
        fields = (
            "id", "staff_profile", "service", "duration_override_minutes", "price_override_minor", "active"
        )
        read_only_fields = ("id",)


class StaffSerializer(TenantModelSerializer):
    membership_name = serializers.CharField(source="membership.user.email", read_only=True)

    class Meta:
        model = BookableStaffProfile
        fields = (
            "id", "membership", "membership_name", "display_name", "timezone_override", "active",
            "accepts_online_booking", "maximum_concurrent_appointments", "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class ResourceSerializer(TenantModelSerializer):
    class Meta:
        model = BookableResource
        fields = ("id", "branch", "name", "resource_type", "capacity", "active", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")


class ServiceResourceRequirementSerializer(TenantModelSerializer):
    class Meta:
        model = ServiceResourceRequirement
        fields = ("id", "service", "resource_type", "specific_resource", "quantity", "required")
        read_only_fields = ("id",)


class WeeklyScheduleRuleSerializer(TenantModelSerializer):
    class Meta:
        model = WeeklyScheduleRule
        fields = (
            "id", "owner_type", "owner_id", "weekday", "start_local_time", "end_local_time",
            "effective_from", "effective_to", "active",
        )
        read_only_fields = ("id",)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        from booking.services import BookingError, ScheduleService

        try:
            ScheduleService.validate_owner(
                organization=self.context["request"].organization,
                owner_type=attrs.get("owner_type", getattr(self.instance, "owner_type", None)),
                owner_id=attrs.get("owner_id", getattr(self.instance, "owner_id", None)),
            )
        except BookingError as exc:
            raise serializers.ValidationError({"owner_id": exc.message}) from exc
        return attrs


class ScheduleBreakSerializer(TenantModelSerializer):
    class Meta:
        model = ScheduleBreak
        fields = ("id", "owner_type", "owner_id", "weekday", "date", "start_local_time", "end_local_time", "reason")
        read_only_fields = ("id",)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        from booking.services import BookingError, ScheduleService

        try:
            ScheduleService.validate_owner(
                organization=self.context["request"].organization,
                owner_type=attrs.get("owner_type", getattr(self.instance, "owner_type", None)),
                owner_id=attrs.get("owner_id", getattr(self.instance, "owner_id", None)),
            )
        except BookingError as exc:
            raise serializers.ValidationError({"owner_id": exc.message}) from exc
        return attrs


class ScheduleExceptionSerializer(TenantModelSerializer):
    class Meta:
        model = ScheduleException
        fields = ("id", "owner_type", "owner_id", "starts_at", "ends_at", "exception_type", "reason", "created_by", "created_at")
        read_only_fields = ("id", "created_by", "created_at")

    def create(self, validated_data):
        validated_data["created_by"] = self.context["request"].organization_membership
        return super().create(validated_data)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        from booking.services import BookingError, ScheduleService

        try:
            ScheduleService.validate_owner(
                organization=self.context["request"].organization,
                owner_type=attrs.get("owner_type", getattr(self.instance, "owner_type", None)),
                owner_id=attrs.get("owner_id", getattr(self.instance, "owner_id", None)),
            )
        except BookingError as exc:
            raise serializers.ValidationError({"owner_id": exc.message}) from exc
        return attrs


class BookingPolicySerializer(TenantModelSerializer):
    class Meta:
        model = BookingPolicy
        exclude = ("organization",)
        read_only_fields = ("id", "created_at", "updated_at")


class PublicBookingProfileSerializer(TenantModelSerializer):
    class Meta:
        model = PublicBookingProfile
        fields = (
            "id", "public_key", "enabled", "title", "intro_text", "privacy_url", "terms_url",
            "allowed_origins", "created_at", "updated_at",
        )
        read_only_fields = ("id", "public_key", "created_at", "updated_at")


class AppointmentEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AppointmentEvent
        fields = ("id", "event_type", "actor_type", "summary", "metadata", "created_at")


class AppointmentSerializer(serializers.ModelSerializer):
    service_name = serializers.CharField(source="service.name", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    staff_name = serializers.CharField(source="staff_profile.display_name", read_only=True)
    contact_name = serializers.CharField(source="contact.display_name", read_only=True)
    events = AppointmentEventSerializer(many=True, read_only=True)
    resource_ids = serializers.PrimaryKeyRelatedField(source="resources", many=True, read_only=True)

    class Meta:
        model = Appointment
        fields = (
            "id", "public_reference", "branch", "branch_name", "service", "service_name",
            "service_name_snapshot", "duration_snapshot_minutes", "price_snapshot_minor", "currency_snapshot",
            "contact", "contact_name", "primary_identity", "staff_profile", "staff_name", "resource_ids",
            "source_channel_type", "starts_at", "ends_at", "customer_timezone", "status",
            "confirmation_status", "cancellation_reason", "internal_notes", "customer_notes",
            "created_by_type", "confirmed_at", "cancelled_at", "completed_at", "created_at", "updated_at", "events",
        )
        read_only_fields = fields


class AppointmentHoldSerializer(serializers.ModelSerializer):
    resources = serializers.SerializerMethodField()

    class Meta:
        model = AppointmentHold
        fields = (
            "id", "branch", "service", "staff_profile", "contact", "starts_at", "ends_at",
            "expires_at", "status", "resources", "created_at",
        )

    def get_resources(self, obj):
        return [
            {"resource_id": str(link.resource_id), "quantity": link.quantity}
            for link in obj.resource_links.all()
        ]


class WaitlistSerializer(TenantModelSerializer):
    class Meta:
        model = WaitlistEntry
        fields = (
            "id", "branch", "service", "contact", "preferred_staff", "earliest_date", "latest_date",
            "preferred_time_windows", "status", "offer_expires_at", "offered_hold", "created_at", "updated_at",
        )
        read_only_fields = ("id", "status", "offer_expires_at", "offered_hold", "created_at", "updated_at")


class ReminderSerializer(serializers.ModelSerializer):
    class Meta:
        model = AppointmentReminder
        fields = (
            "id", "appointment", "reminder_type", "scheduled_for", "preferred_channel", "status",
            "attempt_count", "last_error_code", "created_at", "updated_at",
        )


class AvailabilityQuerySerializer(serializers.Serializer):
    branch_id = serializers.UUIDField()
    service_id = serializers.UUIDField()
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    staff_profile_id = serializers.UUIDField(required=False)


class HoldCreateSerializer(serializers.Serializer):
    branch_id = serializers.UUIDField()
    service_id = serializers.UUIDField()
    contact_id = serializers.UUIDField()
    staff_profile_id = serializers.UUIDField(required=False, allow_null=True)
    starts_at = serializers.DateTimeField()


class AppointmentCreateSerializer(serializers.Serializer):
    hold_id = serializers.UUIDField()
    customer_timezone = serializers.CharField(max_length=64)
    primary_identity_id = serializers.UUIDField(required=False, allow_null=True)
    customer_notes = serializers.CharField(max_length=3000, required=False, allow_blank=True)
    source_conversation_id = serializers.UUIDField(required=False, allow_null=True)


class AppointmentActionSerializer(serializers.Serializer):
    starts_at = serializers.DateTimeField(required=False)
    reason = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=Appointment.Status.choices, required=False)
