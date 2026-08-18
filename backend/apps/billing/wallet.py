from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from billing.models import (
    Invoice,
    OrganizationWallet,
    PaymentAttempt,
    Subscription,
    WalletReconciliationRun,
    WalletTransaction,
)
from billing.services import (
    BillingError,
    _period_end,
    generate_invoice,
    issue_invoice,
    notify,
    sync_entitlement,
)
from control_plane.models import (
    PlatformAccessStatus,
    PlatformRole,
    PlatformStaffAccess,
)
from control_plane.services import record_audit
from organizations.models import Organization


SAFE_METADATA_KEYS = {
    "source",
    "channel",
    "migration",
    "invoice_number",
    "request_id",
    "note",
}
MUTATING_ROLES = {
    PlatformRole.OWNER,
    PlatformRole.ADMIN,
}


@dataclass(frozen=True)
class WalletPaymentResult:
    invoice: Invoice
    payment_attempt: PaymentAttempt
    transaction: WalletTransaction | None
    paid: bool
    required_minor: int
    available_minor: int


def safe_wallet_metadata(value: dict | None) -> dict:
    if not isinstance(value, dict):
        return {}
    return {
        str(key)[:80]: str(raw)[:300]
        for key, raw in value.items()
        if str(key) in SAFE_METADATA_KEYS and raw is not None
    }


def _currency(value: str) -> str:
    clean = str(value or "").upper()
    if len(clean) != 3 or not clean.isalpha():
        raise BillingError("wallet_currency_invalid", "Use a three-letter ISO 4217 currency code.", status_code=422)
    return clean


def _positive_amount(value: int) -> int:
    try:
        amount = int(value)
    except (TypeError, ValueError) as exc:
        raise BillingError("wallet_amount_invalid", "Wallet amount must be a positive integer.", status_code=422) from exc
    if amount <= 0:
        raise BillingError("wallet_amount_invalid", "Wallet amount must be a positive integer.", status_code=422)
    return amount


def _idempotency_key(value: str) -> str:
    clean = str(value or "").strip()
    if len(clean) < 8:
        raise BillingError("idempotency_key_required", "A stable idempotency key is required.", status_code=422)
    return clean[:200]


def _require_platform_mutator(staff: PlatformStaffAccess | None) -> PlatformStaffAccess:
    if not staff or staff.status != PlatformAccessStatus.ACTIVE or staff.role not in MUTATING_ROLES:
        raise BillingError(
            "wallet_platform_permission_denied",
            "Only an active platform owner or platform admin may change a wallet.",
            status_code=403,
        )
    return staff


def ensure_wallet(organization: Organization, currency: str | None = None) -> OrganizationWallet:
    currency = _currency(currency or getattr(settings, "BILLING_DEFAULT_CURRENCY", "UZS"))
    wallet, _ = OrganizationWallet.objects.get_or_create(
        organization=organization,
        currency=currency,
    )
    return wallet


def _existing_transaction(wallet: OrganizationWallet, key: str) -> WalletTransaction | None:
    return WalletTransaction.objects.filter(wallet=wallet, idempotency_key=key).first()


