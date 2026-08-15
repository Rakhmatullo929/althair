from __future__ import annotations

from rest_framework import serializers

from billing.models import (
    BillingAccount,
    BillingProviderEvent,
    Invoice,
    InvoiceLine,
    PaymentAttempt,
    PlanPrice,
    ScheduledSubscriptionChange,
    Subscription,
    UsageAggregate,
)
from control_plane.models import PlanCatalog


class BillingAccountSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)
    payment_method_stored = serializers.SerializerMethodField()

    class Meta:
        model = BillingAccount
        fields = [
            "id", "organization", "legal_name", "tax_id", "billing_email", "billing_address",
            "country_code", "default_currency", "timezone", "provider", "status",
            "payment_method_stored", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "organization", "provider", "status", "default_currency", "payment_method_stored",
            "created_at", "updated_at",
        ]

    def get_payment_method_stored(self, obj):
        return False


class PlanPriceSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanPrice
        fields = [
            "id", "currency", "billing_interval", "amount_minor", "included_usage", "overage_rates",
            "effective_from", "effective_to", "status",
        ]


class PlanSerializer(serializers.ModelSerializer):
    prices = PlanPriceSerializer(many=True, read_only=True)

    class Meta:
        model = PlanCatalog
        fields = [
            "id", "key", "version", "display_name", "description", "status", "audience",
            "feature_values", "prices", "created_at", "published_at", "retired_at",
        ]


class ScheduledChangeSerializer(serializers.ModelSerializer):
    target_plan = serializers.SerializerMethodField()
    target_price = serializers.UUIDField(source="target_price_id", read_only=True)

    class Meta:
        model = ScheduledSubscriptionChange
        fields = ["id", "target_plan", "target_price", "effective_at", "change_type", "status", "preview_snapshot", "created_at"]

    def get_target_plan(self, obj):
        return {"id": str(obj.target_plan_id), "key": obj.target_plan.key, "version": obj.target_plan.version}


class SubscriptionSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)
    plan = PlanSerializer(read_only=True)
    price = PlanPriceSerializer(read_only=True)
    scheduled_change = serializers.SerializerMethodField()
    online_payment_connected = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = [
            "id", "organization", "provider", "plan", "price", "status", "trial_started_at", "trial_ends_at",
            "current_period_start", "current_period_end", "cancel_at_period_end", "cancelled_at",
            "grace_ends_at", "ended_at", "scheduled_change", "online_payment_connected", "created_at", "updated_at",
        ]

    def get_scheduled_change(self, obj):
        row = obj.scheduled_changes.filter(status=ScheduledSubscriptionChange.Status.SCHEDULED).select_related(
            "target_plan", "target_price"
        ).first()
        return ScheduledChangeSerializer(row).data if row else None

    def get_online_payment_connected(self, obj):
        return False


class UsageAggregateSerializer(serializers.ModelSerializer):
    remaining = serializers.SerializerMethodField()
    included = serializers.SerializerMethodField()
    overage_estimate_minor = serializers.SerializerMethodField()

    class Meta:
        model = UsageAggregate
        fields = ["meter_key", "period_start", "period_end", "quantity", "included", "remaining", "overage_estimate_minor", "updated_at"]

    def _included(self, obj):
        return obj.subscription.price.included_usage.get(obj.meter_key, 0)

    def get_included(self, obj):
        return self._included(obj)

    def get_remaining(self, obj):
        from decimal import Decimal

        return str(max(Decimal(0), Decimal(str(self._included(obj))) - obj.quantity))

    def get_overage_estimate_minor(self, obj):
        from billing.services import calculate_overage

        rows = calculate_overage(obj.subscription.price, {obj.meter_key: obj.quantity})
        return rows[0]["amount_minor"] if rows else 0


class InvoiceLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLine
        fields = [
            "id", "line_type", "description", "feature_or_meter_key", "quantity", "unit_amount_minor",
            "amount_minor", "source_period", "pricing_snapshot", "created_at",
        ]


class PaymentAttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentAttempt
        fields = [
            "id", "provider", "status", "amount_minor", "currency", "failure_code", "attempted_at",
            "completed_at", "created_at",
        ]


class InvoiceSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id", read_only=True)
    lines = InvoiceLineSerializer(many=True, read_only=True)
    payment_attempts = PaymentAttemptSerializer(many=True, read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id", "organization", "invoice_number", "provider", "currency", "status", "period_start", "period_end",
            "subtotal_minor", "discount_minor", "tax_minor", "total_minor", "amount_paid_minor", "amount_due_minor",
            "due_at", "issued_at", "paid_at", "voided_at", "lines", "payment_attempts", "created_at", "updated_at",
        ]


class BillingProviderEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillingProviderEvent
        fields = [
            "id", "provider", "provider_event_id", "event_type", "status", "received_at", "processed_at", "safe_error",
        ]


class PlanCreateSerializer(serializers.Serializer):
    key = serializers.SlugField(max_length=80)
    display_name = serializers.CharField(max_length=160)
    description = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    audience = serializers.ChoiceField(choices=PlanCatalog.Audience.choices)
    feature_values = serializers.DictField()
    internal_notes = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    currency = serializers.CharField(min_length=3, max_length=3)
    billing_interval = serializers.ChoiceField(choices=PlanPrice.Interval.choices)
    amount_minor = serializers.IntegerField(min_value=0)
    included_usage = serializers.DictField(required=False)
    overage_rates = serializers.DictField(required=False)


class ChangeRequestSerializer(serializers.Serializer):
    price_id = serializers.UUIDField()


class BillingProfileWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillingAccount
        fields = ["legal_name", "tax_id", "billing_email", "billing_address", "country_code", "timezone"]


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=8, max_length=1000)


class GrantSubscriptionSerializer(ReasonSerializer):
    organization_id = serializers.UUIDField()
    price_id = serializers.UUIDField()
    period_days = serializers.IntegerField(min_value=1, max_value=3660, default=30)


class ExtendGraceSerializer(ReasonSerializer):
    days = serializers.IntegerField(min_value=1, max_value=90)
