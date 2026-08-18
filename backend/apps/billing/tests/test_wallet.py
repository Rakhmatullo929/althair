from __future__ import annotations

import base64
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from billing.bootstrap import BootstrapError, bootstrap_platform, safe_report_json
from billing.models import (
    Invoice,
    OrganizationWallet,
    PaymentAttempt,
    Subscription,
    WalletTransaction,
)
from billing.services import BillingError, ensure_billing_for_organization, generate_invoice, issue_invoice
from billing.wallet import (
    credit_wallet,
    backfill_wallets,
    debit_adjustment,
    ensure_wallet,
    pay_invoice_from_wallet,
    process_wallet_renewal,
    reconcile_wallet,
    retry_due_invoices,
    reverse_transaction,
    safe_wallet_metadata,
    set_wallet_frozen,
)
from control_plane.models import (
    PlatformAccessStatus,
    PlatformAuditEvent,
    PlatformMFADevice,
    PlatformRole,
    PlatformStaffAccess,
)
from organizations.models import Organization
from organizations.services import create_organization
from users.models import User


TEST_ENCRYPTION_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
TEST_PASSWORD = "test-only-wallet-password-92!"


@override_settings(
    BILLING_ENABLE=True,
    BILLING_PROVIDER="fake",
    BILLING_DEFAULT_PAYMENT_SOURCE="wallet",
    BILLING_DEFAULT_PLAN_KEY="starter",
    BILLING_DEFAULT_CURRENCY="UZS",
    BILLING_TRIAL_DAYS=0,
    BILLING_GRACE_DAYS=7,
    BILLING_INVOICE_PREFIX="WALLET",
    DEBUG=True,
)
class WalletServiceTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(username="wallet-customer", password=TEST_PASSWORD)
        self.organization = create_organization(
            creator=self.customer, name="Wallet Tenant", slug="wallet-tenant"
        )
        _, self.subscription, _ = ensure_billing_for_organization(self.organization)
        self.subscription.payment_source = Subscription.PaymentSource.WALLET
        self.subscription.save(update_fields=["payment_source", "updated_at"])
        self.wallet = self.organization.wallets.get(currency="UZS")
        self.staff_user = User.objects.create_user(username="wallet-platform", password=TEST_PASSWORD)
        self.staff = PlatformStaffAccess.objects.create(
            user=self.staff_user,
            role=PlatformRole.OWNER,
            status=PlatformAccessStatus.ACTIVE,
        )

    def _invoice(self, *, due=True):
        invoice = issue_invoice(generate_invoice(self.subscription, due_days=0 if due else 7))
        if due:
            invoice.due_at = timezone.now() - timedelta(seconds=1)
            invoice.save(update_fields=["due_at", "updated_at"])
        return invoice

    def test_credit_is_idempotent_and_ledger_is_immutable(self):
        first = credit_wallet(
            self.wallet,
            amount_minor=20_000_000,
            idempotency_key="top-up-wallet-test-0001",
            platform_staff=self.staff,
            reason="Synthetic reviewed top up",
            retry_invoices=False,
        )
        replay = credit_wallet(
            self.wallet,
            amount_minor=20_000_000,
            idempotency_key="top-up-wallet-test-0001",
            platform_staff=self.staff,
            reason="Synthetic reviewed top up",
            retry_invoices=False,
        )
        self.wallet.refresh_from_db()
        self.assertEqual(first.id, replay.id)
        self.assertEqual(self.wallet.available_balance_minor, 20_000_000)
        self.assertEqual(self.wallet.transactions.count(), 1)
        first.reason = "edited"
        with self.assertRaisesMessage(ValidationError, "immutable"):
            first.save()
        with self.assertRaisesMessage(ValidationError, "immutable"):
            first.delete()

    def test_idempotency_conflict_and_cross_tenant_staff_rules(self):
        credit_wallet(
            self.wallet,
            amount_minor=100,
            idempotency_key="top-up-wallet-conflict",
            platform_staff=self.staff,
            reason="Synthetic reviewed top up",
            retry_invoices=False,
        )
        with self.assertRaisesMessage(BillingError, "different wallet operation"):
            credit_wallet(
                self.wallet,
                amount_minor=200,
                idempotency_key="top-up-wallet-conflict",
                platform_staff=self.staff,
                reason="Synthetic reviewed top up",
                retry_invoices=False,
            )
        support_user = User.objects.create_user(username="wallet-support", password=TEST_PASSWORD)
        support = PlatformStaffAccess.objects.create(
            user=support_user,
            role=PlatformRole.SUPPORT,
            status=PlatformAccessStatus.ACTIVE,
        )
        with self.assertRaisesMessage(BillingError, "platform owner or platform admin"):
            credit_wallet(
                self.wallet,
                amount_minor=100,
                idempotency_key="support-top-up-denied",
                platform_staff=support,
                reason="Support must not top up",
                retry_invoices=False,
            )

    def test_invoice_is_paid_atomically_once_and_never_partially(self):
        invoice = self._invoice()
        credit_wallet(
            self.wallet,
            amount_minor=invoice.amount_due_minor - 1,
            idempotency_key="wallet-insufficient-credit",
            platform_staff=self.staff,
            reason="Synthetic insufficient balance",
            retry_invoices=False,
        )
        result = pay_invoice_from_wallet(invoice)
        self.assertFalse(result.paid)
        invoice.refresh_from_db()
        self.wallet.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(invoice.amount_paid_minor, 0)
        self.assertEqual(self.wallet.available_balance_minor, invoice.total_minor - 1)
        self.assertEqual(self.subscription.status, Subscription.Status.GRACE)
        self.assertEqual(self.wallet.transactions.filter(direction="debit").count(), 0)
        self.assertEqual(result.payment_attempt.failure_code, "insufficient_wallet_balance")

        credit_wallet(
            self.wallet,
            amount_minor=1,
            idempotency_key="wallet-recovery-credit",
            platform_staff=self.staff,
            reason="Synthetic recovery balance",
            retry_invoices=False,
        )
        paid = retry_due_invoices(self.organization.id, currency="UZS")
        self.assertEqual(len(paid), 1)
        self.assertTrue(paid[0].paid)
        replay = pay_invoice_from_wallet(invoice)
        self.assertTrue(replay.paid)
        invoice.refresh_from_db()
        self.wallet.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(self.wallet.available_balance_minor, 0)
        self.assertEqual(
            WalletTransaction.objects.filter(
                invoice=invoice,
                transaction_type=WalletTransaction.TransactionType.SUBSCRIPTION_PAYMENT,
            ).count(),
            1,
        )
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)

    def test_top_up_retries_only_due_invoices_on_commit(self):
        due = self._invoice(due=True)
        self.subscription.current_period_start = self.subscription.current_period_end
        self.subscription.current_period_end += timedelta(days=30)
        self.subscription.save(update_fields=["current_period_start", "current_period_end", "updated_at"])
        future = self._invoice(due=False)
        with self.captureOnCommitCallbacks(execute=True):
            credit_wallet(
                self.wallet,
                amount_minor=due.total_minor + future.total_minor,
                idempotency_key="wallet-auto-retry-credit",
                platform_staff=self.staff,
                reason="Synthetic automatic invoice retry",
            )
        due.refresh_from_db()
        future.refresh_from_db()
        self.assertEqual(due.status, Invoice.Status.PAID)
        self.assertEqual(future.status, Invoice.Status.OPEN)

    def test_adjustment_reversal_and_reconciliation_preserve_nonnegative_balance(self):
        credit = credit_wallet(
            self.wallet,
            amount_minor=1000,
            idempotency_key="wallet-adjustment-credit",
            platform_staff=self.staff,
            reason="Synthetic adjustment opening",
            retry_invoices=False,
        )
        debit_adjustment(
            self.wallet,
            amount_minor=400,
            idempotency_key="wallet-adjustment-debit",
            platform_staff=self.staff,
            reason="Synthetic reviewed adjustment",
        )
        with self.assertRaisesMessage(BillingError, "insufficient balance"):
            reverse_transaction(
                credit,
                idempotency_key="wallet-credit-reversal-insufficient",
                platform_staff=self.staff,
                reason="Synthetic reversal safety check",
            )
        debit = self.wallet.transactions.get(idempotency_key="wallet-adjustment-debit")
        reversal = reverse_transaction(
            debit,
            idempotency_key="wallet-debit-reversal",
            platform_staff=self.staff,
            reason="Synthetic reviewed reversal",
        )
        self.assertEqual(reversal.direction, WalletTransaction.Direction.CREDIT)
        run = reconcile_wallet(self.wallet, platform_staff=self.staff)
        self.assertEqual(run.status, "matched")
        self.assertEqual(run.difference_minor, 0)

    def test_payment_reversal_reopens_invoice_and_duplicate_reversal_is_idempotent(self):
        invoice = self._invoice()
        credit_wallet(
            self.wallet,
            amount_minor=invoice.total_minor,
            idempotency_key="wallet-payment-reversal-credit",
            platform_staff=self.staff,
            reason="Synthetic payment reversal opening",
            retry_invoices=False,
        )
        paid = pay_invoice_from_wallet(invoice)
        reversal = reverse_transaction(
            paid.transaction,
            idempotency_key="wallet-payment-reversal-entry",
            platform_staff=self.staff,
            reason="Synthetic subscription payment reversal",
        )
        replay = reverse_transaction(
            paid.transaction,
            idempotency_key="wallet-payment-reversal-entry",
            platform_staff=self.staff,
            reason="Synthetic subscription payment reversal",
        )
        self.assertEqual(reversal.id, replay.id)
        invoice.refresh_from_db()
        self.subscription.refresh_from_db()
        paid.payment_attempt.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.OPEN)
        self.assertEqual(invoice.amount_paid_minor, 0)
        self.assertEqual(paid.payment_attempt.status, PaymentAttempt.Status.REFUNDED)
        self.assertEqual(self.subscription.status, Subscription.Status.GRACE)
        with self.assertRaisesMessage(BillingError, "already reversed"):
            reverse_transaction(
                paid.transaction,
                idempotency_key="wallet-payment-other-reversal",
                platform_staff=self.staff,
                reason="Synthetic duplicate reversal rejection",
            )

    def test_frozen_closed_validation_and_safe_metadata_paths(self):
        self.assertEqual(safe_wallet_metadata(None), {})
        self.assertEqual(
            safe_wallet_metadata({"source": "cash", "password": "hidden", "note": None}),
            {"source": "cash"},
        )
        with self.assertRaisesMessage(BillingError, "ISO 4217"):
            ensure_wallet(self.organization, "bad-currency")
        with self.assertRaisesMessage(BillingError, "positive integer"):
            credit_wallet(
                self.wallet,
                amount_minor="not-a-number",
                idempotency_key="wallet-invalid-amount",
                platform_staff=self.staff,
                reason="Synthetic validation check",
                retry_invoices=False,
            )
        with override_settings(WALLET_TOP_UP_TWO_PERSON_THRESHOLD_MINOR="1"):
            with self.assertRaisesMessage(BillingError, "single-operator"):
                credit_wallet(
                    self.wallet,
                    amount_minor=1,
                    idempotency_key="wallet-two-person-threshold",
                    platform_staff=self.staff,
                    reason="Synthetic two-person threshold",
                    retry_invoices=False,
                )
        set_wallet_frozen(
            self.wallet,
            frozen=True,
            platform_staff=self.staff,
            reason="Synthetic wallet freeze",
        )
        invoice = self._invoice()
        with self.assertRaisesMessage(BillingError, "not active"):
            pay_invoice_from_wallet(invoice)
        with self.assertRaisesMessage(BillingError, "active wallet"):
            debit_adjustment(
                self.wallet,
                amount_minor=1,
                idempotency_key="wallet-frozen-debit",
                platform_staff=self.staff,
                reason="Synthetic frozen debit check",
            )
        set_wallet_frozen(
            self.wallet,
            frozen=False,
            platform_staff=self.staff,
            reason="Synthetic wallet unfreeze",
        )
        self.wallet.refresh_from_db()
        self.wallet.status = OrganizationWallet.Status.CLOSED
        self.wallet.save(update_fields=["status", "updated_at"])
        with self.assertRaisesMessage(BillingError, "closed wallet"):
            credit_wallet(
                self.wallet,
                amount_minor=1,
                idempotency_key="wallet-closed-credit",
                platform_staff=self.staff,
                reason="Synthetic closed credit check",
                retry_invoices=False,
            )
        with self.assertRaisesMessage(BillingError, "closed wallet"):
            set_wallet_frozen(
                self.wallet,
                frozen=False,
                platform_staff=self.staff,
                reason="Synthetic closed status check",
            )

    def test_payment_source_amount_retry_and_renewal_validation_paths(self):
        invoice = self._invoice()
        first = pay_invoice_from_wallet(invoice)
        second = pay_invoice_from_wallet(invoice)
        self.assertFalse(first.paid)
        self.assertFalse(second.paid)
        self.assertEqual(invoice.payment_attempts.count(), 1)
        self.subscription.payment_source = Subscription.PaymentSource.MANUAL
        self.subscription.save(update_fields=["payment_source", "updated_at"])
        other_invoice = generate_invoice(self.subscription)
        issue_invoice(other_invoice)
        with self.assertRaisesMessage(BillingError, "not configured"):
            pay_invoice_from_wallet(other_invoice)
        with self.assertRaisesMessage(BillingError, "does not use wallet"):
            process_wallet_renewal(self.subscription)
        self.subscription.payment_source = Subscription.PaymentSource.WALLET
        self.subscription.current_period_start = timezone.now() - timedelta(days=31)
        self.subscription.current_period_end = timezone.now() - timedelta(days=1)
        self.subscription.status = Subscription.Status.ACTIVE
        self.subscription.save(
            update_fields=[
                "payment_source",
                "current_period_start",
                "current_period_end",
                "status",
                "updated_at",
            ]
        )
        credit_wallet(
            self.wallet,
            amount_minor=self.subscription.price.amount_minor,
            idempotency_key="wallet-renewal-credit",
            platform_staff=self.staff,
            reason="Synthetic renewal credit",
            retry_invoices=False,
        )
        renewed = process_wallet_renewal(self.subscription)
        self.assertTrue(renewed.paid)
        self.subscription.refresh_from_db()
        self.assertGreater(self.subscription.current_period_end, timezone.now())
        with override_settings(WALLET_RECONCILIATION_ENABLE=False):
            with self.assertRaisesMessage(BillingError, "disabled"):
                reconcile_wallet(self.wallet)

    def test_backfill_handles_organization_without_billing_account(self):
        organization = Organization.objects.create(name="No Billing", slug="no-billing")
        self.assertEqual(backfill_wallets([organization]), 1)
        self.assertEqual(backfill_wallets([organization]), 0)


