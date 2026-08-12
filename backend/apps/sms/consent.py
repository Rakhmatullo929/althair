from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from sms.models import (
    SMSAuditEvent,
    SMSConsent,
    SMSConsentSource,
    SMSConsentState,
    SMSProviderType,
)


STOP_KEYWORDS = frozenset({"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REVOKE", "OPTOUT"})
START_KEYWORDS = frozenset({"START", "UNSTOP"})
HELP_KEYWORDS = frozenset({"HELP", "INFO"})


@dataclass(frozen=True)
class ConsentDecision:
    state: str
    keyword_type: str
    ai_eligible: bool


def classify_keyword(body: str, provider_signal: str = "") -> str:
    signal = str(provider_signal or "").strip().upper()
    if signal in {"STOP", "START", "HELP"}:
        return signal
    normalized = " ".join(str(body or "").strip().upper().split())
    if normalized in STOP_KEYWORDS:
        return "STOP"
    if normalized in START_KEYWORDS:
        return "START"
    if normalized in HELP_KEYWORDS:
        return "HELP"
    return ""


@transaction.atomic
def apply_inbound_consent(*, connection, contact_identity, body: str, provider_signal: str = "") -> ConsentDecision:
    consent, _ = SMSConsent.objects.select_for_update().get_or_create(
        organization=connection.organization,
        connection=connection,
        contact_identity=contact_identity,
        defaults={
            "state": SMSConsentState.UNKNOWN,
            "source": SMSConsentSource.INBOUND_MESSAGE,
        },
    )
    keyword_type = classify_keyword(body, provider_signal)
    previous = consent.state
    now = timezone.now()
    if keyword_type == "STOP":
        consent.state = SMSConsentState.OPTED_OUT
        consent.source = SMSConsentSource.PROVIDER_OPT_OUT
        consent.opted_out_at = now
        consent.consented_at = None
    elif keyword_type == "START":
        provider_permits = bool(
            str(provider_signal or "").upper() == "START"
            or connection.provider == SMSProviderType.FAKE
            or not connection.advanced_opt_out_enabled
        )
        if provider_permits:
            consent.state = SMSConsentState.OPTED_IN
            consent.source = SMSConsentSource.PROVIDER_OPT_IN
            consent.consented_at = now
            consent.opted_out_at = None
    elif keyword_type == "HELP":
        pass
    elif consent.state == SMSConsentState.UNKNOWN and connection.allow_inbound_support:
        consent.state = SMSConsentState.IMPLIED_SUPPORT
        consent.source = SMSConsentSource.INBOUND_MESSAGE
        consent.consented_at = now
    consent.last_keyword = keyword_type
    consent.save()
    if previous != consent.state or keyword_type:
        SMSAuditEvent.objects.create(
            organization=connection.organization,
            connection=connection,
            event_type="sms.consent_transition",
            metadata={"from": previous, "to": consent.state, "keyword": keyword_type or "none"},
        )
    return ConsentDecision(
        state=consent.state,
        keyword_type=keyword_type,
        ai_eligible=not keyword_type and consent.state in {SMSConsentState.IMPLIED_SUPPORT, SMSConsentState.OPTED_IN},
    )


def consent_allows_send(consent: SMSConsent | None) -> bool:
    return bool(consent and consent.state in {SMSConsentState.IMPLIED_SUPPORT, SMSConsentState.OPTED_IN})
