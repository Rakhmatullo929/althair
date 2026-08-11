from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings


REQUIRED_PERMISSIONS = (
    "instagram_business_basic",
    "instagram_business_manage_messages",
)


class InstagramProviderError(Exception):
    def __init__(self, code: str, *, transient: bool = False):
        super().__init__(code)
        self.code = code
        self.transient = transient


@dataclass(frozen=True)
class InstagramAccountSnapshot:
    instagram_user_id: str
    username: str
    account_type: str
    profile_name: str
    profile_picture_url: str
    access_token: str
    expires_in: int
    permissions: tuple[str, ...]


@dataclass(frozen=True)
class InstagramSendResult:
    message_id: str
    request_id: str


class BaseInstagramProvider:
    def exchange_code(self, code: str) -> InstagramAccountSnapshot:
        raise NotImplementedError

    def send_text(
        self,
        *,
        connection,
        recipient_id: str,
        text: str,
        human_agent: bool,
        reply_to_message_id: str = "",
    ) -> InstagramSendResult:
        raise NotImplementedError

    def health(self, connection) -> dict:
        raise NotImplementedError


class FakeInstagramProvider(BaseInstagramProvider):
    CODE_PATTERN = re.compile(
        r"^fake_connect:([A-Za-z0-9_.-]{3,120}):([A-Za-z0-9_.-]{1,80})(?::(BUSINESS|CREATOR))?$"
    )

    def exchange_code(self, code: str) -> InstagramAccountSnapshot:
        match = self.CODE_PATTERN.fullmatch(code or "")
        if not match:
            raise InstagramProviderError("invalid_fake_authorization_code")
        account_id, username, account_type = match.groups()
        return InstagramAccountSnapshot(
            instagram_user_id=account_id,
            username=username,
            account_type=account_type or "BUSINESS",
            profile_name=username.replace("_", " ").title(),
            profile_picture_url="",
            access_token=f"fake-access-{hashlib.sha256(code.encode()).hexdigest()}",
            expires_in=60 * 24 * 60 * 60,
            permissions=REQUIRED_PERMISSIONS,
        )

    def send_text(
        self,
        *,
        connection,
        recipient_id: str,
        text: str,
        human_agent: bool,
        reply_to_message_id: str = "",
    ) -> InstagramSendResult:
        lowered = text.casefold()
        if "[meta-transient-error]" in lowered:
            raise InstagramProviderError("provider_temporarily_unavailable", transient=True)
        if "[meta-policy-error]" in lowered:
            raise InstagramProviderError("provider_policy_rejected")
        digest = hashlib.sha256(
            f"{connection.id}:{recipient_id}:{text}:{human_agent}:{reply_to_message_id}".encode()
        ).hexdigest()
        return InstagramSendResult(
            message_id=f"ig_fake_{digest[:24]}",
            request_id=f"fake_request_{digest[24:40]}",
        )

    def health(self, connection) -> dict:
        return {
            "provider_reachable": True,
            "account_matches": True,
            "permissions": list(connection.permission_snapshot),
        }