@override_settings(
    BILLING_ENABLE=True,
    BILLING_PROVIDER="fake",
    BILLING_DEFAULT_PAYMENT_SOURCE="wallet",
    BILLING_DEFAULT_PLAN_KEY="starter",
    BILLING_DEFAULT_CURRENCY="UZS",
    BILLING_TRIAL_DAYS=0,
    BILLING_INVOICE_PREFIX="WALLETAPI",
    DEBUG=True,
    CONTROL_PLANE_ENABLE=True,
    CONTROL_PLANE_FAKE_MFA=True,
    CONTROL_PLANE_MFA_REQUIRED=True,
    CONTROL_PLANE_COOKIE_NAME="wallet-internal-session",
    CONTROL_PLANE_ALLOWED_IPS=[],
    FIELD_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY,
)
class WalletAPITests(TestCase):
    password = TEST_PASSWORD

    def setUp(self):
        self.customer = User.objects.create_user(
            username="wallet-api-customer", email="wallet-api-customer@example.test", password=self.password
        )
        self.organization = create_organization(
            creator=self.customer, name="Wallet API Tenant", slug="wallet-api-tenant"
        )
        _, self.subscription, _ = ensure_billing_for_organization(self.organization)
        self.wallet = self.organization.wallets.get()
        self.customer_client = APIClient()
        self.customer_client.force_authenticate(self.customer)
        self.customer_headers = {"HTTP_X_ORGANIZATION_ID": str(self.organization.id)}
        self.owner_client, self.owner_access = self._staff_client("wallet-api-owner", PlatformRole.OWNER, verify=True)
        self.support_client, _ = self._staff_client("wallet-api-support", PlatformRole.SUPPORT, verify=True)
        self.stale_admin_client, _ = self._staff_client("wallet-api-admin", PlatformRole.ADMIN, verify=False)

    def _staff_client(self, name, role, *, verify):
        user = User.objects.create_user(
            username=f"{name}@example.test", email=f"{name}@example.test", password=self.password
        )
        access = PlatformStaffAccess.objects.create(user=user, role=role, status=PlatformAccessStatus.ACTIVE)
        PlatformMFADevice.objects.create(
            access=access,
            secret_encrypted=base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("="),
            recovery_code_hashes=[],
            enabled=True,
            confirmed_at=timezone.now(),
        )
        client = APIClient(enforce_csrf_checks=True)
        csrf = client.get("/api/v1/internal/auth/csrf/").data["csrftoken"]
        login = client.post(
            "/api/v1/internal/auth/login/",
            {"email": user.email, "password": self.password},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(login.status_code, 200)
        if verify:
            response = client.post(
                "/api/v1/internal/auth/mfa/verify/",
                {"code": "000000"},
                format="json",
                HTTP_X_CSRFTOKEN=csrf,
            )
            self.assertEqual(response.status_code, 200)
        client.defaults["HTTP_X_CSRFTOKEN"] = csrf
        return client, access

    def test_customer_wallet_is_read_only_and_tenant_scoped(self):
        response = self.customer_client.get("/api/v1/billing/wallet/", **self.customer_headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["wallet"]["organization"], str(self.organization.id))
        self.assertEqual(
            self.customer_client.post(
                "/api/v1/billing/wallet/",
                {"available_balance_minor": 999999},
                format="json",
                **self.customer_headers,
            ).status_code,
            405,
        )
        self.assertNotIn("reason", str(response.data))

    def test_internal_top_up_requires_role_recent_mfa_reason_and_idempotency(self):
        path = f"/api/v1/internal/billing/wallets/{self.wallet.id}/top-up/"
        payload = {"amount_minor": 1000, "reason": "Synthetic internal wallet top up"}
        self.assertEqual(self.support_client.post(path, payload, format="json").status_code, 403)
        self.assertEqual(self.stale_admin_client.post(path, payload, format="json").status_code, 403)
        missing_key = self.owner_client.post(path, payload, format="json")
        self.assertEqual(missing_key.status_code, 422)
        created = self.owner_client.post(
            path,
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="wallet-api-top-up-0001",
        )
        replay = self.owner_client.post(
            path,
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="wallet-api-top-up-0001",
        )
        self.assertEqual((created.status_code, replay.status_code), (201, 201))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance_minor, 1000)
        self.assertEqual(self.wallet.transactions.count(), 1)
        self.assertTrue(PlatformAuditEvent.objects.filter(action="wallet.credit").exists())


