from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone

from billing.models import (
    BillingAccount,
    BillingIdempotencyRecord,
    BillingNotification,
    BillingProviderEvent,
    FeatureDefinition,
    Invoice,
    InvoiceLine,
    InvoiceSequence,
    PaymentAttempt,
    PlanPrice,
    ScheduledSubscriptionChange,
    Subscription,
    UsageAggregate,
    UsageEvent,
)
from billing.providers import BillingProviderError, VerifiedBillingEvent, get_billing_provider
from control_plane.models import OrganizationEntitlement, PlanCatalog
from organizations.models import Organization, OrganizationStatus


class BillingError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 409, details: dict | None = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


FEATURE_CATALOG = {
    "crm": ("CRM", "product", "boolean", True, "hard"),
    "crm_read": ("CRM read access", "safety", "boolean", True, "informational"),
    "billing_access": ("Billing access", "safety", "boolean", True, "informational"),
    "data_export": ("Data export", "safety", "boolean", True, "informational"),
    "web_chat": ("Public Web Chat", "channel", "boolean", True, "hard"),
    "instagram": ("Instagram", "channel", "boolean", True, "hard"),
    "telegram": ("Telegram", "channel", "boolean", True, "hard"),
    "gmail": ("Gmail", "channel", "boolean", True, "hard"),
    "sms": ("SMS", "channel", "boolean", True, "hard"),
    "voice": ("Voice", "channel", "boolean", True, "hard"),
    "ai_runtime": ("AI Runtime", "ai", "boolean", True, "hard"),
    "ai_autopilot": ("AI autopilot", "ai", "boolean", False, "hard"),
    "api_access": ("API access", "product", "boolean", False, "hard"),
    "custom_tools": ("Custom AI tools", "ai", "boolean", False, "hard"),
    "max_members": ("Members", "limit", "integer", 5, "hard"),
    "max_branches": ("Branches", "limit", "integer", 2, "hard"),
    "max_channel_connections": ("Channel connections", "limit", "integer", 4, "hard"),
    "max_web_chat_installations": ("Web Chat installations", "limit", "integer", 1, "hard"),
    "max_instagram_connections": ("Instagram connections", "limit", "integer", 1, "hard"),
    "max_telegram_bots": ("Telegram bots", "limit", "integer", 1, "hard"),
    "max_gmail_connections": ("Gmail connections", "limit", "integer", 1, "hard"),
    "max_sms_connections": ("SMS connections", "limit", "integer", 1, "hard"),
    "max_voice_connections": ("Voice connections", "limit", "integer", 1, "hard"),
    "monthly_ai_input_tokens": ("AI input tokens", "usage", "integer", 100000, "soft"),
    "monthly_ai_output_tokens": ("AI output tokens", "usage", "integer", 25000, "soft"),
    "monthly_ai_runs": ("AI runs", "usage", "integer", 1000, "hard"),
    "monthly_sms_segments": ("SMS segments", "usage", "integer", 1000, "soft"),
    "monthly_voice_minutes": ("Voice minutes", "usage", "decimal", "120", "soft"),
    "monthly_external_messages": ("External messages", "usage", "integer", 5000, "soft"),
    "retention_days": ("Retention days", "product", "integer", 90, "informational"),
}

MANUAL_FEATURE_VALUES = {
    key: default for key, (_, _, _, default, _) in FEATURE_CATALOG.items()
}
MANUAL_FEATURE_VALUES.update(
    {
        "ai_autopilot": True,
        "api_access": True,
        "custom_tools": True,
        "max_members": 25,
        "max_branches": 25,
        "max_channel_connections": 25,
        "max_web_chat_installations": 25,
        "max_instagram_connections": 25,
        "max_telegram_bots": 25,
        "max_gmail_connections": 25,
        "max_sms_connections": 25,
        "max_voice_connections": 25,
        "monthly_ai_input_tokens": 1000000,
        "monthly_ai_output_tokens": 250000,
        "monthly_ai_runs": 10000,
        "monthly_sms_segments": 10000,
        "monthly_voice_minutes": "1000",
        "monthly_external_messages": 50000,
    }
)

STARTER_FEATURE_VALUES = dict(MANUAL_FEATURE_VALUES)
STARTER_FEATURE_VALUES.update(
    {
        "ai_autopilot": False,
        "api_access": False,
        "custom_tools": False,
        "max_members": 5,
        "max_branches": 2,
        "max_channel_connections": 4,
        "max_web_chat_installations": 1,
        "max_instagram_connections": 1,
        "max_telegram_bots": 1,
        "max_gmail_connections": 1,
        "max_sms_connections": 1,
        "max_voice_connections": 1,
        "monthly_ai_input_tokens": 100000,
        "monthly_ai_output_tokens": 25000,
        "monthly_ai_runs": 1000,
        "monthly_sms_segments": 1000,
        "monthly_voice_minutes": "120",
        "monthly_external_messages": 5000,
    }
)

METER_LIMITS = {
    "ai_input_tokens": "monthly_ai_input_tokens",
    "ai_output_tokens": "monthly_ai_output_tokens",
    "ai_runs": "monthly_ai_runs",
    "sms_segments": "monthly_sms_segments",
    "voice_seconds": "monthly_voice_minutes",
    "external_messages": "monthly_external_messages",
}

