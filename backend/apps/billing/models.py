from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from organizations.models import Organization, validate_json_object


def default_feature_value():
    return False


class FeatureDefinition(models.Model):
    class ValueType(models.TextChoices):
        BOOLEAN = "boolean", "Boolean"
        INTEGER = "integer", "Integer"
        DECIMAL = "decimal", "Decimal"
        STRING_SET = "string_set", "String set"

    class EnforcementMode(models.TextChoices):
        HARD = "hard", "Hard"
        SOFT = "soft", "Soft"
        INFORMATIONAL = "informational", "Informational"

    key = models.SlugField(max_length=100, primary_key=True)
    display_name = models.CharField(max_length=160)
    category = models.CharField(max_length=80, db_index=True)
    value_type = models.CharField(max_length=16, choices=ValueType.choices)
    default_value = models.JSONField(default=default_feature_value)
    enforcement_mode = models.CharField(
        max_length=16, choices=EnforcementMode.choices, default=EnforcementMode.HARD
    )
    active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class PlanPrice(models.Model):
    class Interval(models.TextChoices):
        MONTH = "month", "Month"
        YEAR = "year", "Year"
        ONE_TIME = "one_time", "One time"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        RETIRED = "retired", "Retired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey("control_plane.PlanCatalog", on_delete=models.PROTECT, related_name="prices")
    currency = models.CharField(max_length=3)
    billing_interval = models.CharField(max_length=12, choices=Interval.choices)
    amount_minor = models.PositiveBigIntegerField()
    included_usage = models.JSONField(default=dict, blank=True, validators=[validate_json_object])
    overage_rates = models.JSONField(default=dict, blank=True, validators=[validate_json_object])
    effective_from = models.DateTimeField(null=True, blank=True)
    effective_to = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["plan__key", "plan__version", "currency", "billing_interval"]
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "currency", "billing_interval"], name="unique_plan_currency_interval"
            ),
            models.CheckConstraint(condition=Q(amount_minor__gte=0), name="plan_price_nonnegative"),
        ]

    def clean(self):
        super().clean()
        self.currency = str(self.currency or "").upper()
        if len(self.currency) != 3 or not self.currency.isalpha():
            raise ValidationError({"currency": "Use a three-letter ISO 4217 currency code."})
        if not isinstance(self.included_usage, dict) or not isinstance(self.overage_rates, dict):
            raise ValidationError("Usage and overage snapshots must be objects.")
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                "plan_id", "currency", "billing_interval", "amount_minor", "included_usage", "overage_rates", "status"
            ).first()
            if previous and previous["status"] == self.Status.ACTIVE:
                immutable = ("plan_id", "currency", "billing_interval", "amount_minor", "included_usage", "overage_rates")
                if any(previous[field] != getattr(self, field) for field in immutable):
                    raise ValidationError("Active prices are immutable; create a new plan version instead.")
                if self.status == self.Status.DRAFT:
                    raise ValidationError("An active price cannot return to draft.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class BillingAccount(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RESTRICTED = "restricted", "Restricted"
        CLOSED = "closed", "Closed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, related_name="billing_account"
    )
    legal_name = models.CharField(max_length=200)
    tax_id = models.CharField(max_length=80, blank=True)
    billing_email = models.EmailField(blank=True)
    billing_address = models.JSONField(default=dict, blank=True, validators=[validate_json_object])
    country_code = models.CharField(max_length=2, blank=True)
    default_currency = models.CharField(max_length=3, default="UZS")
    timezone = models.CharField(max_length=64, default="Asia/Tashkent")
    provider = models.CharField(max_length=24, default="manual")
    provider_customer_id = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        self.default_currency = str(self.default_currency or "").upper()
        self.country_code = str(self.country_code or "").upper()
        if len(self.default_currency) != 3 or not self.default_currency.isalpha():
            raise ValidationError({"default_currency": "Use a three-letter ISO 4217 currency code."})
        if self.country_code and (len(self.country_code) != 2 or not self.country_code.isalpha()):
            raise ValidationError({"country_code": "Use a two-letter country code."})
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values_list("organization_id", flat=True).first()
            if previous and previous != self.organization_id:
                raise ValidationError({"organization": "Organization is immutable."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class Subscription(models.Model):
    class PaymentSource(models.TextChoices):
        WALLET = "wallet", "Organization wallet"
        MANUAL = "manual", "Manual"
        FAKE = "fake", "Deterministic fake"
        FUTURE_EXTERNAL = "future_external", "Future external provider"

    class Status(models.TextChoices):
        TRIALING = "trialing", "Trialing"
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Past due"
        GRACE = "grace", "Grace"
        PAUSED = "paused", "Paused"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"
        MANUAL = "manual", "Manual"

    EFFECTIVE_STATUSES = (
        Status.TRIALING,
        Status.ACTIVE,
        Status.PAST_DUE,
        Status.GRACE,
        Status.PAUSED,
        Status.MANUAL,
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="subscriptions")
    billing_account = models.ForeignKey(BillingAccount, on_delete=models.PROTECT, related_name="subscriptions")
    provider = models.CharField(max_length=24, default="manual")
    payment_source = models.CharField(
        max_length=24,
        choices=PaymentSource.choices,
        default=PaymentSource.MANUAL,
        db_index=True,
    )
    provider_subscription_id = models.CharField(max_length=160, blank=True)
    plan = models.ForeignKey("control_plane.PlanCatalog", on_delete=models.PROTECT, related_name="subscriptions")
    price = models.ForeignKey(PlanPrice, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=16, choices=Status.choices, db_index=True)
    trial_started_at = models.DateTimeField(null=True, blank=True)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    current_period_start = models.DateTimeField()
    current_period_end = models.DateTimeField()
    cancel_at_period_end = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    grace_ends_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    provider_state = models.JSONField(default=dict, blank=True, validators=[validate_json_object])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization"],
                condition=Q(status__in=["trialing", "active", "past_due", "grace", "paused", "manual"]),
                name="one_effective_subscription_per_org",
            ),
        ]
        indexes = [models.Index(fields=["organization", "status", "current_period_end"])]

    def clean(self):
        super().clean()
        if self.billing_account_id and self.billing_account.organization_id != self.organization_id:
            raise ValidationError("Billing account belongs to another organization.")
        if self.price_id and self.price.plan_id != self.plan_id:
            raise ValidationError("Price belongs to another plan version.")
        if self.price_id and self.billing_account_id and self.price.currency != self.billing_account.default_currency:
            raise ValidationError("Subscription currency must match the billing account currency.")
        if self.current_period_end <= self.current_period_start:
            raise ValidationError("Subscription period end must be after its start.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ScheduledSubscriptionChange(models.Model):
    class ChangeType(models.TextChoices):
        UPGRADE = "upgrade", "Upgrade"
        DOWNGRADE = "downgrade", "Downgrade"
        INTERVAL = "interval_change", "Interval change"
        CANCELLATION = "cancellation", "Cancellation"

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        APPLIED = "applied", "Applied"
        CANCELLED = "cancelled", "Cancelled"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="scheduled_changes")
    target_plan = models.ForeignKey("control_plane.PlanCatalog", on_delete=models.PROTECT, related_name="targeted_changes")
    target_price = models.ForeignKey(PlanPrice, on_delete=models.PROTECT, related_name="targeted_changes")
    effective_at = models.DateTimeField(db_index=True)
    change_type = models.CharField(max_length=20, choices=ChangeType.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SCHEDULED, db_index=True)
    preview_snapshot = models.JSONField(default=dict, validators=[validate_json_object])
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="billing_changes_requested"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["subscription"], condition=Q(status="scheduled"), name="one_scheduled_subscription_change"
            )
        ]


class ImmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Immutable billing records cannot be updated in bulk.")

    def delete(self):
        raise ValidationError("Immutable billing records cannot be deleted.")


class UsageEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="billing_usage_events")
    meter_key = models.SlugField(max_length=100, db_index=True)
    quantity = models.DecimalField(max_digits=24, decimal_places=6)
    unit = models.CharField(max_length=40)
    source_type = models.CharField(max_length=80)
    source_id = models.CharField(max_length=160)
    idempotency_key = models.CharField(max_length=200)
    occurred_at = models.DateTimeField(db_index=True)
    period_start = models.DateTimeField(db_index=True)
    metadata = models.JSONField(default=dict, blank=True, validators=[validate_json_object])
    correction = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = ImmutableQuerySet.as_manager()

    class Meta:
        ordering = ["-occurred_at"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "idempotency_key"], name="unique_billing_usage_event"),
            models.CheckConstraint(condition=Q(quantity__gte=0) | Q(correction=True), name="usage_nonnegative_or_correction"),
        ]
        indexes = [models.Index(fields=["organization", "meter_key", "period_start"])]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Usage events are immutable; append a correction event.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Usage events are immutable.")


class UsageAggregate(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="billing_usage_aggregates")
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="usage_aggregates")
    meter_key = models.SlugField(max_length=100)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    quantity = models.DecimalField(max_digits=24, decimal_places=6, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "subscription", "meter_key", "period_start"],
                name="unique_billing_usage_aggregate",
            )
        ]