@override_settings(
    BILLING_ENABLE=True,
    BILLING_PROVIDER="fake",
    BILLING_DEFAULT_PAYMENT_SOURCE="wallet",
    BILLING_DEFAULT_PLAN_KEY="starter",
    BILLING_DEFAULT_CURRENCY="UZS",
    BILLING_TRIAL_DAYS=0,
    BILLING_INVOICE_PREFIX="WALLETPG",
    DEBUG=True,
)
class WalletPostgresConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock concurrency test")
        customer = User.objects.create_user(username="wallet-pg-customer", password=TEST_PASSWORD)
        self.organization = create_organization(creator=customer, name="Wallet PG", slug="wallet-pg")
        _, self.subscription, _ = ensure_billing_for_organization(self.organization)
        self.invoice = issue_invoice(generate_invoice(self.subscription, due_days=0))
        staff_user = User.objects.create_user(username="wallet-pg-staff", password=TEST_PASSWORD)
        staff = PlatformStaffAccess.objects.create(
            user=staff_user, role=PlatformRole.OWNER, status=PlatformAccessStatus.ACTIVE
        )
        self.staff_id = staff.id
        self.wallet_id = self.organization.wallets.get().id
        credit_wallet(
            self.organization.wallets.get(),
            amount_minor=self.invoice.total_minor,
            idempotency_key="wallet-pg-opening-credit",
            platform_staff=staff,
            reason="Synthetic PostgreSQL concurrency credit",
            retry_invoices=False,
        )

    def test_concurrent_workers_debit_invoice_once(self):
        invoice_id = self.invoice.id

        def worker():
            close_old_connections()
            result = pay_invoice_from_wallet(Invoice.objects.get(pk=invoice_id))
            connection.close()
            return result.paid

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: worker(), range(2)))
        self.assertEqual(outcomes, [True, True])
        self.assertEqual(
            WalletTransaction.objects.filter(
                invoice_id=invoice_id,
                transaction_type=WalletTransaction.TransactionType.SUBSCRIPTION_PAYMENT,
            ).count(),
            1,
        )
        self.assertEqual(PaymentAttempt.objects.filter(invoice_id=invoice_id).count(), 1)
        wallet = self.organization.wallets.get()
        self.assertEqual(wallet.available_balance_minor, 0)

    def test_concurrent_credits_are_both_preserved(self):
        wallet_id = self.wallet_id
        staff_id = self.staff_id

        def worker(index):
            close_old_connections()
            entry = credit_wallet(
                OrganizationWallet.objects.get(pk=wallet_id),
                amount_minor=100,
                idempotency_key=f"wallet-pg-concurrent-credit-{index}",
                platform_staff=PlatformStaffAccess.objects.get(pk=staff_id),
                reason="Synthetic concurrent PostgreSQL credit",
                retry_invoices=False,
            )
            connection.close()
            return entry.id

        with ThreadPoolExecutor(max_workers=2) as pool:
            entries = list(pool.map(worker, range(2)))
        self.assertEqual(len(set(entries)), 2)
        wallet = OrganizationWallet.objects.get(pk=wallet_id)
        self.assertEqual(wallet.available_balance_minor, self.invoice.total_minor + 200)

    def test_concurrent_bootstrap_creates_one_isolated_platform_owner(self):
        owner_email = "wallet-pg-bootstrap@example.test"

        def worker():
            close_old_connections()
            report = bootstrap_platform(
                owner_email=owner_email,
                owner_password=TEST_PASSWORD,
            )
            connection.close()
            return report["created_owner"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: worker(), range(2)))

        self.assertEqual(sum(outcomes), 1)
        owner = User.objects.get(email=owner_email)
        self.assertFalse(owner.is_staff)
        self.assertFalse(owner.is_superuser)
        self.assertEqual(owner.platform_access.role, PlatformRole.OWNER)
        self.assertTrue(owner.platform_access.mfa_required)
        self.assertEqual(PlatformStaffAccess.objects.filter(user=owner).count(), 1)


