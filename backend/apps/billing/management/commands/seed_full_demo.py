from __future__ import annotations

import argparse
import json
import os
from io import StringIO

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import router, transaction
from django.db.models.deletion import CASCADE, PROTECT, RESTRICT, Collector
from django.test.utils import override_settings

from billing.models import Invoice, Subscription, WalletTransaction
from billing.services import generate_invoice, issue_invoice
from billing.wallet import credit_wallet, ensure_wallet, pay_invoice_from_wallet
from channels.models import ChannelConnection, ChannelStatus, ChannelType
from control_plane.models import PlatformAccessStatus, PlatformRole, PlatformStaffAccess
from organizations.models import Organization


def _delete_demo_organization(organization: Organization) -> int:
    """Delete one explicitly confirmed demo tenant, including protected tenant children."""
    using = router.db_for_write(Organization)
    protected_fields = []
    for model in apps.get_models():
        for field in model._meta.fields:
            remote = getattr(field, "remote_field", None)
            if remote and remote.on_delete in {PROTECT, RESTRICT}:
                protected_fields.append((remote, remote.on_delete))
                remote.on_delete = CASCADE
    try:
        collector = Collector(using=using)
        with transaction.atomic(using=using):
            collector.collect([organization], fail_on_restricted=False)
            deleted, _ = collector.delete()
        return deleted
    finally:
        for remote, on_delete in protected_fields:
            remote.on_delete = on_delete