SAFE_READ_FEATURES = {"crm_read", "billing_access", "data_export"}
BLOCKED_BILLING_STATUSES = {Subscription.Status.PAUSED, Subscription.Status.CANCELLED, Subscription.Status.EXPIRED}
SENSITIVE_METADATA_TERMS = {
    "body", "message", "prompt", "transcript", "audio", "email", "address", "phone", "token", "secret", "payload"
}


def _safe_metadata(metadata: dict | None) -> dict:
    result = {}
    for key, value in (metadata or {}).items():
        lowered = str(key).lower()
        if any(term in lowered for term in SENSITIVE_METADATA_TERMS):
            continue
        if value is None or isinstance(value, (bool, int, float)):
            result[str(key)[:80]] = value
        else:
            result[str(key)[:80]] = str(value)[:200]
    return result


def _period_end(start, interval: str):
    if interval == PlanPrice.Interval.YEAR:
        try:
            return start.replace(year=start.year + 1)
        except ValueError:
            return start.replace(year=start.year + 1, day=28)
    if interval == PlanPrice.Interval.ONE_TIME:
        return start + timedelta(days=3650)
    if start.month == 12:
        return start.replace(year=start.year + 1, month=1)
    return start.replace(month=start.month + 1)


def _included_usage(feature_values: dict) -> dict:
    included = {
        meter: feature_values.get(limit_key, 0) for meter, limit_key in METER_LIMITS.items()
    }
    included["voice_seconds"] = str(Decimal(str(included["voice_seconds"])) * Decimal(60))
    return included


@transaction.atomic
def ensure_catalog() -> tuple[PlanCatalog, PlanCatalog]:
    existing_feature_keys = set(
        FeatureDefinition.objects.filter(key__in=FEATURE_CATALOG).values_list("key", flat=True)
    )
    for key, (label, category, value_type, default, enforcement) in FEATURE_CATALOG.items():
        if key in existing_feature_keys:
            continue
        FeatureDefinition.objects.get_or_create(
            key=key,
            defaults={
                "display_name": label,
                "category": category,
                "value_type": value_type,
                "default_value": default,
                "enforcement_mode": enforcement,
                "active": True,
            },
        )
    manual, _ = PlanCatalog.objects.get_or_create(
        key="manual",
        version=1,
        defaults={
            "display_name": "Manual pilot",
            "description": "Reviewed manual contract with no online payment method.",
            "status": PlanCatalog.Status.ACTIVE,
            "audience": PlanCatalog.Audience.INTERNAL,
            "feature_values": MANUAL_FEATURE_VALUES,
            "internal_notes": "Canonical migrated entitlement plan for pilots and existing organizations.",
            "published_at": timezone.now(),
        },
    )
    starter, _ = PlanCatalog.objects.get_or_create(
        key="starter",
        version=1,
        defaults={
            "display_name": "Starter",
            "description": "Provider-independent starter subscription.",
            "status": PlanCatalog.Status.ACTIVE,
            "audience": PlanCatalog.Audience.SELF_SERVE,
            "feature_values": STARTER_FEATURE_VALUES,
            "published_at": timezone.now(),
        },
    )
    for plan, amount, interval in (
        (manual, 0, PlanPrice.Interval.MONTH),
        (starter, 9900000, PlanPrice.Interval.MONTH),
    ):
        PlanPrice.objects.get_or_create(
            plan=plan,
            currency=settings.BILLING_DEFAULT_CURRENCY,
            billing_interval=interval,
            defaults={
                "amount_minor": amount,
                "included_usage": _included_usage(plan.feature_values),
                "overage_rates": {},
                "effective_from": timezone.now(),
                "status": PlanPrice.Status.ACTIVE,
            },
        )
    return manual, starter


def _active_price(plan: PlanCatalog, currency: str) -> PlanPrice:
    price = PlanPrice.objects.filter(
        plan=plan, currency=currency, status=PlanPrice.Status.ACTIVE
    ).order_by("billing_interval").first()
    if not price:
        raise BillingError("price_unavailable", "No active price exists for this plan and currency.", status_code=422)
    return price


def _entitlement_status(subscription: Subscription) -> str:
    return {
        Subscription.Status.TRIALING: OrganizationEntitlement.Status.TRIAL,
        Subscription.Status.ACTIVE: OrganizationEntitlement.Status.ACTIVE,
        Subscription.Status.PAST_DUE: OrganizationEntitlement.Status.GRACE,
        Subscription.Status.GRACE: OrganizationEntitlement.Status.GRACE,
        Subscription.Status.MANUAL: OrganizationEntitlement.Status.MANUAL,
        Subscription.Status.PAUSED: OrganizationEntitlement.Status.SUSPENDED,
        Subscription.Status.CANCELLED: OrganizationEntitlement.Status.SUSPENDED,
        Subscription.Status.EXPIRED: OrganizationEntitlement.Status.SUSPENDED,
    }[subscription.status]


