from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

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
    UsageEvent,
)
from billing.providers import (
    BillingProviderError,
    FakeBillingProvider,
    ManualBillingProvider,
    VerifiedBillingEvent,
)
from billing.services import (
    _period_end,
    BillingError,
    EntitlementService,
    apply_scheduled_change,
    calculate_overage,
    cancel_subscription,
    change_preview,
    checkout_state,
    ensure_billing_for_organization,
    ensure_catalog,
    expire_grace,
    feature_allowed,
    generate_invoice,
    issue_invoice,
    mark_invoice_paid,
    payment_failed,
    process_verified_event,
    publish_plan,
    reconcile_usage,
    record_usage,
    recover_payment,
    resume_subscription,
    schedule_change,
    void_invoice,
)
from billing.tasks import process_billing_lifecycle
from control_plane.models import (
    OrganizationEntitlement,
    PlanCatalog,
    PlatformAccessStatus,
    PlatformAuditEvent,
    PlatformMFADevice,
    PlatformRole,
    PlatformStaffAccess,
)
from organizations.models import OrganizationMembership, OrganizationMembershipRole, OrganizationStatus
from organizations.services import create_organization
from users.models import User


TEST_ENCRYPTION_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


@override_settings(
    BILLING_ENABLE=True,
    BILLING_PROVIDER="fake",
    BILLING_DEFAULT_PLAN_KEY="starter",
    BILLING_DEFAULT_CURRENCY="UZS",
    BILLING_TRIAL_DAYS=14,
    BILLING_GRACE_DAYS=7,
    BILLING_INVOICE_PREFIX="TEST",
    BILLING_FAKE_PROVIDER=True,
    DEBUG=True,
)
class BillingServiceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="billing-owner@example.test", email="billing-owner@example.test", password="test-only-password-92!"
        )
        self.organization = create_organization(
            creator=self.owner, name="Billing Test", slug="billing-test", default_language="en"
        )
        self.account, self.subscription, self.entitlement = ensure_billing_for_organization(self.organization)

    def test_registration_creates_exactly_one_trial_and_retry_is_idempotent(self):
        self.assertEqual(BillingAccount.objects.filter(organization=self.organization).count(), 1)
        self.assertEqual(Subscription.objects.filter(organization=self.organization).count(), 1)
        self.assertEqual(self.subscription.status, Subscription.Status.TRIALING)
        ensure_billing_for_organization(self.organization)
        self.assertEqual(Subscription.objects.filter(organization=self.organization).count(), 1)
        self.assertEqual(OrganizationEntitlement.objects.filter(organization=self.organization).count(), 1)

    def test_period_boundaries_and_unavailable_price_are_explicit(self):
        leap_day = datetime(2024, 2, 29, tzinfo=timezone.get_current_timezone())
        self.assertEqual(_period_end(leap_day, PlanPrice.Interval.YEAR).day, 28)
        self.assertEqual((_period_end(leap_day, PlanPrice.Interval.ONE_TIME) - leap_day).days, 3650)
        december = datetime(2026, 12, 12, tzinfo=timezone.get_current_timezone())
        self.assertEqual(_period_end(december, PlanPrice.Interval.MONTH).month, 1)
        unavailable = PlanCatalog.objects.create(
            key="unpriced", version=1, display_name="Unpriced", feature_values={"crm": True}
        )
        from billing.services import _active_price

        with self.assertRaisesMessage(BillingError, "No active price"):
            _active_price(unavailable, "UZS")

    def test_existing_entitlement_is_reused_as_single_source_of_truth(self):
        self.assertEqual(self.entitlement.plan_id, self.subscription.plan_id)
        self.assertEqual(self.entitlement.status, OrganizationEntitlement.Status.TRIAL)
        self.assertFalse(hasattr(self.organization, "billing_entitlement"))

    def test_plan_versions_publish_and_then_become_immutable(self):
        plan = PlanCatalog.objects.create(
            key="growth", version=1, display_name="Growth", feature_values={"crm": True}
        )
        price = PlanPrice.objects.create(
            plan=plan, currency="UZS", billing_interval="month", amount_minor=2000
        )
        publish_plan(plan)
        plan.display_name = "Changed"
        with self.assertRaisesMessage(ValidationError, "immutable"):
            plan.save()
        price.refresh_from_db()
        price.amount_minor = 3000
        with self.assertRaisesMessage(ValidationError, "immutable"):
            price.save()
        newer = PlanCatalog.objects.create(
            key="growth", version=2, display_name="Growth 2", feature_values={"crm": True}
        )
        self.assertNotEqual(plan.id, newer.id)

    def test_publish_rejects_unknown_features_and_requires_price(self):
        unknown = PlanCatalog.objects.create(
            key="unknown", version=1, display_name="Unknown", feature_values={"not_real": True}
        )
        PlanPrice.objects.create(plan=unknown, currency="UZS", billing_interval="month", amount_minor=1)
        with self.assertRaisesMessage(BillingError, "unknown feature"):
            publish_plan(unknown)
        empty = PlanCatalog.objects.create(
            key="empty", version=1, display_name="Empty", feature_values={"crm": True}
        )
        with self.assertRaisesMessage(BillingError, "price"):
            publish_plan(empty)

    def test_money_currency_and_overage_are_deterministic(self):
        plan = PlanCatalog.objects.create(
            key="overage", version=1, display_name="Overage", feature_values={"crm": True}
        )
        price = PlanPrice.objects.create(
            plan=plan, currency="UZS", billing_interval="month", amount_minor=100
        )
        price.overage_rates = {"sms_segments": {"unit_amount_minor": 25}}
        price.included_usage = {"sms_segments": 10}
        price.save()
        rows = calculate_overage(price, {"sms_segments": Decimal("12.5")})
        self.assertEqual(rows[0]["amount_minor"], 63)
        price.currency = "US"
        with self.assertRaises(ValidationError):
            price.save()

    def test_entitlements_fail_closed_and_security_precedes_override(self):
        service = EntitlementService(self.organization)
        self.assertFalse(service.resolve("unregistered_feature").allowed)
        self.entitlement.feature_overrides = {"sms": True}
        self.entitlement.override_reason = "Synthetic temporary grant"
        self.entitlement.override_expires_at = timezone.now() + timedelta(hours=1)
        self.entitlement.save()
        self.organization.status = OrganizationStatus.SUSPENDED
        self.organization.save(update_fields=["status"])
        self.assertFalse(EntitlementService(self.organization).resolve("sms").allowed)
        self.assertTrue(EntitlementService(self.organization).resolve("billing_access").allowed)

    def test_expired_override_is_ignored(self):
        self.entitlement.feature_overrides = {"sms": False}
        self.entitlement.override_expires_at = timezone.now() - timedelta(seconds=1)
        self.entitlement.save()
        self.assertTrue(EntitlementService(self.organization).resolve("sms").allowed)

    def test_entitlement_capacity_grace_and_safe_reads(self):
        self.entitlement.limit_overrides = {"max_branches": 1}
        self.entitlement.save(update_fields=["limit_overrides", "updated_at"])
        with self.assertRaisesMessage(BillingError, "limit"):
            EntitlementService(self.organization).require_capacity("max_branches", 1)
        self.assertTrue(feature_allowed(self.organization, "crm"))
        self.subscription.status = Subscription.Status.GRACE
        self.subscription.grace_ends_at = timezone.now() - timedelta(seconds=1)
        self.subscription.save(update_fields=["status", "grace_ends_at", "updated_at"])
        self.assertEqual(
            EntitlementService(self.organization).resolve("sms").enforcement_reason,
            "grace_expired",
        )
        self.assertTrue(EntitlementService(self.organization).resolve("billing_access").allowed)

    def test_usage_is_idempotent_aggregated_and_metadata_is_allowlisted(self):
        event, created = record_usage(
            organization=self.organization, meter_key="sms_segments", quantity=2, unit="segment",
            source_type="sms_message", source_id="synthetic", idempotency_key="usage-key-0001",
            metadata={"provider": "fake", "message_body": "must-not-persist"},
        )
        duplicate, duplicate_created = record_usage(
            organization=self.organization, meter_key="sms_segments", quantity=2, unit="segment",
            source_type="sms_message", source_id="synthetic", idempotency_key="usage-key-0001",
        )
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(event.id, duplicate.id)
        self.assertNotIn("message_body", event.metadata)
        self.assertEqual(UsageAggregate.objects.get(meter_key="sms_segments").quantity, Decimal("2"))

    def test_negative_usage_requires_append_only_correction(self):
        with self.assertRaisesMessage(BillingError, "correction"):
            record_usage(
                organization=self.organization, meter_key="voice_seconds", quantity=-10, unit="second",
                source_type="correction", source_id="one", idempotency_key="negative-not-allowed",
            )
        record_usage(
            organization=self.organization, meter_key="voice_seconds", quantity=60, unit="second",
            source_type="voice_call", source_id="one", idempotency_key="voice-original",
        )
        correction, _ = record_usage(
            organization=self.organization, meter_key="voice_seconds", quantity=-10, unit="second",
            source_type="correction", source_id="one", idempotency_key="voice-correction", correction=True,
        )
        with self.assertRaises(ValidationError):
            correction.delete()
        self.assertEqual(reconcile_usage(organization=self.organization)[0].quantity, Decimal("50"))

    def test_hard_usage_limit_blocks_but_soft_limit_reports_remaining(self):
        plan = PlanCatalog.objects.create(
            key="limited", version=1, display_name="Limited", status=PlanCatalog.Status.ACTIVE,
            feature_values={**self.subscription.plan.feature_values, "monthly_ai_runs": 1},
        )
        price = PlanPrice.objects.create(
            plan=plan, currency="UZS", billing_interval="month", amount_minor=100, status=PlanPrice.Status.ACTIVE
        )
        self.subscription.plan = plan
        self.subscription.price = price
        self.subscription.save()
        record_usage(
            organization=self.organization, meter_key="ai_runs", quantity=1, unit="run",
            source_type="ai_run", source_id="one", idempotency_key="ai-limit-one",
        )
        snapshot = EntitlementService(self.organization).resolve("monthly_ai_runs")
        self.assertFalse(snapshot.allowed)
        self.assertEqual(snapshot.enforcement_reason, "usage_limit_reached")

    def test_invoice_snapshots_overage_and_issued_rows_are_immutable(self):
        record_usage(
            organization=self.organization, meter_key="sms_segments", quantity=3, unit="segment",
            source_type="sms", source_id="invoice-source", idempotency_key="invoice-usage",
        )
        invoice = generate_invoice(self.subscription)
        self.assertEqual(invoice.tax_minor, 0)
        self.assertEqual(invoice.lines.filter(line_type=InvoiceLine.LineType.BASE).count(), 1)
        invoice = issue_invoice(invoice)
        invoice.total_minor += 1
        with self.assertRaises(ValidationError):
            invoice.save()
        line = invoice.lines.first()
        line.amount_minor += 1
        with self.assertRaises(ValidationError):
            line.save()
        with self.assertRaises(ValidationError):
            InvoiceLine.objects.create(
                invoice=invoice,
                line_type=InvoiceLine.LineType.ADJUSTMENT,
                description="Late mutation",
                quantity=Decimal("1"),
                unit_amount_minor=1,
                amount_minor=1,
            )

    def test_invoice_numbers_are_unique_and_manual_paid_requires_review(self):
        first = generate_invoice(self.subscription)
        second = generate_invoice(self.subscription)
        self.assertNotEqual(first.invoice_number, second.invoice_number)
        issue_invoice(first)
        first.provider = "manual"
        first.save(update_fields=["provider", "updated_at"])
        with self.assertRaisesMessage(BillingError, "reviewed"):
            mark_invoice_paid(first)
        paid, attempt = mark_invoice_paid(first, reviewed=True)
        self.assertEqual(paid.status, Invoice.Status.PAID)
        self.assertEqual(attempt.status, PaymentAttempt.Status.SUCCEEDED)

    def test_void_invoice_is_idempotent_and_paid_invoice_cannot_void(self):
        draft = generate_invoice(self.subscription)
        self.assertEqual(void_invoice(void_invoice(draft)).status, Invoice.Status.VOID)
        paid = generate_invoice(self.subscription)
        issue_invoice(paid)
        mark_invoice_paid(paid, reviewed=True)
        with self.assertRaises(BillingError):
            void_invoice(paid)

    def test_invoice_replays_and_manual_checkout_are_honest(self):
        invoice = issue_invoice(generate_invoice(self.subscription))
        self.assertEqual(issue_invoice(invoice).status, Invoice.Status.OPEN)
        paid, attempt = mark_invoice_paid(invoice, reviewed=True)
        replay, replay_attempt = mark_invoice_paid(paid, reviewed=True)
        self.assertEqual((replay.status, replay_attempt.id), (Invoice.Status.PAID, attempt.id))
        paid.provider = "manual"
        paid.save(update_fields=["provider", "updated_at"])
        state = checkout_state(paid)
        self.assertFalse(state["online_payment_connected"])
        self.assertEqual(state["status"], "payment_provider_not_connected")

    def test_change_preview_schedules_applies_cancel_and_resume(self):
        manual, _ = ensure_catalog()
        target = PlanPrice.objects.get(plan=manual, currency="UZS")
        preview = change_preview(self.subscription, target)
        self.assertEqual(preview["proration"], "not_applied")
        change = schedule_change(self.subscription, target, requested_by=self.owner)
        change.effective_at = timezone.now() - timedelta(seconds=1)
        change.save(update_fields=["effective_at"])
        updated = apply_scheduled_change(change)
        self.assertEqual(updated.plan_id, manual.id)
        cancel_subscription(updated)
        updated.refresh_from_db()
        self.assertTrue(updated.cancel_at_period_end)
        resume_subscription(updated)
        updated.refresh_from_db()
        self.assertFalse(updated.cancel_at_period_end)

    def test_invalid_and_replayed_lifecycle_transitions_fail_safely(self):
        plan = PlanCatalog.objects.create(
            key="pending", version=1, display_name="Pending", feature_values={"crm": True}
        )
        price = PlanPrice.objects.create(
            plan=plan, currency="UZS", billing_interval="month", amount_minor=100
        )
        with self.assertRaisesMessage(BillingError, "not active"):
            change_preview(self.subscription, price)
        publish_plan(plan)
        price.refresh_from_db()
        change = schedule_change(self.subscription, price)
        with self.assertRaisesMessage(BillingError, "not due"):
            apply_scheduled_change(change)
        change.effective_at = timezone.now() - timedelta(seconds=1)
        change.save(update_fields=["effective_at"])
        applied = apply_scheduled_change(change)
        self.assertEqual(apply_scheduled_change(change).id, applied.id)
        applied.ended_at = timezone.now()
        applied.save(update_fields=["ended_at", "updated_at"])
        with self.assertRaisesMessage(BillingError, "no longer"):
            resume_subscription(applied)

    def test_currency_mismatch_change_is_rejected(self):
        plan = PlanCatalog.objects.create(
            key="usd", version=1, display_name="USD", status="active", feature_values={"crm": True}
        )
        price = PlanPrice.objects.create(
            plan=plan, currency="USD", billing_interval="month", amount_minor=10, status="active"
        )
        with self.assertRaisesMessage(BillingError, "currency"):
            change_preview(self.subscription, price)

    def test_grace_expiry_restricts_and_recovery_restores(self):
        payment_failed(self.subscription)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.GRACE)
        self.subscription.grace_ends_at = timezone.now() - timedelta(seconds=1)
        self.subscription.save(update_fields=["grace_ends_at", "updated_at"])
        expire_grace(self.subscription)
        self.subscription.refresh_from_db()
        self.assertFalse(EntitlementService(self.organization).resolve("sms").allowed)
        recover_payment(self.subscription)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)
        self.assertTrue(EntitlementService(self.organization).resolve("sms").allowed)

    def test_lifecycle_task_applies_due_transitions_without_looping(self):
        self.subscription.trial_ends_at = timezone.now() - timedelta(seconds=1)
        self.subscription.save(update_fields=["trial_ends_at", "updated_at"])
        result = process_billing_lifecycle()
        self.assertEqual(result["trial_expired"], 1)
        self.assertEqual(process_billing_lifecycle()["trial_expired"], 0)

    def test_fake_provider_signatures_are_deterministic_and_manual_is_honest(self):
        payload = json.dumps(
            {"id": "evt-1", "type": "payment", "object_type": "invoice", "object_id": "inv-1", "status": "succeeded", "amount_minor": 100, "currency": "UZS"},
            sort_keys=True,
        ).encode()
        provider = FakeBillingProvider()
        signature = provider.sign_event(payload)
        self.assertEqual(provider.parse_verified_webhook(payload=payload, signature=signature).provider_event_id, "evt-1")
        with self.assertRaises(BillingProviderError):
            provider.parse_verified_webhook(payload=payload, signature="invalid")
        with self.assertRaisesMessage(BillingProviderError, "not connected"):
            ManualBillingProvider().create_checkout_session(invoice_id="one")

    def test_provider_event_ignored_conflict_mismatch_and_failure_paths(self):
        ignored = VerifiedBillingEvent(
            "evt-ignored", "customer.updated", "customer", "customer-1", "ok", 0, "UZS", "a" * 64
        )
        row, created = process_verified_event("fake", ignored)
        self.assertTrue(created)
        self.assertEqual(row.status, BillingProviderEvent.Status.IGNORED)
        with self.assertRaisesMessage(BillingError, "reused"):
            process_verified_event(
                "fake",
                VerifiedBillingEvent(
                    "evt-ignored", "customer.updated", "customer", "customer-1", "ok", 0, "UZS", "b" * 64
                ),
            )
        invoice = issue_invoice(generate_invoice(self.subscription))
        invoice.provider_invoice_id = "fake-event-invoice"
        invoice.save(update_fields=["provider_invoice_id", "updated_at"])
        mismatch = VerifiedBillingEvent(
            "evt-mismatch", "payment.updated", "invoice", invoice.provider_invoice_id,
            "succeeded", invoice.amount_due_minor + 1, invoice.currency, "c" * 64,
        )
        with self.assertRaisesMessage(BillingError, "does not match"):
            process_verified_event("fake", mismatch)
        failed_mismatch = BillingProviderEvent.objects.get(provider_event_id="evt-mismatch")
        self.assertEqual(failed_mismatch.status, BillingProviderEvent.Status.FAILED)
        self.assertEqual(failed_mismatch.safe_error, "payment_mismatch")
        failure = VerifiedBillingEvent(
            "evt-failed", "payment.updated", "invoice", invoice.provider_invoice_id,
            "failed", invoice.amount_due_minor, invoice.currency, "d" * 64,
        )
        event, _ = process_verified_event("fake", failure)
        self.assertEqual(event.status, BillingProviderEvent.Status.PROCESSED)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.GRACE)
        self.assertEqual(invoice.payment_attempts.get().status, PaymentAttempt.Status.FAILED)