def _append_locked(
    wallet: OrganizationWallet,
    *,
    direction: str,
    transaction_type: str,
    amount_minor: int,
    idempotency_key: str,
    platform_staff: PlatformStaffAccess | None = None,
    invoice: Invoice | None = None,
    payment_attempt: PaymentAttempt | None = None,
    reverses_transaction: WalletTransaction | None = None,
    payment_method: str = "",
    external_reference: str = "",
    reason: str = "",
    safe_metadata: dict | None = None,
) -> WalletTransaction:
    amount = _positive_amount(amount_minor)
    key = _idempotency_key(idempotency_key)
    existing = _existing_transaction(wallet, key)
    if existing:
        if (
            existing.direction != direction
            or existing.transaction_type != transaction_type
            or existing.amount_minor != amount
            or existing.invoice_id != (invoice.id if invoice else None)
        ):
            raise BillingError(
                "wallet_idempotency_conflict",
                "The idempotency key was already used for a different wallet operation.",
            )
        return existing

    delta = amount if direction == WalletTransaction.Direction.CREDIT else -amount
    balance_after = wallet.available_balance_minor + delta
    if balance_after < 0:
        raise BillingError(
            "wallet_insufficient_balance",
            "The organization wallet has insufficient balance.",
            status_code=409,
            details={
                "required_minor": amount,
                "available_minor": wallet.available_balance_minor,
                "currency": wallet.currency,
            },
        )
    wallet.available_balance_minor = balance_after
    wallet.ledger_version += 1
    wallet.save(update_fields=["available_balance_minor", "ledger_version", "updated_at"])
    return WalletTransaction.objects.create(
        organization=wallet.organization,
        wallet=wallet,
        direction=direction,
        transaction_type=transaction_type,
        amount_minor=amount,
        currency=wallet.currency,
        status=WalletTransaction.Status.COMPLETED,
        idempotency_key=key,
        invoice=invoice,
        payment_attempt=payment_attempt,
        reverses_transaction=reverses_transaction,
        payment_method=str(payment_method or "")[:40],
        external_reference=str(external_reference or "")[:160],
        reason=str(reason or "")[:500],
        safe_metadata=safe_wallet_metadata(safe_metadata),
        performed_by_platform_staff=platform_staff,
        balance_after_minor=balance_after,
        ledger_version=wallet.ledger_version,
    )


@transaction.atomic
def credit_wallet(
    wallet: OrganizationWallet,
    *,
    amount_minor: int,
    idempotency_key: str,
    platform_staff: PlatformStaffAccess,
    reason: str,
    request=None,
    transaction_type: str = WalletTransaction.TransactionType.TOP_UP,
    payment_method: str = "manual",
    external_reference: str = "",
    safe_metadata: dict | None = None,
    retry_invoices: bool = True,
) -> WalletTransaction:
    platform_staff = _require_platform_mutator(platform_staff)
    threshold = str(getattr(settings, "WALLET_TOP_UP_TWO_PERSON_THRESHOLD_MINOR", "") or "").strip()
    if threshold and _positive_amount(amount_minor) >= int(threshold):
        raise BillingError(
            "wallet_two_person_approval_required",
            "This top-up exceeds the configured single-operator limit.",
            status_code=403,
        )
    wallet = OrganizationWallet.objects.select_for_update().select_related("organization").get(pk=wallet.pk)
    if wallet.status == OrganizationWallet.Status.CLOSED:
        raise BillingError("wallet_closed", "A closed wallet cannot be credited.")
    before = wallet.available_balance_minor
    entry = _append_locked(
        wallet,
        direction=WalletTransaction.Direction.CREDIT,
        transaction_type=transaction_type,
        amount_minor=amount_minor,
        idempotency_key=idempotency_key,
        platform_staff=platform_staff,
        payment_method=payment_method,
        external_reference=external_reference,
        reason=reason,
        safe_metadata=safe_metadata,
    )
    if request:
        record_audit(
            request,
            action="wallet.credit",
            target_type="organization_wallet",
            target_id=wallet.id,
            organization=wallet.organization,
            reason=reason,
            before={"balance_minor": before, "currency": wallet.currency},
            after={"balance_minor": entry.balance_after_minor, "transaction_id": entry.id},
        )
    if (
        retry_invoices
        and wallet.status == OrganizationWallet.Status.ACTIVE
        and getattr(settings, "WALLET_AUTO_APPLY_DUE_INVOICES", True)
    ):
        organization_id = wallet.organization_id
        currency = wallet.currency
        transaction.on_commit(lambda: _safe_retry_due_invoices(organization_id, currency=currency))
    return entry


