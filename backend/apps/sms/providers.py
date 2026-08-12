from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass

from django.conf import settings
from twilio.rest import Client

from sms.models import SMSConnection, SMSOwnershipMode, SMSProviderType


class SMSProviderError(Exception):
    def __init__(self, code: str, *, transient: bool = False):
        self.code = code
        self.transient = transient
        super().__init__(code)


@dataclass(frozen=True)
class SMSSendResult:
    message_sid: str
    status: str
    provider_segments: int | None = None


class SMSProvider(ABC):
    @abstractmethod
    def health(self, connection: SMSConnection) -> dict: ...

    @abstractmethod
    def send(self, *, connection: SMSConnection, to: str, body: str, status_callback: str) -> SMSSendResult: ...


class FakeSMSProvider(SMSProvider):
    def health(self, connection: SMSConnection) -> dict:
        return {
            "provider_reachable": True,
            "sender_active": True,
            "messaging_service_active": bool(connection.messaging_service_sid),
        }

    def send(self, *, connection: SMSConnection, to: str, body: str, status_callback: str) -> SMSSendResult:
        if to.endswith("0000"):
            raise SMSProviderError("fake_invalid_recipient")
        digest = hashlib.sha256(
            f"{connection.id}|{to}|{body}|{status_callback}".encode()
        ).hexdigest()[:32].upper()
        return SMSSendResult(message_sid=f"SM{digest}", status="queued")


class TwilioSMSProvider(SMSProvider):
    def _credentials(self, connection: SMSConnection) -> tuple[str, str, str]:
        if connection.ownership_mode == SMSOwnershipMode.PLATFORM_MANAGED:
            account_sid = settings.TWILIO_ACCOUNT_SID
            username = getattr(settings, "TWILIO_API_KEY_SID", "") or account_sid
            password = getattr(settings, "TWILIO_API_KEY_SECRET", "") or settings.TWILIO_AUTH_TOKEN
        else:
            account_sid = connection.account_sid
            username = connection.api_key_sid or account_sid
            password = connection.api_key_secret_encrypted or connection.auth_token_encrypted
        if not account_sid or not username or not password:
            raise SMSProviderError("credentials_missing")
        return account_sid, username, password

    def _client(self, connection: SMSConnection):
        account_sid, username, password = self._credentials(connection)
        return Client(username, password, account_sid)

    def health(self, connection: SMSConnection) -> dict:
        if not settings.SMS_ENABLE_LIVE:
            raise SMSProviderError("live_provider_disabled")
        try:
            client = self._client(connection)
            account = client.api.accounts(
                connection.account_sid or settings.TWILIO_ACCOUNT_SID
            ).fetch()
            account_active = str(getattr(account, "status", "")).lower() == "active"
            sender_active = False
            messaging_service_active = False
            if connection.messaging_service_sid:
                client.messaging.v1.services(connection.messaging_service_sid).fetch()
                messaging_service_active = True
                sender_active = account_active
            elif connection.phone_number_sid:
                number = client.incoming_phone_numbers(connection.phone_number_sid).fetch()
                capabilities = getattr(number, "capabilities", {}) or {}
                sender_active = bool(
                    capabilities.get("sms")
                    and str(getattr(number, "phone_number", "")) == connection.sender_address
                )
            else:
                numbers = client.incoming_phone_numbers.list(
                    phone_number=connection.sender_address, limit=1
                )
                if numbers:
                    capabilities = getattr(numbers[0], "capabilities", {}) or {}
                    sender_active = bool(capabilities.get("sms"))
            return {
                "provider_reachable": True,
                "sender_active": sender_active,
                "messaging_service_active": messaging_service_active,
            }
        except Exception as exc:
            code = str(getattr(exc, "code", "provider_unreachable"))[:32]
            raise SMSProviderError(code or "provider_unreachable", transient=True) from exc

    def send(self, *, connection: SMSConnection, to: str, body: str, status_callback: str) -> SMSSendResult:
        if not settings.SMS_ENABLE_LIVE:
            raise SMSProviderError("live_provider_disabled")
        arguments = {"to": to, "body": body, "status_callback": status_callback}
        if connection.messaging_service_sid:
            arguments["messaging_service_sid"] = connection.messaging_service_sid
        else:
            arguments["from_"] = connection.sender_address
        try:
            message = self._client(connection).messages.create(**arguments)
        except Exception as exc:
            provider_code = str(getattr(exc, "code", "send_failed"))[:32]
            permanent = provider_code in {"21211", "21610", "21612", "21614", "21617"}
            raise SMSProviderError(provider_code or "send_failed", transient=not permanent) from exc
        return SMSSendResult(
            message_sid=str(message.sid),
            status=str(getattr(message, "status", "queued") or "queued").lower(),
            provider_segments=int(message.num_segments) if getattr(message, "num_segments", None) else None,
        )


def provider_for(connection: SMSConnection) -> SMSProvider:
    if connection.provider == SMSProviderType.FAKE:
        if not settings.SMS_FAKE_PROVIDER:
            raise SMSProviderError("fake_provider_disabled")
        return FakeSMSProvider()
    if connection.provider == SMSProviderType.TWILIO:
        return TwilioSMSProvider()
    raise SMSProviderError("unsupported_provider")