class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        OPEN = "open", "Open"
        PAID = "paid", "Paid"
        VOID = "void", "Void"
        UNCOLLECTIBLE = "uncollectible", "Uncollectible"
        REFUNDED = "refunded", "Refunded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="invoices")
    billing_account = models.ForeignKey(BillingAccount, on_delete=models.PROTECT, related_name="invoices")
    subscription = models.ForeignKey(Subscription, on_delete=models.PROTECT, related_name="invoices")
    provider = models.CharField(max_length=24)
    provider_invoice_id = models.CharField(max_length=160, blank=True)
    invoice_number = models.CharField(max_length=64, unique=True)
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    subtotal_minor = models.BigIntegerField(default=0)
    discount_minor = models.BigIntegerField(default=0)
    tax_minor = models.BigIntegerField(default=0)
    total_minor = models.BigIntegerField(default=0)
    amount_paid_minor = models.BigIntegerField(default=0)
    amount_due_minor = models.BigIntegerField(default=0)
    due_at = models.DateTimeField(null=True, blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(tax_minor__gte=0), name="invoice_tax_nonnegative"),
            models.CheckConstraint(condition=Q(amount_paid_minor__gte=0), name="invoice_paid_nonnegative"),
        ]

    def clean(self):
        super().clean()
        if self.billing_account_id and self.billing_account.organization_id != self.organization_id:
            raise ValidationError("Invoice billing account belongs to another organization.")
        if self.subscription_id and self.subscription.organization_id != self.organization_id:
            raise ValidationError("Invoice subscription belongs to another organization.")
        if self.tax_minor != 0:
            raise ValidationError({"tax_minor": "Tax calculation is not implemented in this stage."})
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                "status", "currency", "period_start", "period_end", "subtotal_minor", "discount_minor", "tax_minor", "total_minor"
            ).first()
            if previous and previous["status"] != self.Status.DRAFT:
                immutable = ("currency", "period_start", "period_end", "subtotal_minor", "discount_minor", "tax_minor", "total_minor")
                if any(previous[field] != getattr(self, field) for field in immutable):
                    raise ValidationError("Issued invoice amounts and periods are immutable.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class InvoiceSequence(models.Model):
    year = models.PositiveSmallIntegerField(primary_key=True)
    next_value = models.PositiveBigIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)


