from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from django.conf import settings


class TelegramProviderError(Exception):
    def __init__(self, code: str, *, transient: bool = False):
        super().__init__(code)
        self.code = code
        self.transient = transient


@dataclass(frozen=True)
class TelegramBotSnapshot:
    user_id: int
    username: str
    name: str
    token: str
    can_manage_bots: bool = False


@dataclass(frozen=True)
class TelegramSendResult:
    message_id: str


class BaseTelegramProvider:
    def manager_health(self) -> dict:
        raise NotImplementedError

    def get_managed_bot(self, user_id: int, *, rotate=False) -> TelegramBotSnapshot:
        raise NotImplementedError

    def validate_existing_bot(self, token: str) -> TelegramBotSnapshot:
        raise NotImplementedError

    def configure_bot(self, connection, token: str, webhook_secret: str) -> dict:
        raise NotImplementedError

    def send_text(self, *, connection, chat_id: str, text: str, reply_to_message_id: str = "") -> TelegramSendResult:
        raise NotImplementedError

    def health(self, connection) -> dict:
        raise NotImplementedError

    def get_access_settings(self, connection) -> dict:
        raise NotImplementedError

    def set_access_settings(self, connection, restricted: bool, user_ids: list[int]) -> dict:
        raise NotImplementedError


class FakeTelegramProvider(BaseTelegramProvider):
    def manager_health(self) -> dict:
        return {"reachable": True, "can_manage_bots": True, "username": settings.TELEGRAM_MANAGER_BOT_USERNAME or "AlthairManagerBot"}

    def get_managed_bot(self, user_id: int, *, rotate=False) -> TelegramBotSnapshot:
        digest = hashlib.sha256(f"managed:{user_id}:{int(rotate)}".encode()).hexdigest()
        return TelegramBotSnapshot(user_id=user_id, username=f"managed_{user_id}_bot", name="Managed company bot", token=f"test-only-{digest}")

    def validate_existing_bot(self, token: str) -> TelegramBotSnapshot:
        if not token.startswith("test-only-existing:"):
            raise TelegramProviderError("bot_token_invalid")
        parts = token.split(":", 3)
        if len(parts) < 4 or not parts[1].isdigit():
            raise TelegramProviderError("bot_token_invalid")
        return TelegramBotSnapshot(user_id=int(parts[1]), username=parts[2], name=parts[3], token=token)

    def configure_bot(self, connection, token: str, webhook_secret: str) -> dict:
        if "configure-fail" in token:
            raise TelegramProviderError("webhook_configuration_failed")
        return {"webhook": "verified", "commands": ["start", "help", "human", "language", "privacy"]}

    def send_text(self, *, connection, chat_id: str, text: str, reply_to_message_id: str = "") -> TelegramSendResult:
        lowered = text.casefold()
        if "[telegram-transient-error]" in lowered:
            raise TelegramProviderError("provider_temporarily_unavailable", transient=True)
        if "[telegram-blocked]" in lowered:
            raise TelegramProviderError("bot_blocked_by_user")
        if "[telegram-token-invalid]" in lowered:
            raise TelegramProviderError("bot_token_invalid")
        digest = hashlib.sha256(f"{connection.id}:{chat_id}:{text}:{reply_to_message_id}:{connection.token_version}".encode()).hexdigest()
        return TelegramSendResult(message_id=str(int(digest[:12], 16)))

    def health(self, connection) -> dict:
        return {"provider_reachable": True, "bot_matches": True, "webhook_matches": connection.webhook_status == "verified", "pending_updates": 0}

    def get_access_settings(self, connection) -> dict:
        return {"is_access_restricted": connection.access_restricted, "added_user_ids": list(connection.permitted_telegram_user_ids)}

    def set_access_settings(self, connection, restricted: bool, user_ids: list[int]) -> dict:
        return {"is_access_restricted": restricted, "added_user_ids": list(user_ids)}


