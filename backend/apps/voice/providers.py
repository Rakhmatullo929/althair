from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

import httpx
from django.conf import settings
from twilio.rest import Client

from voice.models import VoiceCarrierType, VoiceConnection, VoiceOwnershipMode


class VoiceProviderError(Exception):
    def __init__(self, code: str, *, transient: bool = False):
        self.code = code
        self.transient = transient
        super().__init__(code)


class VoiceCarrierProvider(ABC):
    @abstractmethod
    def health(self, connection: VoiceConnection) -> dict: ...


class FakeVoiceCarrierProvider(VoiceCarrierProvider):
    def health(self, connection: VoiceConnection) -> dict:
        return {"carrier_reachable": True, "number_voice_capable": True, "sip_trunk_ready": True}


class TwilioSIPCarrierProvider(VoiceCarrierProvider):
    def _credentials(self, connection: VoiceConnection) -> tuple[str, str, str]:
        if connection.ownership_mode == VoiceOwnershipMode.PLATFORM_MANAGED:
            account = settings.TWILIO_VOICE_ACCOUNT_SID
            username = settings.TWILIO_VOICE_API_KEY_SID or account
            password = settings.TWILIO_VOICE_API_KEY_SECRET or settings.TWILIO_VOICE_AUTH_TOKEN
        else:
            account = connection.carrier_account_sid
            username = connection.carrier_api_key_sid or account
            password = connection.carrier_api_key_secret_encrypted or connection.carrier_auth_token_encrypted
        if not account or not username or not password:
            raise VoiceProviderError("carrier_credentials_missing")
        return str(account), str(username), str(password)

    def health(self, connection: VoiceConnection) -> dict:
        if not settings.VOICE_ENABLE_LIVE:
            raise VoiceProviderError("live_voice_disabled")
        try:
            account, username, password = self._credentials(connection)
            client = Client(username, password, account)
            number = (
                client.incoming_phone_numbers(connection.phone_number_sid).fetch()
                if connection.phone_number_sid
                else client.incoming_phone_numbers.list(phone_number=connection.phone_number_e164, limit=1)[0]
            )
            capabilities = getattr(number, "capabilities", {}) or {}
            trunk_sid = connection.sip_trunk_sid or settings.TWILIO_VOICE_SIP_TRUNK_SID
            if trunk_sid:
                client.trunking.v1.trunks(trunk_sid).fetch()
            return {
                "carrier_reachable": True,
                "number_voice_capable": bool(capabilities.get("voice")),
                "sip_trunk_ready": bool(trunk_sid),
            }
        except Exception as exc:
            code = str(getattr(exc, "code", "carrier_unreachable"))[:40]
            raise VoiceProviderError(code or "carrier_unreachable", transient=True) from exc


def carrier_provider_for(connection: VoiceConnection) -> VoiceCarrierProvider:
    if connection.carrier == VoiceCarrierType.FAKE:
        if not settings.VOICE_FAKE_PROVIDER:
            raise VoiceProviderError("fake_voice_disabled")
        return FakeVoiceCarrierProvider()
    return TwilioSIPCarrierProvider()


class RealtimeVoiceProvider(ABC):
    @abstractmethod
    def accept(self, *, call_id: str, session: dict, safety_identifier: str) -> None: ...

    @abstractmethod
    def reject(self, *, call_id: str, status_code: int = 603) -> None: ...

    @abstractmethod
    def refer(self, *, call_id: str, target_uri: str, idempotency_key: str) -> str: ...

    @abstractmethod
    def hangup(self, *, call_id: str) -> None: ...

    @abstractmethod
    async def events(self, *, call_id: str) -> AsyncIterator[dict]: ...

    async def send(self, *, call_id: str, event: dict) -> None:
        return None


class FakeRealtimeVoiceProvider(RealtimeVoiceProvider):
    def __init__(self, events: list[dict] | None = None):
        self._events = list(events or [])

    def accept(self, *, call_id: str, session: dict, safety_identifier: str) -> None:
        if not settings.VOICE_FAKE_PROVIDER:
            raise VoiceProviderError("fake_realtime_disabled")

    def reject(self, *, call_id: str, status_code: int = 603) -> None:
        return None

    def refer(self, *, call_id: str, target_uri: str, idempotency_key: str) -> str:
        if "fail" in target_uri:
            raise VoiceProviderError("fake_transfer_failed")
        digest = hashlib.sha256(f"{call_id}|{target_uri}|{idempotency_key}".encode()).hexdigest()[:24]
        return f"fake-refer-{digest}"

    def hangup(self, *, call_id: str) -> None:
        return None

    async def events(self, *, call_id: str) -> AsyncIterator[dict]:
        for event in self._events:
            yield event


class OpenAIRealtimeSIPProvider(RealtimeVoiceProvider):
    base_url = "https://api.openai.com/v1/realtime/calls"

    def _headers(self, safety_identifier: str = "", idempotency_key: str = "") -> dict:
        if not settings.VOICE_ENABLE_LIVE or not settings.OPENAI_API_KEY:
            raise VoiceProviderError("openai_realtime_not_configured")
        headers = {"Authorization": f"Bearer {settings.OPENAI_API_KEY}", "Content-Type": "application/json"}
        if safety_identifier:
            headers["OpenAI-Safety-Identifier"] = safety_identifier
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _post(self, call_id: str, action: str, *, payload: dict | None = None, safety_identifier: str = "", idempotency_key: str = ""):
        try:
            response = httpx.post(
                f"{self.base_url}/{call_id}/{action}",
                headers=self._headers(safety_identifier, idempotency_key),
                json=payload,
                timeout=settings.VOICE_PROVIDER_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except Exception as exc:
            code = f"openai_{action}_failed"
            raise VoiceProviderError(code, transient=True) from exc
        return response

    def accept(self, *, call_id: str, session: dict, safety_identifier: str) -> None:
        self._post(call_id, "accept", payload=session, safety_identifier=safety_identifier)

    def reject(self, *, call_id: str, status_code: int = 603) -> None:
        self._post(call_id, "reject", payload={"status_code": status_code})

    def refer(self, *, call_id: str, target_uri: str, idempotency_key: str) -> str:
        response = self._post(
            call_id, "refer", payload={"target_uri": target_uri}, idempotency_key=idempotency_key
        )
        return response.headers.get("x-request-id", "refer-accepted")

    def hangup(self, *, call_id: str) -> None:
        self._post(call_id, "hangup")

    async def events(self, *, call_id: str) -> AsyncIterator[dict]:
        import websockets

        try:
            async with websockets.connect(
                f"wss://api.openai.com/v1/realtime?call_id={call_id}",
                additional_headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                open_timeout=settings.VOICE_PROVIDER_TIMEOUT_SECONDS,
                close_timeout=5,
                max_size=1_000_000,
            ) as socket:
                await socket.send(json.dumps({"type": "response.create"}))
                async for raw in socket:
                    event = json.loads(raw)
                    if isinstance(event, dict):
                        yield event
        except Exception as exc:
            raise VoiceProviderError("realtime_websocket_lost", transient=True) from exc


def realtime_provider_for(connection: VoiceConnection, *, events: list[dict] | None = None) -> RealtimeVoiceProvider:
    provider = settings.VOICE_REALTIME_PROVIDER
    if connection.carrier == VoiceCarrierType.FAKE or provider == "fake":
        return FakeRealtimeVoiceProvider(events)
    if provider == "openai":
        return OpenAIRealtimeSIPProvider()
    raise VoiceProviderError("unsupported_realtime_provider")