class InvoiceLine(models.Model):
    class LineType(models.TextChoices):
        BASE = "base", "Base"
        USAGE = "usage", "Usage"
        ADJUSTMENT = "adjustment", "Adjustment"
        CREDIT = "credit", "Credit"
        TAX_PLACEHOLDER = "tax_placeholder", "Tax placeholder"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    line_type = models.CharField(max_length=20, choices=LineType.choices)
    description = models.CharField(max_length=500)
    feature_or_meter_key = models.CharField(max_length=100, blank=True)
    quantity = models.DecimalField(max_digits=24, decimal_places=6)
    unit_amount_minor = models.BigIntegerField()
    amount_minor = models.BigIntegerField()
    source_period = models.JSONField(default=dict, validators=[validate_json_object])
    pricing_snapshot = models.JSONField(default=dict, validators=[validate_json_object])
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.invoice.status != Invoice.Status.DRAFT:
            raise ValidationError("Issued invoice lines are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.invoice.status != Invoice.Status.DRAFT:
            raise ValidationError("Issued invoice lines are immutable.")
        return super().delete(*args, **kwargs)


class PaymentAttempt(models.Model):
    class Status(models.TextChoices):
        CREATED = "created", "Created"
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payment_attempts")
    provider = models.CharField(max_length=24)
    provider_payment_id = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CREATED, db_index=True)
    amount_minor = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3)
    failure_code = models.CharField(max_length=80, blank=True)
    attempted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class OrganizationWallet(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        FROZEN = "frozen", "Frozen"
        CLOSED = "closed", "Closed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="wallets",
    )
    currency = models.CharField(max_length=3)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    available_balance_minor = models.BigIntegerField(default=0)
    ledger_version = models.PositiveBigIntegerField(default=0)
    last_reconciled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["organization_id", "currency"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "currency"],
                name="unique_organization_wallet_currency",
            ),
            models.CheckConstraint(
                condition=Q(available_balance_minor__gte=0),
                name="wallet_balance_nonnegative",
            ),
        ]

    def clean(self):
        super().clean()
        self.currency = str(self.currency or "").upper()
        if len(self.currency) != 3 or not self.currency.isalpha():
            raise ValidationError({"currency": "Use a three-letter ISO 4217 currency code."})
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                "organization_id", "currency"
            ).first()
            if previous and (
                previous["organization_id"] != self.organization_id
                or previous["currency"] != self.currency
            ):
                raise ValidationError("Wallet organization and currency are immutable.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class WalletTransactionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Wallet ledger entries are immutable.")

    def delete(self):
        raise ValidationError("Wallet ledger entries are immutable.")