class LiveInstagramProvider(BaseInstagramProvider):
    timeout = 20

    def _json_request(self, url: str, *, method: str = "GET", body=None, token: str = "") -> dict:
        data = None
        headers = {"Accept": "application/json", "User-Agent": "Althair-Instagram/1.0"}
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read(1_000_000).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise InstagramProviderError("provider_invalid_response")
                return payload
        except urllib.error.HTTPError as exc:
            transient = exc.code == 429 or 500 <= exc.code < 600
            raise InstagramProviderError(
                "provider_rate_limited" if exc.code == 429 else "provider_request_rejected",
                transient=transient,
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise InstagramProviderError("provider_temporarily_unavailable", transient=True) from exc

    def exchange_code(self, code: str) -> InstagramAccountSnapshot:
        required = (
            settings.META_APP_ID,
            settings.META_APP_SECRET,
            settings.META_INSTAGRAM_REDIRECT_URI,
            settings.META_INSTAGRAM_GRAPH_API_VERSION,
        )
        if not all(required):
            raise InstagramProviderError("meta_live_configuration_incomplete")
        form = urllib.parse.urlencode(
            {
                "client_id": settings.META_APP_ID,
                "client_secret": settings.META_APP_SECRET,
                "grant_type": "authorization_code",
                "redirect_uri": settings.META_INSTAGRAM_REDIRECT_URI,
                "code": code,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://api.instagram.com/oauth/access_token",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                short = json.loads(response.read(1_000_000).decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            raise InstagramProviderError("oauth_code_exchange_failed") from exc
        short_token = str(short.get("access_token", ""))
        if not short_token:
            raise InstagramProviderError("oauth_code_exchange_failed")
        long_url = "https://graph.instagram.com/access_token?" + urllib.parse.urlencode(
            {
                "grant_type": "ig_exchange_token",
                "client_secret": settings.META_APP_SECRET,
                "access_token": short_token,
            }
        )
        long_lived = self._json_request(long_url)
        token = str(long_lived.get("access_token", ""))
        if not token:
            raise InstagramProviderError("oauth_token_exchange_failed")
        version = settings.META_INSTAGRAM_GRAPH_API_VERSION
        profile = self._json_request(
            f"https://graph.instagram.com/{version}/me?"
            + urllib.parse.urlencode(
                {"fields": "user_id,username,name,account_type,profile_picture_url"}
            ),
            token=token,
        )
        permission_payload = self._json_request(
            f"https://graph.instagram.com/{version}/me/permissions",
            token=token,
        )
        granted_permissions = tuple(
            str(item.get("permission"))
            for item in (permission_payload.get("data") or [])
            if isinstance(item, dict)
            and item.get("status") == "granted"
            and item.get("permission")
        )
        account_id = str(profile.get("user_id") or profile.get("id") or "")
        username = str(profile.get("username") or "")
        if not account_id or not username:
            raise InstagramProviderError("professional_account_required")
        return InstagramAccountSnapshot(
            instagram_user_id=account_id,
            username=username,
            account_type=str(profile.get("account_type") or "PROFESSIONAL"),
            profile_name=str(profile.get("name") or "")[:200],
            profile_picture_url=str(profile.get("profile_picture_url") or "")[:1000],
            access_token=token,
            expires_in=int(long_lived.get("expires_in") or 0),
            permissions=granted_permissions,
        )

    def send_text(
        self,
        *,
        connection,
        recipient_id: str,
        text: str,
        human_agent: bool,
        reply_to_message_id: str = "",
    ) -> InstagramSendResult:
        credentials = connection.channel_connection.get_credentials()
        token = str(credentials.get("access_token", ""))
        if not token:
            raise InstagramProviderError("access_token_missing")
        version = connection.graph_api_version or settings.META_INSTAGRAM_GRAPH_API_VERSION
        body = {"recipient": {"id": recipient_id}, "message": {"text": text}}
        if human_agent:
            body["tag"] = "HUMAN_AGENT"
        if reply_to_message_id:
            body["message"]["reply_to"] = {"mid": reply_to_message_id}
        payload = self._json_request(
            f"https://graph.instagram.com/{version}/{connection.instagram_user_id}/messages",
            method="POST",
            body=body,
            token=token,
        )
        message_id = str(payload.get("message_id") or payload.get("id") or "")
        if not message_id:
            raise InstagramProviderError("provider_invalid_response")
        return InstagramSendResult(
            message_id=message_id,
            request_id=str(payload.get("request_id") or "")[:120],
        )

    def health(self, connection) -> dict:
        credentials = connection.channel_connection.get_credentials()
        token = str(credentials.get("access_token", ""))
        if not token:
            raise InstagramProviderError("access_token_missing")
        version = connection.graph_api_version or settings.META_INSTAGRAM_GRAPH_API_VERSION
        payload = self._json_request(
            f"https://graph.instagram.com/{version}/me?fields=user_id,username",
            token=token,
        )
        account_id = str(payload.get("user_id") or payload.get("id") or "")
        permission_payload = self._json_request(
            f"https://graph.instagram.com/{version}/me/permissions",
            token=token,
        )
        permissions = [
            str(item.get("permission"))
            for item in (permission_payload.get("data") or [])
            if isinstance(item, dict)
            and item.get("status") == "granted"
            and item.get("permission")
        ]
        return {
            "provider_reachable": True,
            "account_matches": account_id == connection.instagram_user_id,
            "permissions": permissions,
        }


def instagram_provider() -> BaseInstagramProvider:
    if settings.META_INSTAGRAM_ENABLE_LIVE:
        return LiveInstagramProvider()
    return FakeInstagramProvider()
