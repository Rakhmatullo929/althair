from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Protocol

from django.conf import settings


class BillingProviderError(Exception):
    def __init__(self, code: str, message: str, *, supported: bool = True):
        self.code = code
        self.message = message
        self.supported = supported
        super().__init__(message)


@dataclass(frozen=True)
class ProviderResult:
    status: str
    provider_id: str = ""
    action_url: str = ""
    safe_state: dict | None = None


@dataclass(frozen=True)
class VerifiedBillingEvent:
    provider_event_id: str
    event_type: str
    object_type: str
    object_id: str
    status: str
    amount_minor: int
    currency: str
    payload_hash: str


class BillingProvider(Protocol):
    key: str
    online_checkout: bool

    def create_customer(self, *, organization_id: str) -> ProviderResult: ...
    def create_checkout_session(self, *, invoice_id: str) -> ProviderResult: ...
    def create_subscription(self, *, subscription_id: str) -> ProviderResult: ...
    def change_subscription(self, *, subscription_id: str, plan_id: str) -> ProviderResult: ...
    def cancel_subscription(self, *, subscription_id: str) -> ProviderResult: ...
    def resume_subscription(self, *, subscription_id: str) -> ProviderResult: ...
    def create_payment_attempt(self, *, invoice_id: str, amount_minor: int, currency: str) -> ProviderResult: ...
    def fetch_payment_status(self, *, provider_payment_id: str) -> ProviderResult: ...
    def parse_verified_webhook(self, *, payload: bytes, signature: str) -> VerifiedBillingEvent: ...
    def refund_payment(self, *, provider_payment_id: str, amount_minor: int) -> ProviderResult: ...


class FakeBillingProvider:
    key = "fake"
    online_checkout = True

    @staticmethod
    def _id(prefix: str, value: str) -> str:
        return f"fake_{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:20]}"

    @staticmethod
    def _signing_key() -> bytes:
        return str(getattr(settings, "BILLING_FAKE_SIGNING_KEY", "deterministic-ci-only")).encode()

    @classmethod
    def sign_event(cls, payload: bytes) -> str:
        return hmac.new(cls._signing_key(), payload, hashlib.sha256).hexdigest()

    def create_customer(self, *, organization_id: str) -> ProviderResult:
        return ProviderResult("created", self._id("customer", organization_id))

    def create_checkout_session(self, *, invoice_id: str) -> ProviderResult:
        if not (settings.DEBUG or settings.TESTING):
            raise BillingProviderError(
                "payment_provider_not_connected",
                "Online payment is not connected yet.",
                supported=False,
            )
        return ProviderResult(
            "ready",
            self._id("checkout", invoice_id),
            action_url=f"/development/billing/fake-checkout/{invoice_id}",
            safe_state={"mode": "deterministic_fake"},
        )

    def create_subscription(self, *, subscription_id: str) -> ProviderResult:
        return ProviderResult("active", self._id("subscription", subscription_id))

    def change_subscription(self, *, subscription_id: str, plan_id: str) -> ProviderResult:
        return ProviderResult("scheduled", self._id("change", f"{subscription_id}:{plan_id}"))

    def cancel_subscription(self, *, subscription_id: str) -> ProviderResult:
        return ProviderResult("scheduled", self._id("cancel", subscription_id))

    def resume_subscription(self, *, subscription_id: str) -> ProviderResult:
        return ProviderResult("active", self._id("resume", subscription_id))

    def create_payment_attempt(self, *, invoice_id: str, amount_minor: int, currency: str) -> ProviderResult:
        return ProviderResult(
            "pending",
            self._id("payment", f"{invoice_id}:{amount_minor}:{currency}"),
            safe_state={"mode": "deterministic_fake"},
        )

    def fetch_payment_status(self, *, provider_payment_id: str) -> ProviderResult:
        return ProviderResult("pending", provider_payment_id)

    def parse_verified_webhook(self, *, payload: bytes, signature: str) -> VerifiedBillingEvent:
        expected = self.sign_event(payload)
        if not hmac.compare_digest(expected, str(signature or "")):
            raise BillingProviderError("invalid_signature", "The billing event signature is invalid.")
        try:
            data = json.loads(payload.decode("utf-8"))
            event_id = str(data["id"])
            event_type = str(data["type"])
            object_type = str(data["object_type"])
            object_id = str(data["object_id"])
            status = str(data["status"])
            amount = int(data.get("amount_minor", 0))
            currency = str(data.get("currency", "UZS")).upper()
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BillingProviderError("invalid_event", "The billing event is malformed.") from exc
        return VerifiedBillingEvent(
            provider_event_id=event_id[:160],
            event_type=event_type[:100],
            object_type=object_type[:40],
            object_id=object_id[:160],
            status=status[:24],
            amount_minor=max(0, amount),
            currency=currency[:3],
            payload_hash=hashlib.sha256(payload).hexdigest(),
        )

    def refund_payment(self, *, provider_payment_id: str, amount_minor: int) -> ProviderResult:
        return ProviderResult("refunded", self._id("refund", f"{provider_payment_id}:{amount_minor}"))