class LiveTelegramProvider(BaseTelegramProvider):
    timeout = 20

    def _call(self, token: str, method: str, payload=None):
        if not token:
            raise TelegramProviderError("bot_token_missing")
        data = json.dumps(payload or {}, separators=(",", ":")).encode()
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/{method}",
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Althair-Telegram/1.0"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read(1_000_000).decode())
        except urllib.error.HTTPError as exc:
            transient = exc.code == 429 or 500 <= exc.code < 600
            code = "provider_rate_limited" if exc.code == 429 else ("bot_token_invalid" if exc.code == 401 else "provider_request_rejected")
            raise TelegramProviderError(code, transient=transient) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise TelegramProviderError("provider_temporarily_unavailable", transient=True) from exc
        if not isinstance(body, dict) or not body.get("ok"):
            code = int(body.get("error_code") or 0) if isinstance(body, dict) else 0
            description = str(body.get("description") or "").casefold() if isinstance(body, dict) else ""
            if code == 401:
                raise TelegramProviderError("bot_token_invalid")
            if "blocked" in description or "deactivated" in description:
                raise TelegramProviderError("bot_blocked_by_user")
            if "chat not found" in description:
                raise TelegramProviderError("chat_not_found")
            raise TelegramProviderError("provider_request_rejected", transient=code == 429 or code >= 500)
        return body.get("result")

    def manager_health(self) -> dict:
        result = self._call(settings.TELEGRAM_MANAGER_BOT_TOKEN, "getMe")
        return {"reachable": True, "can_manage_bots": bool(result.get("can_manage_bots")), "username": str(result.get("username") or "")}

    def get_managed_bot(self, user_id: int, *, rotate=False) -> TelegramBotSnapshot:
        method = "replaceManagedBotToken" if rotate else "getManagedBotToken"
        token = str(self._call(settings.TELEGRAM_MANAGER_BOT_TOKEN, method, {"user_id": user_id}) or "")
        if not token:
            raise TelegramProviderError("managed_bot_token_unavailable")
        return self.validate_existing_bot(token)

    def validate_existing_bot(self, token: str) -> TelegramBotSnapshot:
        result = self._call(token, "getMe")
        if not result.get("is_bot"):
            raise TelegramProviderError("telegram_bot_required")
        return TelegramBotSnapshot(user_id=int(result["id"]), username=str(result.get("username") or ""), name=str(result.get("first_name") or "Telegram bot"), token=token, can_manage_bots=bool(result.get("can_manage_bots")))

    def configure_bot(self, connection, token: str, webhook_secret: str) -> dict:
        base = settings.TELEGRAM_BOT_WEBHOOK_BASE_URL.rstrip("/")
        if not base.startswith("https://"):
            raise TelegramProviderError("telegram_webhook_https_required")
        allowed = list(connection.allowed_updates)
        self._call(token, "setWebhook", {"url": f"{base}/{connection.webhook_public_key}/", "secret_token": webhook_secret, "allowed_updates": allowed, "drop_pending_updates": False})
        command_sets = {
            "en": [("start", "Start support"), ("help", "Show help"), ("human", "Ask for a person"), ("language", "Change language"), ("privacy", "Privacy information")],
            "ru": [("start", "Начать поддержку"), ("help", "Показать помощь"), ("human", "Позвать сотрудника"), ("language", "Сменить язык"), ("privacy", "Конфиденциальность")],
            "uz": [("start", "Yordamni boshlash"), ("help", "Yordamni ko‘rsatish"), ("human", "Xodimni chaqirish"), ("language", "Tilni o‘zgartirish"), ("privacy", "Maxfiylik")],
        }
        for language, commands in command_sets.items():
            self._call(token, "setMyCommands", {"language_code": language, "commands": [{"command": command, "description": description} for command, description in commands]})
        self._call(token, "setMyName", {"name": connection.bot_name})
        return {"webhook": "verified", "commands": list(command_sets)}

    def _token(self, connection):
        return str(connection.channel_connection.get_credentials().get("bot_token") or "")

    def send_text(self, *, connection, chat_id: str, text: str, reply_to_message_id: str = "") -> TelegramSendResult:
        body = {"chat_id": chat_id, "text": text}
        if reply_to_message_id:
            body["reply_parameters"] = {"message_id": int(reply_to_message_id)}
        result = self._call(self._token(connection), "sendMessage", body)
        return TelegramSendResult(message_id=str(result.get("message_id") or ""))

    def health(self, connection) -> dict:
        me = self._call(self._token(connection), "getMe")
        webhook = self._call(self._token(connection), "getWebhookInfo")
        expected = f"{settings.TELEGRAM_BOT_WEBHOOK_BASE_URL.rstrip('/')}/{connection.webhook_public_key}/"
        return {"provider_reachable": True, "bot_matches": int(me.get("id") or 0) == connection.bot_user_id, "webhook_matches": str(webhook.get("url") or "") == expected, "pending_updates": int(webhook.get("pending_update_count") or 0)}

    def get_access_settings(self, connection) -> dict:
        result = self._call(settings.TELEGRAM_MANAGER_BOT_TOKEN, "getManagedBotAccessSettings", {"user_id": connection.bot_user_id})
        return {"is_access_restricted": bool(result.get("is_access_restricted")), "added_user_ids": [int(user["id"]) for user in result.get("added_users", []) if isinstance(user, dict) and user.get("id")]}

    def set_access_settings(self, connection, restricted: bool, user_ids: list[int]) -> dict:
        self._call(settings.TELEGRAM_MANAGER_BOT_TOKEN, "setManagedBotAccessSettings", {"user_id": connection.bot_user_id, "is_access_restricted": restricted, "added_user_ids": user_ids if restricted else []})
        return {"is_access_restricted": restricted, "added_user_ids": user_ids if restricted else []}


def telegram_provider() -> BaseTelegramProvider:
    if settings.TELEGRAM_ENABLE_LIVE:
        return LiveTelegramProvider()
    if settings.TELEGRAM_FAKE_PROVIDER and (settings.DEBUG or settings.TESTING):
        return FakeTelegramProvider()
    raise TelegramProviderError("telegram_provider_disabled")