@override_settings(
    BILLING_ENABLE=True,
    BILLING_PROVIDER="fake",
    BILLING_DEFAULT_PLAN_KEY="starter",
    BILLING_DEFAULT_CURRENCY="UZS",
    BILLING_TRIAL_DAYS=14,
    BILLING_GRACE_DAYS=7,
    BILLING_FAKE_PROVIDER=True,
    DEBUG=True,
)
class BillingAPITests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="api-owner@example.test", email="api-owner@example.test", password="test-only-password-92!"
        )
        self.organization = create_organization(creator=self.owner, name="API Billing", slug="api-billing")
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.headers = {"HTTP_X_ORGANIZATION_ID": str(self.organization.id)}
        self.account, self.subscription, _ = ensure_billing_for_organization(self.organization)

    def test_customer_overview_profile_plans_usage_and_entitlements_are_tenant_scoped(self):
        for path in ("account/", "subscription/", "plans/", "usage/", "entitlements/"):
            response = self.client.get(f"/api/v1/billing/{path}", **self.headers)
            self.assertEqual(response.status_code, 200, response.json())
            self.assertNotIn("provider_customer_id", str(response.json()))
        profile = self.client.patch(
            "/api/v1/billing/account/",
            {"legal_name": "API Billing LLC", "billing_email": "billing@example.test"},
            format="json",
            **self.headers,
        )
        self.assertEqual(profile.status_code, 200)
        self.assertFalse(profile.json()["payment_method_stored"])

    def test_change_cancel_and_resume_are_idempotent(self):
        manual, _ = ensure_catalog()
        price = PlanPrice.objects.get(plan=manual, currency="UZS")
        preview = self.client.post(
            "/api/v1/billing/subscription/change-preview/", {"price_id": str(price.id)}, format="json", **self.headers
        )
        self.assertEqual(preview.status_code, 200)
        idempotent = {**self.headers, "HTTP_IDEMPOTENCY_KEY": "customer-change-0001"}
        first = self.client.post(
            "/api/v1/billing/subscription/change/", {"price_id": str(price.id)}, format="json", **idempotent
        )
        replay = self.client.post(
            "/api/v1/billing/subscription/change/", {"price_id": str(price.id)}, format="json", **idempotent
        )
        self.assertEqual((first.status_code, replay.status_code), (201, 200))
        cancel = self.client.post(
            "/api/v1/billing/subscription/cancel/", {}, format="json",
            **{**self.headers, "HTTP_IDEMPOTENCY_KEY": "customer-cancel-0001"},
        )
        self.assertTrue(cancel.json()["cancel_at_period_end"])
        resume = self.client.post(
            "/api/v1/billing/subscription/resume/", {}, format="json",
            **{**self.headers, "HTTP_IDEMPOTENCY_KEY": "customer-resume-0001"},
        )
        self.assertFalse(resume.json()["cancel_at_period_end"])

    def test_missing_idempotency_key_is_rejected(self):
        response = self.client.post("/api/v1/billing/subscription/cancel/", {}, format="json", **self.headers)
        self.assertEqual(response.status_code, 400)

    def test_cross_tenant_invoice_is_404_and_customer_cannot_set_status(self):
        other_user = User.objects.create_user(username="other-billing", password="test-only-password-92!")
        other = create_organization(creator=other_user, name="Other", slug="other-billing")
        invoice = generate_invoice(ensure_billing_for_organization(other)[1])
        response = self.client.get(f"/api/v1/billing/invoices/{invoice.id}/", **self.headers)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            self.client.patch(f"/api/v1/billing/invoices/{invoice.id}/", {"status": "paid"}, format="json", **self.headers).status_code,
            405,
        )

    def test_checkout_never_renders_card_data(self):
        invoice = issue_invoice(generate_invoice(self.subscription))
        response = self.client.post(
            "/api/v1/billing/checkout/", {"invoice_id": str(invoice.id)}, format="json", **self.headers
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("card", str(response.json()).lower())
        self.assertNotIn("cvv", str(response.json()).lower())
        invoice.refresh_from_db()
        self.assertTrue(invoice.provider_invoice_id.startswith("fake_checkout_"))
        self.assertEqual(invoice.payment_attempts.get().status, PaymentAttempt.Status.PENDING)

    def test_verified_fake_payment_event_is_idempotent_and_completes_pending_attempt(self):
        invoice = issue_invoice(generate_invoice(self.subscription))
        self.client.post(
            "/api/v1/billing/checkout/", {"invoice_id": str(invoice.id)}, format="json", **self.headers
        )
        invoice.refresh_from_db()
        payload = json.dumps(
            {
                "id": "evt-api-paid-1",
                "type": "payment.updated",
                "object_type": "invoice",
                "object_id": invoice.provider_invoice_id,
                "status": "succeeded",
                "amount_minor": invoice.amount_due_minor,
                "currency": invoice.currency,
            },
            separators=(",", ":"),
        ).encode()
        signature = FakeBillingProvider.sign_event(payload)
        invalid = self.client.post(
            "/api/v1/webhooks/billing/fake/",
            data=payload,
            content_type="application/json",
            HTTP_X_BILLING_SIGNATURE="invalid",
        )
        self.assertEqual(invalid.status_code, 403)
        first = self.client.post(
            "/api/v1/webhooks/billing/fake/",
            data=payload,
            content_type="application/json",
            HTTP_X_BILLING_SIGNATURE=signature,
        )
        replay = self.client.post(
            "/api/v1/webhooks/billing/fake/",
            data=payload,
            content_type="application/json",
            HTTP_X_BILLING_SIGNATURE=signature,
        )
        self.assertEqual(first.json(), {"status": "processed", "created": True})
        self.assertEqual(replay.json(), {"status": "processed", "created": False})
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(invoice.payment_attempts.get().status, PaymentAttempt.Status.SUCCEEDED)

    def test_viewer_cannot_mutate_billing_profile(self):
        viewer = User.objects.create_user(username="billing-viewer", password="test-only-password-92!")
        OrganizationMembership.objects.create(
            organization=self.organization, user=viewer, role=OrganizationMembershipRole.VIEWER, status="active"
        )
        self.client.force_authenticate(viewer)
        response = self.client.patch(
            "/api/v1/billing/account/", {"legal_name": "No"}, format="json", **self.headers
        )
        self.assertEqual(response.status_code, 403)


@override_settings(
    BILLING_ENABLE=True,
    BILLING_PROVIDER="fake",
    BILLING_DEFAULT_PLAN_KEY="starter",
    BILLING_DEFAULT_CURRENCY="UZS",
    BILLING_TRIAL_DAYS=14,
    BILLING_GRACE_DAYS=7,
    BILLING_FAKE_PROVIDER=True,
    DEBUG=True,
    CONTROL_PLANE_ENABLE=True,
    CONTROL_PLANE_FAKE_MFA=True,
    CONTROL_PLANE_MFA_REQUIRED=True,
    CONTROL_PLANE_COOKIE_NAME="billing-internal-session",
    CONTROL_PLANE_ALLOWED_IPS=[],
    FIELD_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY,
)
class InternalBillingAPITests(TestCase):
    password = "test-only-platform-password-92!"

    def setUp(self):
        self.customer = User.objects.create_user(username="customer-billing", password=self.password)
        self.organization = create_organization(creator=self.customer, name="Internal Billing", slug="internal-billing")
        self.account, self.subscription, _ = ensure_billing_for_organization(self.organization)
        self.owner_client = self._staff_client("billing-platform-owner", PlatformRole.OWNER)
        self.support_client = self._staff_client("billing-support", PlatformRole.SUPPORT)

    def _staff_client(self, name, role):
        user = User.objects.create_user(
            username=f"{name}@example.test", email=f"{name}@example.test", password=self.password
        )
        access = PlatformStaffAccess.objects.create(user=user, role=role, status=PlatformAccessStatus.ACTIVE)
        PlatformMFADevice.objects.create(
            access=access,
            secret_encrypted=base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("="),
            recovery_code_hashes=[], enabled=True, confirmed_at=timezone.now(),
        )
        client = APIClient(enforce_csrf_checks=True)
        csrf = client.get("/api/v1/internal/auth/csrf/").data["csrftoken"]
        login = client.post(
            "/api/v1/internal/auth/login/", {"email": user.email, "password": self.password},
            format="json", HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(login.status_code, 200, login.data)
        verify = client.post(
            "/api/v1/internal/auth/mfa/verify/", {"code": "000000"}, format="json", HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(verify.status_code, 200, verify.data)
        client.defaults["HTTP_X_CSRFTOKEN"] = csrf
        return client

    def test_internal_plan_creation_publication_and_audit(self):
        created = self.owner_client.post(
            "/api/v1/internal/billing/plans/",
            {
                "key": "scale", "display_name": "Scale", "audience": "sales_assisted",
                "feature_values": {"crm": True, "sms": True}, "currency": "UZS",
                "billing_interval": "month", "amount_minor": 5000000,
                "reason": "Synthetic plan catalogue review",
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        published = self.owner_client.post(
            f"/api/v1/internal/billing/plans/{created.data['id']}/publish/",
            {"reason": "Synthetic publication review"}, format="json",
        )
        self.assertEqual(published.status_code, 200, published.data)
        self.assertEqual(published.data["status"], "active")
        self.assertTrue(PlatformAuditEvent.objects.filter(action="billing.plan.publish").exists())

    def test_manual_mark_paid_requires_privileged_role_and_audits(self):
        invoice = issue_invoice(generate_invoice(self.subscription))
        invoice.provider = "manual"
        invoice.save(update_fields=["provider", "updated_at"])
        denied = self.support_client.post(
            f"/api/v1/internal/billing/invoices/{invoice.id}/mark-paid/",
            {"reason": "Support must not mark payments"}, format="json",
        )
        self.assertEqual(denied.status_code, 403)
        paid = self.owner_client.post(
            f"/api/v1/internal/billing/invoices/{invoice.id}/mark-paid/",
            {"reason": "Reviewed synthetic manual payment"}, format="json",
        )
        self.assertEqual(paid.status_code, 200, paid.data)
        self.assertTrue(PlatformAuditEvent.objects.filter(action="billing.invoice.mark-paid").exists())

    def test_internal_usage_reconciliation_is_idempotent(self):
        record_usage(
            organization=self.organization, meter_key="sms_segments", quantity=2, unit="segment",
            source_type="sms", source_id="internal", idempotency_key="internal-usage",
        )
        payload = {"organization_id": str(self.organization.id), "reason": "Synthetic reconciliation review"}
        first = self.owner_client.post("/api/v1/internal/billing/usage/reconcile/", payload, format="json")
        second = self.owner_client.post("/api/v1/internal/billing/usage/reconcile/", payload, format="json")
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(UsageAggregate.objects.get(meter_key="sms_segments").quantity, Decimal("2"))

    def test_customer_session_cannot_cross_into_internal_billing(self):
        client = APIClient()
        client.force_authenticate(self.customer)
        self.assertIn(client.get("/api/v1/internal/billing/plans/").status_code, {401, 403})