@transaction.atomic
def debit_adjustment(
    wallet: OrganizationWallet,
    *,
    amount_minor: int,
    idempotency_key: str,
    platform_staff: PlatformStaffAccess,
    reason: str,
    request=None,
    safe_metadata: dict | None = None,
) -> WalletTransaction:
    platform_staff = _require_platform_mutator(platform_staff)
    wallet = OrganizationWallet.objects.select_for_update().select_related("organization").get(pk=wallet.pk)
    if wallet.status != OrganizationWallet.Status.ACTIVE:
        raise BillingError("wallet_not_active", "Only an active wallet can be debited.")
    before = wallet.available_balance_minor
    entry = _append_locked(
        wallet,
        direction=WalletTransaction.Direction.DEBIT,
        transaction_type=WalletTransaction.TransactionType.ADJUSTMENT,
        amount_minor=amount_minor,
        idempotency_key=idempotency_key,
        platform_staff=platform_staff,
        reason=reason,
        safe_metadata=safe_metadata,
    )
    if request:
        record_audit(
            request,
            action="wallet.debit_adjustment",
            target_type="organization_wallet",
            target_id=wallet.id,
            organization=wallet.organization,
            reason=reason,
            before={"balance_minor": before, "currency": wallet.currency},
            after={"balance_minor": entry.balance_after_minor, "transaction_id": entry.id},
        )
    return entry


@transaction.atomic
def reverse_transaction(
    entry: WalletTransaction,
    *,
    idempotency_key: str,
    platform_staff: PlatformStaffAccess,
    reason: str,
    request=None,
) -> WalletTransaction:
    platform_staff = _require_platform_mutator(platform_staff)
    original = WalletTransaction.objects.select_related("wallet", "organization").get(pk=entry.pk)
    invoice = None
    if original.invoice_id:
        invoice = Invoice.objects.select_for_update().get(pk=original.invoice_id)
    wallet = OrganizationWallet.objects.select_for_update().select_related("organization").get(pk=original.wallet_id)
    if hasattr(original, "reversal_transaction"):
        existing = original.reversal_transaction
        if existing.idempotency_key == _idempotency_key(idempotency_key):
            return existing
        raise BillingError("wallet_transaction_already_reversed", "This ledger entry was already reversed.")
    direction = (
        WalletTransaction.Direction.DEBIT
        if original.direction == WalletTransaction.Direction.CREDIT
        else WalletTransaction.Direction.CREDIT
    )
    before = wallet.available_balance_minor
    reversal = _append_locked(
        wallet,
        direction=direction,
        transaction_type=WalletTransaction.TransactionType.REVERSAL,
        amount_minor=original.amount_minor,
        idempotency_key=idempotency_key,
        platform_staff=platform_staff,
        reverses_transaction=original,
        reason=reason,
        safe_metadata={"source": "ledger_reversal"},
    )
    if invoice and original.transaction_type == WalletTransaction.TransactionType.SUBSCRIPTION_PAYMENT:
        invoice.status = Invoice.Status.OPEN
        invoice.amount_paid_minor = 0
        invoice.amount_due_minor = invoice.total_minor
        invoice.paid_at = None
        invoice.save(
            update_fields=["status", "amount_paid_minor", "amount_due_minor", "paid_at", "updated_at"]
        )
        if original.payment_attempt_id:
            PaymentAttempt.objects.filter(pk=original.payment_attempt_id).update(
                status=PaymentAttempt.Status.REFUNDED,
                completed_at=timezone.now(),
            )
        subscription = Subscription.objects.select_for_update().get(pk=invoice.subscription_id)
        if subscription.status not in {Subscription.Status.CANCELLED, Subscription.Status.EXPIRED}:
            subscription.status = Subscription.Status.GRACE
            subscription.grace_ends_at = timezone.now() + timedelta(days=settings.BILLING_GRACE_DAYS)
            subscription.save(update_fields=["status", "grace_ends_at", "updated_at"])
            sync_entitlement(subscription)
        notify(
            invoice.organization,
            "wallet_payment_reversed",
            f"invoice:{invoice.id}:wallet-reversed:{reversal.id}",
            {"invoice_number": invoice.invoice_number, "amount_minor": original.amount_minor},
        )
    if request:
        record_audit(
            request,
            action="wallet.reverse",
            target_type="wallet_transaction",
            target_id=original.id,
            organization=wallet.organization,
            reason=reason,
            before={"balance_minor": before, "transaction_id": original.id},
            after={"balance_minor": reversal.balance_after_minor, "reversal_id": reversal.id},
        )
    return reversal