@transaction.atomic
def sync_entitlement(subscription: Subscription) -> OrganizationEntitlement:
    entitlement, _ = OrganizationEntitlement.objects.select_for_update().get_or_create(
        organization=subscription.organization,
        defaults={"plan": subscription.plan, "status": _entitlement_status(subscription)},
    )
    expected = {
        "plan": subscription.plan,
        "status": _entitlement_status(subscription),
        "starts_at": subscription.current_period_start,
        "ends_at": subscription.current_period_end,
    }
    changed = [field for field, value in expected.items() if getattr(entitlement, field) != value]
    if changed:
        for field, value in expected.items():
            setattr(entitlement, field, value)
        entitlement.save(update_fields=[*changed, "updated_at"])
    return entitlement


@transaction.atomic
def ensure_billing_for_organization(organization: Organization) -> tuple[BillingAccount, Subscription, OrganizationEntitlement]:
    manual, starter = ensure_catalog()
    account, _ = BillingAccount.objects.get_or_create(
        organization=organization,
        defaults={
            "legal_name": organization.name,
            "default_currency": settings.BILLING_DEFAULT_CURRENCY,
            "timezone": organization.timezone,
            "provider": settings.BILLING_PROVIDER if settings.BILLING_ENABLE else "manual",
        },
    )
    existing = OrganizationEntitlement.objects.select_related("plan").filter(organization=organization).first()
    plan = existing.plan if existing else (
        PlanCatalog.objects.filter(
            key=settings.BILLING_DEFAULT_PLAN_KEY, status=PlanCatalog.Status.ACTIVE
        ).order_by("-version").first()
        if settings.BILLING_ENABLE and settings.BILLING_DEFAULT_PLAN_KEY
        else None
    )
    plan = plan or (starter if settings.BILLING_ENABLE else manual)
    price = _active_price(plan, account.default_currency)
    subscription = Subscription.objects.filter(
        organization=organization, status__in=Subscription.EFFECTIVE_STATUSES
    ).first()
    if not subscription:
        now = timezone.now()
        trial_days = settings.BILLING_TRIAL_DAYS if settings.BILLING_ENABLE else 0
        status = Subscription.Status.TRIALING if trial_days > 0 else Subscription.Status.MANUAL
        period_end = now + timedelta(days=trial_days) if trial_days else _period_end(now, price.billing_interval)
        subscription = Subscription.objects.create(
            organization=organization,
            billing_account=account,
            provider=account.provider,
            plan=plan,
            price=price,
            status=status,
            trial_started_at=now if trial_days else None,
            trial_ends_at=period_end if trial_days else None,
            current_period_start=now,
            current_period_end=period_end,
            provider_state={"mode": account.provider, "online_checkout": False},
        )
    return account, subscription, sync_entitlement(subscription)


def ensure_default_entitlement(organization: Organization) -> OrganizationEntitlement:
    return ensure_billing_for_organization(organization)[2]


@dataclass(frozen=True)
class EntitlementSnapshot:
    feature: str
    allowed: bool
    value: object
    source: str
    effective_period: dict
    usage: str | None
    remaining: str | None
    enforcement_reason: str
    enforcement_mode: str

    def as_dict(self):
        return asdict(self)


