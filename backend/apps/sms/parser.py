from __future__ import annotations

import hashlib
from dataclasses import dataclass

import phonenumbers
from django.conf import settings


class SMSPayloadError(ValueError):
    pass


def normalize_phone(value: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = phonenumbers.parse(raw, None)
    except phonenumbers.NumberParseException as exc:
        raise SMSPayloadError("invalid_phone") from exc
    if not phonenumbers.is_possible_number(parsed):
        raise SMSPayloadError("invalid_phone")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def _bounded(value, limit: int) -> str:
    return str(value or "")[:limit]


def _small_int(value, *, minimum=0, maximum=100) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise SMSPayloadError("invalid_integer") from exc
    if not minimum <= parsed <= maximum:
        raise SMSPayloadError("invalid_integer")
    return parsed


@dataclass(frozen=True)
class SMSInbound:
    message_sid: str
    from_address: str
    raw_from_address: str
    to_address: str
    body: str
    num_media: int
    messaging_service_sid: str
    opt_out_type: str


@dataclass(frozen=True)
class SMSStatusCallback:
    message_sid: str
    status: str
    to_address: str
    from_address: str
    error_code: str
    segments: int | None
    messaging_service_sid: str


def parse_inbound(params) -> SMSInbound:
    message_sid = _bounded(params.get("MessageSid") or params.get("SmsSid"), 64)
    raw_from = _bounded(params.get("From"), 64)
    raw_to = _bounded(params.get("To"), 64)
    if not message_sid.startswith(("SM", "MM")):
        raise SMSPayloadError("invalid_message_sid")
    body = _bounded(params.get("Body"), 10000)
    if not body and not params.get("NumMedia"):
        raise SMSPayloadError("empty_message")
    return SMSInbound(
        message_sid=message_sid,
        from_address=normalize_phone(raw_from),
        raw_from_address=raw_from,
        to_address=normalize_phone(raw_to),
        body=body,
        num_media=_small_int(
            params.get("NumMedia"), maximum=int(getattr(settings, "SMS_MAX_MEDIA_ITEMS", 10))
        ),
        messaging_service_sid=_bounded(params.get("MessagingServiceSid"), 64),
        opt_out_type=_bounded(params.get("OptOutType"), 16).upper(),
    )


def parse_status(params) -> SMSStatusCallback:
    message_sid = _bounded(params.get("MessageSid") or params.get("SmsSid"), 64)
    status = _bounded(params.get("MessageStatus") or params.get("SmsStatus"), 24).lower()
    if not message_sid.startswith(("SM", "MM")) or not status:
        raise SMSPayloadError("invalid_status_callback")
    segments = None
    if params.get("NumSegments") not in (None, ""):
        segments = _small_int(params.get("NumSegments"), minimum=1, maximum=100)
    return SMSStatusCallback(
        message_sid=message_sid,
        status=status,
        to_address=_bounded(params.get("To"), 64),
        from_address=_bounded(params.get("From"), 64),
        error_code=_bounded(params.get("ErrorCode"), 32),
        segments=segments,
        messaging_service_sid=_bounded(params.get("MessagingServiceSid"), 64),
    )


def inbound_event_key(message_sid: str) -> str:
    return hashlib.sha256(f"inbound:{message_sid}".encode()).hexdigest()


def status_event_key(callback: SMSStatusCallback) -> str:
    material = "|".join(
        [
            callback.message_sid,
            callback.status,
            callback.error_code,
            callback.to_address,
            callback.from_address,
            callback.messaging_service_sid,
        ]
    )
    return hashlib.sha256(material.encode()).hexdigest()
