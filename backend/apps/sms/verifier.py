from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from twilio.request_validator import RequestValidator


@dataclass(frozen=True)
class VerificationResult:
    valid: bool
    external_url: str
    reason: str = ""


def external_request_url(request) -> str:
    public_base = str(getattr(settings, "SMS_PUBLIC_BASE_URL", "") or "").rstrip("/")
    if public_base:
        base = urlsplit(public_base)
        requested = urlsplit(request.get_full_path())
        return urlunsplit((base.scheme, base.netloc, requested.path, requested.query, ""))
    return request.build_absolute_uri()


class SMSWebhookVerifier:
    def verify(self, *, request, auth_token: str) -> VerificationResult:
        url = external_request_url(request)
        signature = request.headers.get("X-Twilio-Signature", "")
        if not auth_token or not signature:
            return VerificationResult(False, url, "missing_signature_or_token")
        content_type = str(request.content_type or "").split(";", 1)[0].strip().lower()
        if content_type == "application/json":
            params: dict | str = request.body.decode("utf-8", errors="strict")
        else:
            params = {key: request.POST.getlist(key) for key in request.POST.keys()}
            params = {key: values[0] if len(values) == 1 else values for key, values in params.items()}
        valid = RequestValidator(auth_token).validate(url, params, signature)
        return VerificationResult(valid, url, "" if valid else "invalid_signature")