class EntitlementService:
    def __init__(self, organization: Organization):
        self.organization = organization
        _, self.subscription, self.entitlement = ensure_billing_for_organization(organization)

    def resolve(self, feature: str) -> EntitlementSnapshot:
        definition = FeatureDefinition.objects.filter(key=feature, active=True).first()
        period = {
            "start": self.subscription.current_period_start,
            "end": self.subscription.current_period_end,
        }
        if not definition:
            return EntitlementSnapshot(feature, False, False, "unknown", period, None, None, "unknown_feature", "hard")
        plan_values = self.subscription.plan.feature_values or {}
        value = plan_values.get(feature, definition.default_value)
        source = f"plan:{self.subscription.plan.key}:v{self.subscription.plan.version}"
        override_active = not self.entitlement.override_expires_at or self.entitlement.override_expires_at > timezone.now()
        if override_active and feature in self.entitlement.feature_overrides:
            value = self.entitlement.feature_overrides[feature]
            source = "control_plane_override"
        if override_active and feature in self.entitlement.limit_overrides:
            value = self.entitlement.limit_overrides[feature]
            source = "control_plane_override"
        allowed = bool(value) if definition.value_type == FeatureDefinition.ValueType.BOOLEAN else Decimal(str(value)) > 0
        reason = "allowed" if allowed else "plan_disabled"
        if self.organization.status == OrganizationStatus.SUSPENDED and feature not in SAFE_READ_FEATURES:
            allowed, reason = False, "organization_suspended"
        if self.subscription.status in BLOCKED_BILLING_STATUSES and feature not in SAFE_READ_FEATURES:
            allowed, reason = False, "billing_restricted"
        if self.subscription.status == Subscription.Status.GRACE and self.subscription.grace_ends_at:
            if self.subscription.grace_ends_at <= timezone.now() and feature not in SAFE_READ_FEATURES:
                allowed, reason = False, "grace_expired"
        try:
            from control_plane.policies import blocking_control

            provider_type = feature if feature in {"web_chat", "instagram", "telegram", "gmail", "sms", "voice"} else ""
            control = blocking_control(
                organization=self.organization,
                provider_type=provider_type,
                ai=feature in {"ai_runtime", "ai_autopilot"},
                voice=feature == "voice",
                autopilot=feature == "ai_autopilot",
            )
            if control and feature not in SAFE_READ_FEATURES:
                allowed, reason = False, control
        except Exception:
            raise
        meter_key = next((meter for meter, limit in METER_LIMITS.items() if limit == feature), None)
        usage = remaining = None
        if meter_key:
            aggregate = UsageAggregate.objects.filter(
                organization=self.organization,
                subscription=self.subscription,
                meter_key=meter_key,
                period_start=self.subscription.current_period_start,
            ).first()
            used = Decimal(str(aggregate.quantity if aggregate else 0))
            limit = Decimal(str(value))
            if meter_key == "voice_seconds":
                limit *= Decimal(60)
            usage = str(used.normalize())
            remaining = str(max(Decimal(0), limit - used).normalize())
            if used >= limit and definition.enforcement_mode == FeatureDefinition.EnforcementMode.HARD:
                allowed, reason = False, "usage_limit_reached"
        return EntitlementSnapshot(
            feature, allowed, value, source, period, usage, remaining, reason, definition.enforcement_mode
        )

    def all(self) -> list[dict]:
        return [self.resolve(key).as_dict() for key in FeatureDefinition.objects.filter(active=True).values_list("key", flat=True)]

    def require(self, feature: str) -> EntitlementSnapshot:
        snapshot = self.resolve(feature)
        if not snapshot.allowed:
            code = "billing_limit_reached" if snapshot.enforcement_reason == "usage_limit_reached" else "feature_not_entitled"
            raise BillingError(code, "This capability is not available for the current subscription.", status_code=429, details=snapshot.as_dict())
        return snapshot

    def require_capacity(self, feature: str, current: int) -> EntitlementSnapshot:
        snapshot = self.require(feature)
        limit = int(Decimal(str(snapshot.value)))
        if current >= limit:
            raise BillingError(
                "billing_limit_reached",
                "The subscription limit has been reached.",
                status_code=429,
                details={**snapshot.as_dict(), "current": current, "limit": limit},
            )
        return snapshot


def feature_allowed(organization: Organization, feature: str) -> bool:
    return EntitlementService(organization).resolve(feature).allowed


def _active_subscription(organization: Organization) -> Subscription:
    subscription = Subscription.objects.filter(
        organization=organization, status__in=Subscription.EFFECTIVE_STATUSES
    ).select_related("plan", "price", "billing_account").first()
    if not subscription:
        subscription = ensure_billing_for_organization(organization)[1]
    return subscription


@transaction.atomic
def record_usage(
    *, organization: Organization, meter_key: str, quantity, unit: str, source_type: str,
    source_id: str, idempotency_key: str, occurred_at=None, metadata=None, correction=False,
) -> tuple[UsageEvent, bool]:
    amount = Decimal(str(quantity))
    if amount < 0 and not correction:
        raise BillingError("negative_usage", "Negative usage requires a controlled correction event.")
    subscription = _active_subscription(organization)
    occurred_at = occurred_at or timezone.now()
    event, created = UsageEvent.objects.get_or_create(
        organization=organization,
        idempotency_key=str(idempotency_key)[:200],
        defaults={
            "meter_key": meter_key,
            "quantity": amount,
            "unit": unit,
            "source_type": source_type,
            "source_id": str(source_id)[:160],
            "occurred_at": occurred_at,
            "period_start": subscription.current_period_start,
            "metadata": _safe_metadata(metadata),
            "correction": correction,
        },
    )
    if not created:
        return event, False
    aggregate, _ = UsageAggregate.objects.select_for_update().get_or_create(
        organization=organization,
        subscription=subscription,
        meter_key=meter_key,
        period_start=subscription.current_period_start,
        defaults={"period_end": subscription.current_period_end, "quantity": Decimal(0)},
    )
    UsageAggregate.objects.filter(pk=aggregate.pk).update(quantity=F("quantity") + amount)
    return event, created


def record_message_usage(message) -> list[UsageEvent]:
    """Meter a durable external message without copying customer content."""
    channel = message.channel_connection
    channel_type = str(channel.type)
    if channel.provider == "internal_test":
        return []
    provider_meter = {
        "instagram": "instagram_messages",
        "telegram": "telegram_messages",
        "gmail": "gmail_messages",
    }.get(channel_type)
    meters = ["external_messages", *([provider_meter] if provider_meter else [])]
    events = []
    for meter_key in meters:
        event, _ = record_usage(
            organization=message.organization,
            meter_key=meter_key,
            quantity=1,
            unit="message",
            source_type=f"{channel_type}_message",
            source_id=str(message.id),
            idempotency_key=f"message:{message.id}:{meter_key}",
            occurred_at=message.occurred_at,
            metadata={"provider": channel.provider, "direction": message.direction},
        )
        events.append(event)
    return events


