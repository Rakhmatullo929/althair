from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from openai import OpenAI
from twilio.request_validator import RequestValidator


class VoiceSignatureError(ValueError):
    pass


def voice_external_request_url(request) -> str:
    public_base = str(getattr(settings, "TWILIO_VOICE_PUBLIC_BASE_URL", "") or "").rstrip("/")
    if public_base:
        base = urlsplit(public_base)
        requested = urlsplit(request.get_full_path())
        return urlunsplit((base.scheme, base.netloc, requested.path, requested.query, ""))
    return request.build_absolute_uri()


class OpenAIIncomingCallVerifier:
    """Verify the raw webhook before parsing or applying side effects."""

    def unwrap(self, *, raw_body: bytes, headers) -> dict:
        body = raw_body.decode("utf-8", errors="strict")
        if settings.VOICE_REALTIME_PROVIDER == "fake":
            supplied = str(headers.get("X-Voice-Fake-Signature", ""))
            expected = hmac.new(
                settings.VOICE_FAKE_WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256
            ).hexdigest()
            if not supplied or not hmac.compare_digest(supplied, expected):
                raise VoiceSignatureError("invalid_openai_signature")
            try:
                event = json.loads(body)
            except (TypeError, ValueError) as exc:
                raise VoiceSignatureError("invalid_openai_payload") from exc
            if not isinstance(event, dict):
                raise VoiceSignatureError("invalid_openai_payload")
            return event

        if not settings.OPENAI_WEBHOOK_SECRET or not settings.OPENAI_API_KEY:
            raise VoiceSignatureError("openai_webhook_configuration_missing")
        try:
            event = OpenAI(
                api_key=settings.OPENAI_API_KEY,
                webhook_secret=settings.OPENAI_WEBHOOK_SECRET,
            ).webhooks.unwrap(body, headers)
        except Exception as exc:
            raise VoiceSignatureError("invalid_openai_signature") from exc
        if hasattr(event, "model_dump"):
            return event.model_dump(mode="json")
        if isinstance(event, dict):
            return event
        raise VoiceSignatureError("invalid_openai_payload")


@dataclass(frozen=True)
class TwilioVoiceVerification:
    valid: bool
    external_url: str
    reason: str = ""


class TwilioVoiceWebhookVerifier:
    def verify(self, *, request, auth_token: str) -> TwilioVoiceVerification:
        url = voice_external_request_url(request)
        signature = request.headers.get("X-Twilio-Signature", "")
        if not auth_token or not signature:
            return TwilioVoiceVerification(False, url, "missing_signature_or_token")
        params = {key: request.POST.getlist(key) for key in request.POST.keys()}
        normalized = {key: values[0] if len(values) == 1 else values for key, values in params.items()}
        valid = RequestValidator(auth_token).validate(url, normalized, signature)
        return TwilioVoiceVerification(valid, url, "" if valid else "invalid_signature")