class ManualBillingProvider:
    key = "manual"
    online_checkout = False

    def create_customer(self, *, organization_id: str) -> ProviderResult:
        return ProviderResult("manual_review", safe_state={"mode": "manual"})

    def create_checkout_session(self, *, invoice_id: str) -> ProviderResult:
        raise BillingProviderError(
            "payment_provider_not_connected",
            "Online payment is not connected yet. Contact sales for manual billing.",
            supported=False,
        )

    def create_subscription(self, *, subscription_id: str) -> ProviderResult:
        return ProviderResult("manual_review", safe_state={"mode": "manual"})

    def change_subscription(self, *, subscription_id: str, plan_id: str) -> ProviderResult:
        return ProviderResult("manual_review", safe_state={"mode": "manual"})

    def cancel_subscription(self, *, subscription_id: str) -> ProviderResult:
        return ProviderResult("scheduled", safe_state={"mode": "manual"})

    def resume_subscription(self, *, subscription_id: str) -> ProviderResult:
        return ProviderResult("manual_review", safe_state={"mode": "manual"})

    def create_payment_attempt(self, *, invoice_id: str, amount_minor: int, currency: str) -> ProviderResult:
        return ProviderResult("pending_review", safe_state={"mode": "manual"})

    def fetch_payment_status(self, *, provider_payment_id: str) -> ProviderResult:
        return ProviderResult("pending_review", safe_state={"mode": "manual"})

    def parse_verified_webhook(self, *, payload: bytes, signature: str) -> VerifiedBillingEvent:
        raise BillingProviderError("webhook_not_supported", "Manual billing does not accept webhooks.", supported=False)

    def refund_payment(self, *, provider_payment_id: str, amount_minor: int) -> ProviderResult:
        raise BillingProviderError("refund_not_supported", "Manual refunds require an offline review.", supported=False)


class WalletBillingProvider(ManualBillingProvider):
    """Internal ledger provider. It never handles card data or external webhooks."""

    key = "wallet"
    online_checkout = False

    def create_customer(self, *, organization_id: str) -> ProviderResult:
        return ProviderResult("ready", safe_state={"mode": "organization_wallet"})

    def create_subscription(self, *, subscription_id: str) -> ProviderResult:
        return ProviderResult("ready", safe_state={"mode": "organization_wallet"})

    def create_payment_attempt(self, *, invoice_id: str, amount_minor: int, currency: str) -> ProviderResult:
        return ProviderResult(
            "internal",
            provider_id=f"wallet:{invoice_id}",
            safe_state={"mode": "organization_wallet"},
        )

    def parse_verified_webhook(self, *, payload: bytes, signature: str) -> VerifiedBillingEvent:
        raise BillingProviderError(
            "webhook_not_supported",
            "Organization wallet payments do not accept webhooks.",
            supported=False,
        )


def get_billing_provider(key: str | None = None) -> BillingProvider:
    provider = str(key or settings.BILLING_PROVIDER).lower()
    if provider == "fake":
        return FakeBillingProvider()
    if provider == "manual":
        return ManualBillingProvider()
    if provider == "wallet":
        return WalletBillingProvider()
    raise BillingProviderError(
        "payment_provider_not_connected",
        "Online payment is not connected yet.",
        supported=False,
    )