class Command(BaseCommand):
    help = "Seed deterministic full-platform demo data for development, staging, or E2E only."

    def add_arguments(self, parser):
        parser.add_argument("--organization-slug", default="mehr-clinic")
        parser.add_argument(
            "--with-admin",
            action=argparse.BooleanOptionalAction,
            default=True,
        )
        parser.add_argument(
            "--with-wallet",
            action=argparse.BooleanOptionalAction,
            default=True,
        )
        parser.add_argument("--reset", action="store_true")
        parser.add_argument("--non-interactive", action="store_true")
        parser.add_argument("--safe-json-report", action="store_true")

    def handle(self, *args, **options):
        environment = getattr(settings, "DEPLOYMENT_ENVIRONMENT", "production")
        if environment not in {"development", "staging", "test"}:
            raise CommandError("seed_full_demo is forbidden in production.")
        organization_slug = options["organization_slug"].strip()
        if not organization_slug:
            raise CommandError("--organization-slug cannot be empty.")
        if options["reset"]:
            if environment not in {"development", "staging"}:
                raise CommandError("Demo reset is forbidden outside development or staging.")
            if options["non_interactive"]:
                confirmation = os.environ.get("FULL_DEMO_SEED_RESET_CONFIRMATION", "")
            else:
                confirmation = input(
                    f"Type the demo organization slug '{organization_slug}' to reset only that organization: "
                )
            if confirmation != organization_slug:
                raise CommandError(
                    "Demo reset confirmation did not match --organization-slug; nothing was deleted."
                )
            organization = Organization.objects.filter(slug=organization_slug).first()
            if organization:
                _delete_demo_organization(organization)
        password = os.environ.get("FULL_DEMO_SEED_PASSWORD", "")
        if len(password) < 12:
            raise CommandError("FULL_DEMO_SEED_PASSWORD with at least 12 characters is required.")
        os.environ.setdefault("CLIENT_PORTAL_SEED_PASSWORD", password)
        os.environ.setdefault("CONTROL_PLANE_SEED_PASSWORD", password)
        command_output = StringIO() if options["safe_json_report"] else self.stdout

        def seed(command, *command_args, **command_options):
            call_command(
                command,
                *command_args,
                stdout=command_output,
                **command_options,
            )

        with override_settings(
            DEBUG=True,
            TESTING=True,
            ENABLE_CRM_TEST_CHANNEL=True,
            BILLING_ENABLE=True,
            BILLING_PROVIDER="fake",
            BILLING_FAKE_PROVIDER=True,
            BILLING_DEFAULT_PLAN_KEY="starter",
            BILLING_DEFAULT_PAYMENT_SOURCE="wallet",
            BILLING_TRIAL_DAYS=14,
        ):
            seed("seed_client_portal")
            if options["with_admin"] or options["with_wallet"]:
                # Wallet credits deliberately require a real, audited internal
                # owner/admin actor, so wallet demo data implies Admin fixtures.
                seed("seed_control_plane_demo")
            seed("seed_crm")
            seed("seed_web_chat_demo", organization=organization_slug)
            seed("seed_billing_demo")
            seed("seed_booking_demo")

        try:
            organization = Organization.objects.get(slug=organization_slug)
        except Organization.DoesNotExist as exc:
            raise CommandError(
                f"The deterministic seed did not create organization '{organization_slug}'."
            ) from exc
        for channel_type, provider in (
            (ChannelType.GMAIL, "fake"),
            (ChannelType.TELEGRAM, "fake"),
            (ChannelType.INSTAGRAM, "fake"),
            (ChannelType.SMS, "fake"),
            (ChannelType.VOICE, "fake"),
        ):
            ChannelConnection.objects.update_or_create(
                organization=organization,
                provider=provider,
                type=channel_type,
                external_identifier=f"demo:{channel_type}:{organization.id}",
                defaults={
                    "display_name": f"Deterministic {channel_type} demo",
                    "status": ChannelStatus.DRAFT,
                    "configuration": {"demo": True, "network": False},
                },
            )

        subscription = Subscription.objects.get(
            organization=organization,
            status__in=Subscription.EFFECTIVE_STATUSES,
        )
        wallet = ensure_wallet(organization, subscription.price.currency)
        if options["with_wallet"]:
            subscription.payment_source = Subscription.PaymentSource.WALLET
            subscription.save(update_fields=["payment_source", "updated_at"])
            owner = PlatformStaffAccess.objects.filter(
                role=PlatformRole.OWNER,
                status=PlatformAccessStatus.ACTIVE,
            ).first()
            if not owner:
                raise CommandError("The demo platform owner was not created.")
            credit_wallet(
                wallet,
                amount_minor=50_000_000,
                idempotency_key=f"full-demo:{organization.id}:initial-credit:v1",
                platform_staff=owner,
                reason="Deterministic development wallet opening credit",
                transaction_type=WalletTransaction.TransactionType.MIGRATION_CREDIT,
                payment_method="development_seed",
                safe_metadata={"source": "seed_full_demo", "migration": "v1"},
                retry_invoices=False,
            )
            demo_payment_exists = WalletTransaction.objects.filter(
                organization=organization,
                transaction_type=WalletTransaction.TransactionType.SUBSCRIPTION_PAYMENT,
                status=WalletTransaction.Status.COMPLETED,
            ).exists()
            if not demo_payment_exists:
                invoice = Invoice.objects.filter(
                    organization=organization,
                    status__in=[Invoice.Status.DRAFT, Invoice.Status.OPEN],
                    total_minor__gt=0,
                    amount_due_minor__gt=0,
                ).order_by("created_at").first()
                if not invoice:
                    invoice = generate_invoice(subscription, due_days=0)
                if invoice.status == Invoice.Status.DRAFT:
                    invoice = issue_invoice(invoice)
                pay_invoice_from_wallet(invoice)
            if not Invoice.objects.filter(
                organization=organization,
                status=Invoice.Status.OPEN,
                total_minor__gt=0,
                amount_due_minor__gt=0,
            ).exists():
                issue_invoice(generate_invoice(subscription))

        wallet.refresh_from_db()
        report = {
            "status": "ready",
            "environment": environment,
            "organization_slug": organization.slug,
            "with_admin": bool(options["with_admin"] or options["with_wallet"]),
            "with_wallet": bool(options["with_wallet"]),
            "wallet_currency": wallet.currency,
            "wallet_balance_minor": wallet.available_balance_minor,
            "invoice_counts": {
                status: Invoice.objects.filter(organization=organization, status=status).count()
                for status in (Invoice.Status.DRAFT, Invoice.Status.OPEN, Invoice.Status.PAID)
            },
            "login_emails": [
                "owner@portal.test",
                *(["platform-owner@example.test"] if options["with_admin"] or options["with_wallet"] else []),
            ],
            "urls": ["/en/login", "/en/app/billing/wallet", "/en/app/billing/wallets"],
        }
        if options["safe_json_report"]:
            self.stdout.write(json.dumps(report, sort_keys=True))
        else:
            self.stdout.write(self.style.SUCCESS("Deterministic full demo seeded without external credentials."))