@transaction.atomic
def set_wallet_frozen(
    wallet: OrganizationWallet,
    *,
    frozen: bool,
    platform_staff: PlatformStaffAccess,
    reason: str,
    request=None,
) -> OrganizationWallet:
    platform_staff = _require_platform_mutator(platform_staff)
    wallet = OrganizationWallet.objects.select_for_update().select_related("organization").get(pk=wallet.pk)
    if wallet.status == OrganizationWallet.Status.CLOSED:
        raise BillingError("wallet_closed", "A closed wallet cannot change status.")
    before = wallet.status
    wallet.status = OrganizationWallet.Status.FROZEN if frozen else OrganizationWallet.Status.ACTIVE
    wallet.save(update_fields=["status", "updated_at"])
    if request:
        record_audit(
            request,
            action="wallet.freeze" if frozen else "wallet.unfreeze",
            target_type="organization_wallet",
            target_id=wallet.id,
            organization=wallet.organization,
            reason=reason,
            before={"status": before},
            after={"status": wallet.status},
        )
    return wallet


def _insufficient_attempt(invoice: Invoice, available_minor: int) -> PaymentAttempt:
    provider_id = f"wallet:{invoice.id}"
    attempt = invoice.payment_attempts.filter(
        provider="wallet",
        provider_payment_id=provider_id,
    ).first()
    now = timezone.now()
    if attempt:
        attempt.status = PaymentAttempt.Status.FAILED
        attempt.failure_code = "insufficient_wallet_balance"
        attempt.attempted_at = now
        attempt.completed_at = now
        attempt.save(update_fields=["status", "failure_code", "attempted_at", "completed_at"])
    else:
        attempt = PaymentAttempt.objects.create(
            invoice=invoice,
            provider="wallet",
            provider_payment_id=provider_id,
            status=PaymentAttempt.Status.FAILED,
            amount_minor=invoice.amount_due_minor,
            currency=invoice.currency,
            failure_code="insufficient_wallet_balance",
            attempted_at=now,
            completed_at=now,
        )
    subscription = Subscription.objects.select_for_update().get(pk=invoice.subscription_id)
    if subscription.status not in {
        Subscription.Status.GRACE,
        Subscription.Status.PAUSED,
        Subscription.Status.CANCELLED,
        Subscription.Status.EXPIRED,
    }:
        subscription.status = Subscription.Status.GRACE
        subscription.grace_ends_at = now + timedelta(days=settings.BILLING_GRACE_DAYS)
        subscription.provider_state = {
            **subscription.provider_state,
            "last_failure_code": "insufficient_wallet_balance",
        }
        subscription.save(update_fields=["status", "grace_ends_at", "provider_state", "updated_at"])
        sync_entitlement(subscription)
        notify(
            subscription.organization,
            "wallet_low_balance",
            f"invoice:{invoice.id}:wallet-insufficient",
            {
                "invoice_number": invoice.invoice_number,
                "required_minor": invoice.amount_due_minor,
                "available_minor": available_minor,
            },
        )
    return attempt


