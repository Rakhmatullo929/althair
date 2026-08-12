from __future__ import annotations

from abc import ABC, abstractmethod

import phonenumbers
from django.conf import settings


class SMSFraudPolicyError(ValueError):
    pass


class SMSFraudPolicy(ABC):
    """Provider-independent hook for country, recipient and future fraud controls."""

    @abstractmethod
    def validate_recipient(self, recipient: str) -> None: ...


class DefaultSMSFraudPolicy(SMSFraudPolicy):
    def validate_recipient(self, recipient: str) -> None:
        parsed = phonenumbers.parse(recipient, None)
        region = (phonenumbers.region_code_for_number(parsed) or "").upper()
        allowed = set(settings.SMS_ALLOWED_COUNTRY_CODES)
        blocked = set(settings.SMS_BLOCKED_COUNTRY_CODES)
        if region and region in blocked:
            raise SMSFraudPolicyError("recipient_country_blocked")
        if allowed and region not in allowed:
            raise SMSFraudPolicyError("recipient_country_not_allowed")


def fraud_policy() -> SMSFraudPolicy:
    return DefaultSMSFraudPolicy()
