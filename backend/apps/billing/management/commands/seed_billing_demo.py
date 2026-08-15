from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from billing.models import Invoice, PlanPrice
from billing.services import (
    STARTER_FEATURE_VALUES,
    ensure_billing_for_organization,
    generate_invoice,
    issue_invoice,
    process_verified_event,
    record_usage,
)
from billing.providers import VerifiedBillingEvent
from control_plane.models import PlanCatalog
from organizations.models import Organization


class Command(BaseCommand):
    help = "Seed deterministic fake Billing evidence for local E2E only."

    def handle(self, *args, **options):
        if not (settings.DEBUG and settings.TESTING):
            raise CommandError("seed_billing_demo requires DEBUG and E2E_TESTING.")
        organizations = list(Organization.objects.order_by("created_at"))
        if not organizations:
            raise CommandError("Run seed_client_portal first.")
        growth, _ = PlanCatalog.objects.get_or_create(
            key="growth",
            version=1,
            defaults={
                "display_name": "Growth",
                "description": "Deterministic self-serve plan for Billing E2E previews.",
                "status": PlanCatalog.Status.ACTIVE,
                "audience": PlanCatalog.Audience.SELF_SERVE,
                "feature_values": {
                    **STARTER_FEATURE_VALUES,
                    "ai_autopilot": True,
                    "max_members": 15,
                    "max_branches": 8,
                    "monthly_sms_segments": 5000,
                    "monthly_voice_minutes": "500",
                },
            },
        )
        PlanPrice.objects.get_or_create(
            plan=growth,
            currency=settings.BILLING_DEFAULT_CURRENCY,
            billing_interval=PlanPrice.Interval.MONTH,
            defaults={
                "amount_minor": 24900000,
                "included_usage": {
                    "sms_segments": 5000,
                    "voice_seconds": 30000,
                    "ai_runs": 5000,
                },
                "overage_rates": {},
                "status": PlanPrice.Status.ACTIVE,
            },
        )
        for organization in organizations:
            _, subscription, entitlement = ensure_billing_for_organization(organization)
            if organization.slug == "mehr-clinic":
                # The shared regression organization exercises every provider in
                # one database. Keep the Starter catalog honest while granting a
                # short-lived, explicit E2E entitlement for legacy autopilot and
                # the accumulated provider connections created by those suites.
                entitlement.feature_overrides = {
                    **entitlement.feature_overrides,
                    "ai_autopilot": True,
                }
                entitlement.limit_overrides = {
                    **entitlement.limit_overrides,
                    "max_channel_connections": 25,
                    "max_web_chat_installations": 25,
                }
                entitlement.override_reason = "Deterministic cross-provider E2E regression grant."
                entitlement.override_expires_at = timezone.now() + timedelta(days=1)
                entitlement.save(
                    update_fields=[
                        "feature_overrides",
                        "limit_overrides",
                        "override_reason",
                        "override_expires_at",
                        "updated_at",
                    ]
                )
                for meter, quantity, unit in (
                    ("ai_runs", 42, "run"),
                    ("ai_input_tokens", 12400, "token"),
                    ("sms_segments", 84, "segment"),
                    ("voice_seconds", 1260, "second"),
                    ("external_messages", 315, "message"),
                ):
                    record_usage(
                        organization=organization,
                        meter_key=meter,
                        quantity=quantity,
                        unit=unit,
                        source_type="e2e_seed",
                        source_id=f"seed-{meter}",
                        idempotency_key=f"billing-e2e:{organization.id}:{meter}",
                        metadata={"scenario": "deterministic_e2e"},
                    )
                if not Invoice.objects.filter(
                    organization=organization,
                    period_start=subscription.current_period_start,
                ).exists():
                    issue_invoice(generate_invoice(subscription))
            elif organization.slug == "atlas-academy" and not Invoice.objects.filter(
                organization=organization,
                period_start=subscription.current_period_start,
            ).exists():
                generate_invoice(subscription)
        try:
            process_verified_event(
                "fake",
                VerifiedBillingEvent(
                    provider_event_id="evt-e2e-unmapped",
                    event_type="payment.updated",
                    object_type="invoice",
                    object_id="fake_invoice_not_mapped",
                    status="failed",
                    amount_minor=100,
                    currency=settings.BILLING_DEFAULT_CURRENCY,
                    payload_hash="e" * 64,
                ),
            )
        except Exception:
            pass
        self.stdout.write(self.style.SUCCESS("Deterministic fake Billing demo is ready."))