@override_settings(
    BILLING_ENABLE=True,
    BILLING_PROVIDER="manual",
    BILLING_DEFAULT_CURRENCY="UZS",
    DEBUG=True,
)
class PlatformBootstrapTests(TestCase):
    def test_bootstrap_owner_is_isolated_non_superuser_idempotent_and_has_no_mfa_secret(self):
        password = TEST_PASSWORD
        first = bootstrap_platform(
            owner_email="bootstrap-owner@example.test",
            owner_password=password,
            create_wallets=True,
        )
        second = bootstrap_platform(
            owner_email="bootstrap-owner@example.test",
            owner_password="",
            create_wallets=True,
        )
        user = User.objects.get(email="bootstrap-owner@example.test")
        access = user.platform_access
        self.assertTrue(first["created_owner"])
        self.assertFalse(second["created_owner"])
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)
        self.assertTrue(user.must_change_password)
        self.assertEqual(access.role, PlatformRole.OWNER)
        self.assertTrue(access.mfa_required)
        self.assertFalse(PlatformMFADevice.objects.filter(access=access).exists())

    def test_production_rejects_full_demo_seed_without_echoing_password(self):
        output = StringIO()
        with override_settings(DEPLOYMENT_ENVIRONMENT="production"):
            with self.assertRaisesMessage(CommandError, "forbidden in production"):
                call_command("seed_full_demo", stdout=output)
        self.assertEqual(output.getvalue(), "")

    def test_explicit_adoption_rotation_wallet_backfill_and_payment_source_migration(self):
        existing = User.objects.create_user(
            username="adopt-owner@example.test",
            email="adopt-owner@example.test",
            password=TEST_PASSWORD,
        )
        with self.assertRaisesMessage(BootstrapError, "adopt-existing-owner"):
            bootstrap_platform(owner_email=existing.email)
        organization = Organization.objects.create(name="Bootstrap Wallet", slug="bootstrap-wallet")
        _, subscription, _ = ensure_billing_for_organization(organization)
        subscription.payment_source = Subscription.PaymentSource.MANUAL
        subscription.save(update_fields=["payment_source", "updated_at"])
        organization.wallets.all().delete()
        first = bootstrap_platform(
            owner_email=existing.email,
            adopt_existing_owner=True,
            create_wallets=True,
        )
        self.assertGreaterEqual(first["wallets_created"], 1)
        self.assertTrue(organization.wallets.filter(currency="UZS").exists())
        self.assertTrue(hasattr(existing, "platform_access"))
        old_hash = User.objects.get(pk=existing.pk).password
        bootstrap_platform(owner_email=existing.email)
        self.assertEqual(User.objects.get(pk=existing.pk).password, old_hash)
        rotated = bootstrap_platform(
            owner_email=existing.email,
            owner_password="test-only-rotated-bootstrap-password-92!",
            rotate_owner_password=True,
            migrate_subscriptions_to_wallet=True,
        )
        self.assertTrue(rotated["rotated_password"])
        subscription.refresh_from_db()
        self.assertEqual(subscription.payment_source, Subscription.PaymentSource.WALLET)
        self.assertTrue(User.objects.get(pk=existing.pk).check_password("test-only-rotated-bootstrap-password-92!"))

    def test_bootstrap_rejects_short_password_and_non_owner_platform_access(self):
        with self.assertRaisesMessage(BootstrapError, "at least 12"):
            bootstrap_platform(owner_email="short-owner@example.test", owner_password="short")
        user = User.objects.create_user(
            username="platform-admin-existing@example.test",
            email="platform-admin-existing@example.test",
            password=TEST_PASSWORD,
        )
        PlatformStaffAccess.objects.create(
            user=user,
            role=PlatformRole.ADMIN,
            status=PlatformAccessStatus.ACTIVE,
        )
        with self.assertRaisesMessage(BootstrapError, "not a platform owner"):
            bootstrap_platform(owner_email=user.email)

    def test_bootstrap_repairs_owner_security_flags_and_safe_report(self):
        user = User.objects.create_user(
            username="repair-owner@example.test",
            email="repair-owner@example.test",
            password=TEST_PASSWORD,
        )
        User.objects.filter(pk=user.pk).update(is_staff=True, is_superuser=True)
        access = PlatformStaffAccess.objects.create(
            user=user,
            role=PlatformRole.OWNER,
            status=PlatformAccessStatus.SUSPENDED,
            mfa_required=False,
        )

        report = bootstrap_platform(owner_email=user.email)

        user.refresh_from_db()
        access.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(access.status, PlatformAccessStatus.ACTIVE)
        self.assertTrue(access.mfa_required)
        self.assertNotIn(TEST_PASSWORD, safe_report_json(report))
        with self.assertRaisesMessage(BootstrapError, "at least 12"):
            bootstrap_platform(
                owner_email=user.email,
                owner_password="short",
                rotate_owner_password=True,
            )