@transaction.atomic
def pay_invoice_from_wallet(invoice: Invoice) -> WalletPaymentResult:
    invoice = Invoice.objects.select_for_update().select_related(
        "organization", "subscription", "subscription__price"
    ).get(pk=invoice.pk)
    existing = WalletTransaction.objects.filter(
        invoice=invoice,
        transaction_type=WalletTransaction.TransactionType.SUBSCRIPTION_PAYMENT,
        status=WalletTransaction.Status.COMPLETED,
    ).select_related("payment_attempt").first()
    if invoice.status == Invoice.Status.PAID and existing:
        return WalletPaymentResult(
            invoice=invoice,
            payment_attempt=existing.payment_attempt,
            transaction=existing,
            paid=True,
            required_minor=existing.amount_minor,
            available_minor=existing.balance_after_minor,
        )
    if invoice.status != Invoice.Status.OPEN:
        raise BillingError("invoice_transition_invalid", "Only an open invoice can be paid from a wallet.")
    if invoice.subscription.payment_source != Subscription.PaymentSource.WALLET:
        raise BillingError("wallet_payment_source_required", "This subscription is not configured for wallet payment.")
    wallet = OrganizationWallet.objects.select_for_update().filter(
        organization=invoice.organization,
        currency=invoice.currency,
    ).first()
    if not wallet:
        wallet = OrganizationWallet.objects.create(
            organization=invoice.organization,
            currency=invoice.currency,
        )
        wallet = OrganizationWallet.objects.select_for_update().get(pk=wallet.pk)
    required = int(invoice.amount_due_minor)
    available = int(wallet.available_balance_minor)
    if wallet.status != OrganizationWallet.Status.ACTIVE:
        raise BillingError("wallet_not_active", "The organization wallet is not active.")
    if required <= 0:
        raise BillingError("invoice_amount_invalid", "The invoice has no positive amount due.")
    if available < required:
        attempt = _insufficient_attempt(invoice, available)
        return WalletPaymentResult(
            invoice=invoice,
            payment_attempt=attempt,
            transaction=None,
            paid=False,
            required_minor=required,
            available_minor=available,
        )

    now = timezone.now()
    provider_id = f"wallet:{invoice.id}"
    attempt = invoice.payment_attempts.filter(provider="wallet", provider_payment_id=provider_id).first()
    if attempt:
        attempt.status = PaymentAttempt.Status.SUCCEEDED
        attempt.failure_code = ""
        attempt.attempted_at = attempt.attempted_at or now
        attempt.completed_at = now
        attempt.save(update_fields=["status", "failure_code", "attempted_at", "completed_at"])
    else:
        attempt = PaymentAttempt.objects.create(
            invoice=invoice,
            provider="wallet",
            provider_payment_id=provider_id,
            status=PaymentAttempt.Status.SUCCEEDED,
            amount_minor=required,
            currency=invoice.currency,
            attempted_at=now,
            completed_at=now,
        )
    entry = _append_locked(
        wallet,
        direction=WalletTransaction.Direction.DEBIT,
        transaction_type=WalletTransaction.TransactionType.SUBSCRIPTION_PAYMENT,
        amount_minor=required,
        idempotency_key=f"invoice:{invoice.id}:wallet-payment",
        invoice=invoice,
        payment_attempt=attempt,
        reason="Atomic subscription invoice payment",
        safe_metadata={"invoice_number": invoice.invoice_number, "source": "subscription"},
    )
    invoice.status = Invoice.Status.PAID
    invoice.amount_paid_minor = invoice.total_minor
    invoice.amount_due_minor = 0
    invoice.paid_at = now
    invoice.save(update_fields=["status", "amount_paid_minor", "amount_due_minor", "paid_at", "updated_at"])
    subscription = Subscription.objects.select_for_update().select_related("price").get(pk=invoice.subscription_id)
    if subscription.status in {
        Subscription.Status.TRIALING,
        Subscription.Status.PAST_DUE,
        Subscription.Status.GRACE,
        Subscription.Status.PAUSED,
    }:
        subscription.status = Subscription.Status.ACTIVE
        subscription.grace_ends_at = None
    if invoice.period_end >= subscription.current_period_end and subscription.current_period_end <= now:
        subscription.current_period_start = invoice.period_end
        subscription.current_period_end = _period_end(invoice.period_end, subscription.price.billing_interval)
    subscription.save(
        update_fields=["status", "grace_ends_at", "current_period_start", "current_period_end", "updated_at"]
    )
    sync_entitlement(subscription)
    notify(
        invoice.organization,
        "wallet_payment_succeeded",
        f"invoice:{invoice.id}:wallet-paid",
        {"invoice_number": invoice.invoice_number, "amount_minor": required},
    )
    return WalletPaymentResult(
        invoice=invoice,
        payment_attempt=attempt,
        transaction=entry,
        paid=True,
        required_minor=required,
        available_minor=entry.balance_after_minor,
    )