@transaction.atomic
def reconcile_usage(*, organization: Organization, subscription: Subscription | None = None) -> list[UsageAggregate]:
    subscription = subscription or _active_subscription(organization)
    totals = UsageEvent.objects.filter(
        organization=organization, period_start=subscription.current_period_start
    ).values("meter_key").annotate(quantity=Sum("quantity"))
    seen = set()
    for item in totals:
        seen.add(item["meter_key"])
        UsageAggregate.objects.update_or_create(
            organization=organization,
            subscription=subscription,
            meter_key=item["meter_key"],
            period_start=subscription.current_period_start,
            defaults={"period_end": subscription.current_period_end, "quantity": item["quantity"]},
        )
    UsageAggregate.objects.filter(
        organization=organization, subscription=subscription, period_start=subscription.current_period_start
    ).exclude(meter_key__in=seen).update(quantity=0)
    return list(UsageAggregate.objects.filter(organization=organization, subscription=subscription))


def calculate_overage(price: PlanPrice, usage: dict[str, Decimal]) -> list[dict]:
    lines = []
    for meter, raw_quantity in usage.items():
        included = Decimal(str(price.included_usage.get(meter, 0)))
        quantity = Decimal(str(raw_quantity))
        billable = max(Decimal(0), quantity - included)
        config = price.overage_rates.get(meter) or {}
        if not billable or not isinstance(config, dict) or "unit_amount_minor" not in config:
            continue
        unit_amount = int(config["unit_amount_minor"])
        amount = int((billable * Decimal(unit_amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        lines.append(
            {
                "meter_key": meter,
                "quantity": billable,
                "included": included,
                "unit_amount_minor": unit_amount,
                "amount_minor": amount,
                "policy": "flat_overage",
            }
        )
    return lines


@transaction.atomic
def next_invoice_number(now=None) -> str:
    now = now or timezone.now()
    sequence, _ = InvoiceSequence.objects.select_for_update().get_or_create(year=now.year)
    value = sequence.next_value
    sequence.next_value = F("next_value") + 1
    sequence.save(update_fields=["next_value", "updated_at"])
    return f"{settings.BILLING_INVOICE_PREFIX}-{now.year}-{value:06d}"


@transaction.atomic
def generate_invoice(subscription: Subscription, *, due_days=7) -> Invoice:
    aggregates = {
        item.meter_key: Decimal(str(item.quantity))
        for item in UsageAggregate.objects.filter(
            subscription=subscription, period_start=subscription.current_period_start
        )
    }
    invoice = Invoice.objects.create(
        organization=subscription.organization,
        billing_account=subscription.billing_account,
        subscription=subscription,
        provider=subscription.provider,
        invoice_number=next_invoice_number(),
        currency=subscription.price.currency,
        period_start=subscription.current_period_start,
        period_end=subscription.current_period_end,
        due_at=timezone.now() + timedelta(days=due_days),
    )
    base = int(subscription.price.amount_minor)
    InvoiceLine.objects.create(
        invoice=invoice,
        line_type=InvoiceLine.LineType.BASE,
        description=f"{subscription.plan.display_name} v{subscription.plan.version}",
        feature_or_meter_key=subscription.plan.key,
        quantity=Decimal(1),
        unit_amount_minor=base,
        amount_minor=base,
        source_period={"start": subscription.current_period_start.isoformat(), "end": subscription.current_period_end.isoformat()},
        pricing_snapshot={
            "plan_id": str(subscription.plan_id),
            "plan_key": subscription.plan.key,
            "plan_version": subscription.plan.version,
            "price_id": str(subscription.price_id),
            "currency": subscription.price.currency,
            "billing_interval": subscription.price.billing_interval,
        },
    )
    overages = calculate_overage(subscription.price, aggregates)
    for item in overages:
        InvoiceLine.objects.create(
            invoice=invoice,
            line_type=InvoiceLine.LineType.USAGE,
            description=f"{item['meter_key'].replace('_', ' ')} overage",
            feature_or_meter_key=item["meter_key"],
            quantity=item["quantity"],
            unit_amount_minor=item["unit_amount_minor"],
            amount_minor=item["amount_minor"],
            source_period={"start": subscription.current_period_start.isoformat(), "end": subscription.current_period_end.isoformat()},
            pricing_snapshot={"included": str(item["included"]), "policy": item["policy"]},
        )
    subtotal = base + sum(item["amount_minor"] for item in overages)
    invoice.subtotal_minor = subtotal
    invoice.total_minor = subtotal
    invoice.amount_due_minor = subtotal
    invoice.save()
    return invoice


@transaction.atomic
def issue_invoice(invoice: Invoice) -> Invoice:
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status == Invoice.Status.OPEN:
        return invoice
    if invoice.status != Invoice.Status.DRAFT:
        raise BillingError("invoice_transition_invalid", "Only a draft invoice can be issued.")
    invoice.status = Invoice.Status.OPEN
    invoice.issued_at = timezone.now()
    invoice.save(update_fields=["status", "issued_at", "updated_at"])
    notify(invoice.organization, "invoice_issued", f"invoice:{invoice.id}:issued", {"invoice_number": invoice.invoice_number})
    return invoice


@transaction.atomic
def mark_invoice_paid(invoice: Invoice, *, provider_payment_id="", reviewed=False) -> tuple[Invoice, PaymentAttempt]:
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status == Invoice.Status.PAID:
        attempt = invoice.payment_attempts.filter(status=PaymentAttempt.Status.SUCCEEDED).first()
        return invoice, attempt
    if invoice.status != Invoice.Status.OPEN:
        raise BillingError("invoice_transition_invalid", "Only an open invoice can be marked paid.")
    if invoice.provider == "manual" and not reviewed:
        raise BillingError("manual_review_required", "A reviewed internal action is required for manual payment.", status_code=403)
    attempt = invoice.payment_attempts.filter(
        status__in=[PaymentAttempt.Status.CREATED, PaymentAttempt.Status.PENDING]
    ).order_by("created_at").first()
    if attempt:
        attempt.status = PaymentAttempt.Status.SUCCEEDED
        attempt.provider_payment_id = str(provider_payment_id or attempt.provider_payment_id)[:160]
        attempt.completed_at = timezone.now()
        attempt.save(update_fields=["status", "provider_payment_id", "completed_at"])
    else:
        attempt = PaymentAttempt.objects.create(
            invoice=invoice,
            provider=invoice.provider,
            provider_payment_id=str(provider_payment_id)[:160],
            status=PaymentAttempt.Status.SUCCEEDED,
            amount_minor=invoice.amount_due_minor,
            currency=invoice.currency,
            attempted_at=timezone.now(),
            completed_at=timezone.now(),
        )
    invoice.status = Invoice.Status.PAID
    invoice.amount_paid_minor = invoice.total_minor
    invoice.amount_due_minor = 0
    invoice.paid_at = timezone.now()
    invoice.save(update_fields=["status", "amount_paid_minor", "amount_due_minor", "paid_at", "updated_at"])
    recover_payment(invoice.subscription)
    notify(invoice.organization, "payment_succeeded", f"invoice:{invoice.id}:paid", {"invoice_number": invoice.invoice_number})
    return invoice, attempt


@transaction.atomic
def void_invoice(invoice: Invoice) -> Invoice:
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status == Invoice.Status.VOID:
        return invoice
    if invoice.status not in {Invoice.Status.DRAFT, Invoice.Status.OPEN}:
        raise BillingError("invoice_transition_invalid", "This invoice cannot be voided.")
    invoice.status = Invoice.Status.VOID
    invoice.voided_at = timezone.now()
    invoice.amount_due_minor = 0
    invoice.save(update_fields=["status", "voided_at", "amount_due_minor", "updated_at"])
    return invoice


def change_preview(subscription: Subscription, target_price: PlanPrice) -> dict:
    if target_price.status != PlanPrice.Status.ACTIVE or target_price.plan.status != PlanCatalog.Status.ACTIVE:
        raise BillingError("plan_unavailable", "The requested plan price is not active.", status_code=422)
    if target_price.currency != subscription.price.currency:
        raise BillingError("currency_mismatch", "Plan changes cannot silently convert currency.", status_code=422)
    change_type = (
        ScheduledSubscriptionChange.ChangeType.INTERVAL
        if target_price.plan_id == subscription.plan_id
        else ScheduledSubscriptionChange.ChangeType.UPGRADE
        if target_price.amount_minor > subscription.price.amount_minor
        else ScheduledSubscriptionChange.ChangeType.DOWNGRADE
    )
    return {
        "subscription_id": str(subscription.id),
        "current_plan": {"key": subscription.plan.key, "version": subscription.plan.version},
        "target_plan": {"id": str(target_price.plan_id), "key": target_price.plan.key, "version": target_price.plan.version},
        "target_price_id": str(target_price.id),
        "currency": target_price.currency,
        "current_amount_minor": subscription.price.amount_minor,
        "target_amount_minor": target_price.amount_minor,
        "effective_at": subscription.current_period_end,
        "change_type": change_type,
        "proration": "not_applied",
    }


@transaction.atomic
def schedule_change(subscription: Subscription, target_price: PlanPrice, *, requested_by=None) -> ScheduledSubscriptionChange:
    subscription = Subscription.objects.select_for_update().select_related("plan", "price").get(pk=subscription.pk)
    preview = change_preview(subscription, target_price)
    ScheduledSubscriptionChange.objects.filter(
        subscription=subscription, status=ScheduledSubscriptionChange.Status.SCHEDULED
    ).update(status=ScheduledSubscriptionChange.Status.CANCELLED)
    return ScheduledSubscriptionChange.objects.create(
        subscription=subscription,
        target_plan=target_price.plan,
        target_price=target_price,
        effective_at=subscription.current_period_end,
        change_type=preview["change_type"],
        preview_snapshot={**preview, "effective_at": subscription.current_period_end.isoformat()},
        requested_by=requested_by,
    )


@transaction.atomic
def apply_scheduled_change(change: ScheduledSubscriptionChange, *, at=None) -> Subscription:
    at = at or timezone.now()
    change = ScheduledSubscriptionChange.objects.select_for_update().select_related(
        "subscription", "target_plan", "target_price"
    ).get(pk=change.pk)
    if change.status == ScheduledSubscriptionChange.Status.APPLIED:
        return change.subscription
    if change.status != ScheduledSubscriptionChange.Status.SCHEDULED or change.effective_at > at:
        raise BillingError("change_not_due", "The scheduled plan change is not due.")
    subscription = Subscription.objects.select_for_update().get(pk=change.subscription_id)
    subscription.plan = change.target_plan
    subscription.price = change.target_price
    subscription.current_period_start = change.effective_at
    subscription.current_period_end = _period_end(change.effective_at, change.target_price.billing_interval)
    subscription.save()
    change.status = ScheduledSubscriptionChange.Status.APPLIED
    change.save(update_fields=["status"])
    sync_entitlement(subscription)
    notify(subscription.organization, "plan_change_applied", f"change:{change.id}:applied", {"plan_key": subscription.plan.key})
    return subscription


@transaction.atomic
def cancel_subscription(subscription: Subscription) -> Subscription:
    subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
    if subscription.status in {Subscription.Status.CANCELLED, Subscription.Status.EXPIRED}:
        return subscription
    subscription.cancel_at_period_end = True
    subscription.cancelled_at = timezone.now()
    subscription.save(update_fields=["cancel_at_period_end", "cancelled_at", "updated_at"])
    return subscription


@transaction.atomic
def resume_subscription(subscription: Subscription) -> Subscription:
    subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
    if subscription.ended_at or subscription.current_period_end <= timezone.now():
        raise BillingError("resume_not_available", "This subscription can no longer be resumed.")
    subscription.cancel_at_period_end = False
    subscription.cancelled_at = None
    subscription.save(update_fields=["cancel_at_period_end", "cancelled_at", "updated_at"])
    return subscription


@transaction.atomic
def payment_failed(subscription: Subscription, *, failure_code="payment_failed") -> Subscription:
    subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
    now = timezone.now()
    subscription.status = Subscription.Status.GRACE
    subscription.grace_ends_at = now + timedelta(days=settings.BILLING_GRACE_DAYS)
    subscription.provider_state = {**subscription.provider_state, "last_failure_code": str(failure_code)[:80]}
    subscription.save(update_fields=["status", "grace_ends_at", "provider_state", "updated_at"])
    sync_entitlement(subscription)
    notify(subscription.organization, "grace_started", f"subscription:{subscription.id}:grace:{subscription.grace_ends_at.isoformat()}", {"grace_ends_at": subscription.grace_ends_at.isoformat()})
    return subscription


@transaction.atomic
def expire_grace(subscription: Subscription, *, at=None) -> Subscription:
    at = at or timezone.now()
    subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
    if subscription.status != Subscription.Status.GRACE or not subscription.grace_ends_at or subscription.grace_ends_at > at:
        return subscription
    subscription.status = Subscription.Status.PAUSED
    subscription.save(update_fields=["status", "updated_at"])
    sync_entitlement(subscription)
    notify(subscription.organization, "subscription_restricted", f"subscription:{subscription.id}:restricted", {})
    return subscription


@transaction.atomic
def recover_payment(subscription: Subscription) -> Subscription:
    subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
    if subscription.status not in {Subscription.Status.PAST_DUE, Subscription.Status.GRACE, Subscription.Status.PAUSED}:
        return subscription
    subscription.status = Subscription.Status.ACTIVE
    subscription.grace_ends_at = None
    subscription.save(update_fields=["status", "grace_ends_at", "updated_at"])
    sync_entitlement(subscription)
    notify(subscription.organization, "payment_recovered", f"subscription:{subscription.id}:recovered", {})
    return subscription


@transaction.atomic
def notify(organization: Organization, event_type: str, idempotency_key: str, safe_context: dict) -> BillingNotification:
    row, _ = BillingNotification.objects.get_or_create(
        organization=organization,
        idempotency_key=idempotency_key,
        defaults={
            "event_type": event_type,
            "locale": organization.default_language,
            "adapter": "console",
            "delivery_status": "development_console" if settings.DEBUG or settings.TESTING else "not_configured",
            "safe_context": _safe_metadata(safe_context),
        },
    )
    return row


def process_verified_event(provider_key: str, event: VerifiedBillingEvent) -> tuple[BillingProviderEvent, bool]:
    row, created = BillingProviderEvent.objects.get_or_create(
        provider=provider_key,
        provider_event_id=event.provider_event_id,
        defaults={"event_type": event.event_type, "payload_hash": event.payload_hash},
    )
    if not created:
        if row.payload_hash != event.payload_hash:
            raise BillingError("provider_event_conflict", "The provider event ID was reused with different content.")
        return row, False
    try:
        with transaction.atomic():
            if event.object_type != "invoice":
                row.status = BillingProviderEvent.Status.IGNORED
            else:
                invoice = Invoice.objects.select_related("subscription").get(
                    provider_invoice_id=event.object_id,
                    provider=provider_key,
                )
                if event.currency != invoice.currency or event.amount_minor != invoice.amount_due_minor:
                    raise BillingError(
                        "payment_mismatch",
                        "Provider payment amount or currency does not match the invoice.",
                    )
                if event.status == "succeeded":
                    mark_invoice_paid(invoice, provider_payment_id=event.object_id, reviewed=True)
                elif event.status == "failed":
                    attempt = invoice.payment_attempts.filter(
                        status__in=[PaymentAttempt.Status.CREATED, PaymentAttempt.Status.PENDING]
                    ).order_by("created_at").first()
                    if attempt:
                        attempt.status = PaymentAttempt.Status.FAILED
                        attempt.failure_code = "provider_payment_failed"
                        attempt.completed_at = timezone.now()
                        attempt.save(update_fields=["status", "failure_code", "completed_at"])
                    else:
                        PaymentAttempt.objects.create(
                            invoice=invoice,
                            provider=provider_key,
                            provider_payment_id=event.object_id,
                            status=PaymentAttempt.Status.FAILED,
                            amount_minor=event.amount_minor,
                            currency=event.currency,
                            failure_code="provider_payment_failed",
                            attempted_at=timezone.now(),
                            completed_at=timezone.now(),
                        )
                    payment_failed(invoice.subscription)
                else:
                    row.status = BillingProviderEvent.Status.IGNORED
                if row.status != BillingProviderEvent.Status.IGNORED:
                    row.status = BillingProviderEvent.Status.PROCESSED
            row.processed_at = timezone.now()
            row.save(update_fields=["status", "processed_at"])
    except Exception as exc:
        row.status = BillingProviderEvent.Status.FAILED
        row.safe_error = getattr(exc, "code", type(exc).__name__)[:200]
        row.processed_at = timezone.now()
        row.save(update_fields=["status", "safe_error", "processed_at"])
        raise
    return row, True


@transaction.atomic
def checkout_state(invoice: Invoice) -> dict:
    try:
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        provider = get_billing_provider(invoice.provider)
        result = provider.create_checkout_session(invoice_id=str(invoice.id))
        if invoice.provider == "fake" and not invoice.provider_invoice_id:
            invoice.provider_invoice_id = result.provider_id
            invoice.save(update_fields=["provider_invoice_id", "updated_at"])
        payment = provider.create_payment_attempt(
            invoice_id=str(invoice.id),
            amount_minor=invoice.amount_due_minor,
            currency=invoice.currency,
        )
        PaymentAttempt.objects.get_or_create(
            invoice=invoice,
            provider=invoice.provider,
            provider_payment_id=payment.provider_id,
            defaults={
                "status": PaymentAttempt.Status.PENDING,
                "amount_minor": invoice.amount_due_minor,
                "currency": invoice.currency,
                "attempted_at": timezone.now(),
            },
        )
        return {
            "status": result.status,
            "provider": invoice.provider,
            "action_url": result.action_url,
            "online_payment_connected": bool(result.action_url),
        }
    except BillingProviderError as exc:
        return {
            "status": exc.code,
            "provider": invoice.provider,
            "message": exc.message,
            "online_payment_connected": False,
            "contact_sales": True,
        }


def mutation_key(request, operation: str, *, required=True) -> str:
    raw = str(request.headers.get("Idempotency-Key", "")).strip()
    if required and len(raw) < 8:
        raise BillingError("idempotency_key_required", "A stable Idempotency-Key header is required.", status_code=400)
    return hashlib.sha256(f"{operation}:{raw}".encode()).hexdigest() if raw else ""


def remember_mutation(organization: Organization, operation: str, key_hash: str, *, result, response: dict):
    return BillingIdempotencyRecord.objects.create(
        organization=organization,
        operation=operation,
        key_hash=key_hash,
        result_type=type(result).__name__,
        result_id=str(getattr(result, "pk", "")),
        response_safe=response,
    )


def existing_mutation(organization: Organization, operation: str, key_hash: str):
    return BillingIdempotencyRecord.objects.filter(
        organization=organization, operation=operation, key_hash=key_hash
    ).first()


@transaction.atomic
def publish_plan(plan: PlanCatalog) -> PlanCatalog:
    plan = PlanCatalog.objects.select_for_update().get(pk=plan.pk)
    if plan.status == PlanCatalog.Status.ACTIVE:
        return plan
    if plan.status != PlanCatalog.Status.DRAFT:
        raise BillingError("plan_transition_invalid", "Only a draft plan can be published.")
    known = set(FeatureDefinition.objects.filter(active=True).values_list("key", flat=True))
    unknown = sorted(set(plan.feature_values) - known)
    if unknown:
        raise BillingError("unknown_feature", "The plan contains unknown feature keys.", details={"keys": unknown})
    if not plan.prices.filter(status=PlanPrice.Status.DRAFT).exists():
        raise BillingError("price_required", "At least one draft price is required before publication.")
    plan.status = PlanCatalog.Status.ACTIVE
    plan.published_at = timezone.now()
    plan.save(update_fields=["status", "published_at", "updated_at"])
    for price in plan.prices.filter(status=PlanPrice.Status.DRAFT):
        price.status = PlanPrice.Status.ACTIVE
        price.effective_from = price.effective_from or timezone.now()
        price.save(update_fields=["status", "effective_from", "updated_at"])
    return plan