class WalletTransaction(models.Model):
    class Direction(models.TextChoices):
        CREDIT = "credit", "Credit"
        DEBIT = "debit", "Debit"

    class TransactionType(models.TextChoices):
        TOP_UP = "top_up", "Top up"
        SUBSCRIPTION_PAYMENT = "subscription_payment", "Subscription payment"
        ADJUSTMENT = "adjustment", "Adjustment"
        REVERSAL = "reversal", "Reversal"
        REFUND = "refund", "Refund"
        MIGRATION_CREDIT = "migration_credit", "Migration credit"

    class Status(models.TextChoices):
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="wallet_transactions",
    )
    wallet = models.ForeignKey(
        OrganizationWallet,
        on_delete=models.PROTECT,
        related_name="transactions",
    )
    direction = models.CharField(max_length=8, choices=Direction.choices)
    transaction_type = models.CharField(max_length=24, choices=TransactionType.choices)
    amount_minor = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.COMPLETED,
        db_index=True,
    )
    idempotency_key = models.CharField(max_length=200)
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="wallet_transactions",
    )
    payment_attempt = models.OneToOneField(
        PaymentAttempt,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="wallet_transaction",
    )
    reverses_transaction = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversal_transaction",
    )
    payment_method = models.CharField(max_length=40, blank=True)
    external_reference = models.CharField(max_length=160, blank=True)
    reason = models.CharField(max_length=500, blank=True)
    safe_metadata = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_json_object],
    )
    performed_by_platform_staff = models.ForeignKey(
        "control_plane.PlatformStaffAccess",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="wallet_transactions_performed",
    )
    balance_after_minor = models.BigIntegerField()
    ledger_version = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    objects = WalletTransactionQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["wallet", "idempotency_key"],
                name="unique_wallet_transaction_idempotency",
            ),
            models.UniqueConstraint(
                fields=["invoice"],
                condition=Q(
                    invoice__isnull=False,
                    transaction_type="subscription_payment",
                    status="completed",
                ),
                name="one_completed_wallet_debit_per_invoice",
            ),
            models.CheckConstraint(
                condition=Q(amount_minor__gt=0),
                name="wallet_transaction_amount_positive",
            ),
            models.CheckConstraint(
                condition=Q(balance_after_minor__gte=0),
                name="wallet_transaction_balance_nonnegative",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "created_at"]),
            models.Index(fields=["wallet", "ledger_version"]),
        ]

    def clean(self):
        super().clean()
        self.currency = str(self.currency or "").upper()
        if self.wallet_id and self.wallet.organization_id != self.organization_id:
            raise ValidationError("Wallet transaction organization does not match its wallet.")
        if self.wallet_id and self.wallet.currency != self.currency:
            raise ValidationError("Wallet transaction currency does not match its wallet.")
        if self.invoice_id and self.invoice.organization_id != self.organization_id:
            raise ValidationError("Wallet transaction invoice belongs to another organization.")
        if self.invoice_id and self.invoice.currency != self.currency:
            raise ValidationError("Wallet transaction invoice currency does not match its wallet.")
        if self.reverses_transaction_id:
            original = self.reverses_transaction
            if original.wallet_id != self.wallet_id or original.organization_id != self.organization_id:
                raise ValidationError("A reversal must remain in the original wallet.")

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Wallet ledger entries are immutable; append a reversal.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Wallet ledger entries are immutable.")


class WalletReconciliationRun(models.Model):
    class Status(models.TextChoices):
        MATCHED = "matched", "Matched"
        MISMATCH = "mismatch", "Mismatch"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(
        OrganizationWallet,
        on_delete=models.PROTECT,
        related_name="reconciliation_runs",
    )
    expected_balance_minor = models.BigIntegerField()
    cached_balance_minor = models.BigIntegerField()
    difference_minor = models.BigIntegerField()
    ledger_entries = models.PositiveBigIntegerField(default=0)
    status = models.CharField(max_length=12, choices=Status.choices, db_index=True)
    safe_report = models.JSONField(default=dict, validators=[validate_json_object])
    performed_by_platform_staff = models.ForeignKey(
        "control_plane.PlatformStaffAccess",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="wallet_reconciliations_performed",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class PlatformCatalogState(models.Model):
    key = models.SlugField(max_length=80, primary_key=True)
    version = models.PositiveIntegerField()
    applied_at = models.DateTimeField(auto_now=True)


class BillingProviderEvent(models.Model):
    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        PROCESSED = "processed", "Processed"
        IGNORED = "ignored", "Ignored"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=24)
    provider_event_id = models.CharField(max_length=160)
    event_type = models.CharField(max_length=100)
    payload_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RECEIVED, db_index=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    safe_error = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "provider_event_id"], name="unique_billing_provider_event")
        ]


class BillingNotification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="billing_notifications")
    event_type = models.CharField(max_length=80)
    locale = models.CharField(max_length=2)
    adapter = models.CharField(max_length=24, default="console")
    delivery_status = models.CharField(max_length=24, default="development_console")
    idempotency_key = models.CharField(max_length=200)
    safe_context = models.JSONField(default=dict, validators=[validate_json_object])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "idempotency_key"], name="unique_billing_notification")
        ]


class BillingIdempotencyRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="billing_idempotency_records")
    operation = models.CharField(max_length=80)
    key_hash = models.CharField(max_length=64)
    result_type = models.CharField(max_length=80, blank=True)
    result_id = models.CharField(max_length=160, blank=True)
    response_safe = models.JSONField(default=dict, validators=[validate_json_object])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "operation", "key_hash"], name="unique_billing_mutation_key"
            )
        ]