def retry_due_invoices(organization_id, *, currency: str | None = None) -> list[WalletPaymentResult]:
    now = timezone.now()
    queryset = Invoice.objects.filter(
        organization_id=organization_id,
        status=Invoice.Status.OPEN,
        amount_due_minor__gt=0,
        due_at__lte=now,
        subscription__payment_source=Subscription.PaymentSource.WALLET,
    ).order_by("due_at", "created_at", "id")
    if currency:
        queryset = queryset.filter(currency=_currency(currency))
    results: list[WalletPaymentResult] = []
    for invoice in queryset[:100]:
        result = pay_invoice_from_wallet(invoice)
        results.append(result)
        if not result.paid:
            break
    return results


def _safe_retry_due_invoices(organization_id, *, currency: str) -> None:
    try:
        retry_due_invoices(organization_id, currency=currency)
    except BillingError:
        # The credit remains durable. A reviewed retry action can inspect a later state conflict.
        return


@transaction.atomic
def process_wallet_renewal(subscription: Subscription) -> WalletPaymentResult:
    """Issue at most one invoice for the locked period and attempt one full debit."""
    subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
    if subscription.payment_source != Subscription.PaymentSource.WALLET:
        raise BillingError("wallet_payment_source_required", "This subscription does not use wallet billing.")
    now = timezone.now()
    trial_due = (
        subscription.status == Subscription.Status.TRIALING
        and subscription.trial_ends_at
        and subscription.trial_ends_at <= now
    )
    if not trial_due and subscription.current_period_end > now:
        raise BillingError("subscription_not_due", "This subscription is not due for renewal.")
    invoice = Invoice.objects.filter(
        subscription=subscription,
        period_start=subscription.current_period_start,
        period_end=subscription.current_period_end,
    ).first()
    if not invoice:
        invoice = generate_invoice(subscription, due_days=0)
    if invoice.status == Invoice.Status.DRAFT:
        invoice = issue_invoice(invoice)
    return pay_invoice_from_wallet(invoice)


@transaction.atomic
def reconcile_wallet(
    wallet: OrganizationWallet,
    *,
    platform_staff: PlatformStaffAccess | None = None,
) -> WalletReconciliationRun:
    if not getattr(settings, "WALLET_RECONCILIATION_ENABLE", True):
        raise BillingError("wallet_reconciliation_disabled", "Wallet reconciliation is disabled.", status_code=403)
    wallet = OrganizationWallet.objects.select_for_update().get(pk=wallet.pk)
    credits = WalletTransaction.objects.filter(
        wallet=wallet,
        status=WalletTransaction.Status.COMPLETED,
        direction=WalletTransaction.Direction.CREDIT,
    ).aggregate(total=Sum("amount_minor"))["total"] or 0
    debits = WalletTransaction.objects.filter(
        wallet=wallet,
        status=WalletTransaction.Status.COMPLETED,
        direction=WalletTransaction.Direction.DEBIT,
    ).aggregate(total=Sum("amount_minor"))["total"] or 0
    expected = int(credits) - int(debits)
    difference = expected - wallet.available_balance_minor
    entries = WalletTransaction.objects.filter(wallet=wallet).count()
    run = WalletReconciliationRun.objects.create(
        wallet=wallet,
        expected_balance_minor=expected,
        cached_balance_minor=wallet.available_balance_minor,
        difference_minor=difference,
        ledger_entries=entries,
        status=(
            WalletReconciliationRun.Status.MATCHED
            if difference == 0
            else WalletReconciliationRun.Status.MISMATCH
        ),
        safe_report={
            "currency": wallet.currency,
            "ledger_version": wallet.ledger_version,
            "credits_minor": int(credits),
            "debits_minor": int(debits),
        },
        performed_by_platform_staff=platform_staff,
    )
    wallet.last_reconciled_at = timezone.now()
    wallet.save(update_fields=["last_reconciled_at", "updated_at"])
    return run


def backfill_wallets(organizations: Iterable[Organization]) -> int:
    count = 0
    for organization in organizations:
        try:
            account = organization.billing_account
        except ObjectDoesNotExist:
            account = None
        currency = getattr(account, "default_currency", None)
        _, created = OrganizationWallet.objects.get_or_create(
            organization=organization,
            currency=_currency(currency or settings.BILLING_DEFAULT_CURRENCY),
        )
        count += int(created)
    return count
