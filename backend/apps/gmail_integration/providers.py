from __future__ import annotations

import base64
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import timedelta, timezone as dt_timezone
from email.message import EmailMessage

from django.conf import settings
from django.utils import timezone


GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"


class GmailProviderError(Exception):
    def __init__(self, code: str, *, transient: bool = False, status_code: int = 409):
        super().__init__(code)
        self.code = code
        self.transient = transient
        self.status_code = status_code


class GmailHistoryExpired(GmailProviderError):
    def __init__(self):
        super().__init__("history_cursor_expired")


@dataclass(frozen=True)
class GmailOAuthSnapshot:
    access_token: str
    refresh_token: str
    expires_in: int
    scope: tuple[str, ...]
    email: str
    name: str
    google_user_id: str


@dataclass(frozen=True)
class GmailSendResult:
    message_id: str
    thread_id: str
    request_id: str


class BaseGmailProvider:
    def exchange_code(self, *, code: str, code_verifier: str) -> GmailOAuthSnapshot:
        raise NotImplementedError

    def start_watch(self, connection) -> dict:
        raise NotImplementedError

    def stop_watch(self, connection) -> None:
        raise NotImplementedError

    def list_recent(self, connection, *, limit: int) -> tuple[list[dict], str]:
        raise NotImplementedError

    def list_history(self, connection, *, start_history_id: str, limit: int) -> tuple[list[str], str]:
        raise NotImplementedError

    def get_message(self, connection, message_id: str) -> dict:
        raise NotImplementedError

    def send_reply(self, connection, *, thread_id: str, raw_message: str) -> GmailSendResult:
        raise NotImplementedError

    def get_attachment(self, connection, *, message_id: str, attachment_id: str) -> bytes:
        raise NotImplementedError

    def health(self, connection) -> dict:
        raise NotImplementedError


class FakeGmailProvider(BaseGmailProvider):
    CODE_PATTERN = re.compile(r"^fake_connect:([A-Za-z0-9_.+\-]+@[A-Za-z0-9.\-]+):([A-Za-z0-9_.\-]{1,80})$")

    def exchange_code(self, *, code: str, code_verifier: str) -> GmailOAuthSnapshot:
        match = self.CODE_PATTERN.fullmatch(code or "")
        if not match or len(code_verifier) < 43:
            raise GmailProviderError("invalid_fake_authorization_code")
        email, name = match.groups()
        digest = hashlib.sha256(f"{code}:{code_verifier}".encode()).hexdigest()
        return GmailOAuthSnapshot(
            access_token=f"fake-access-{digest}",
            refresh_token=f"fake-refresh-{digest[::-1]}",
            expires_in=3600,
            scope=(GMAIL_MODIFY_SCOPE,),
            email=email.casefold(),
            name=name.replace("_", " ").title(),
            google_user_id=f"fake-google-{hashlib.sha256(email.casefold().encode()).hexdigest()[:24]}",
        )

    def start_watch(self, connection) -> dict:
        return {
            "history_id": connection.history_id or "1000",
            "expiration": timezone.now() + timedelta(days=7),
        }

    def stop_watch(self, connection) -> None:
        return None

    def list_recent(self, connection, *, limit: int) -> tuple[list[dict], str]:
        return [], connection.history_id or "1000"

    def list_history(self, connection, *, start_history_id: str, limit: int) -> tuple[list[str], str]:
        if start_history_id == "expired":
            raise GmailHistoryExpired()
        pending = list(connection.channel_connection.configuration.get("fake_pending_message_ids") or [])
        return pending[:limit], str(max(int(start_history_id or 0), int(connection.channel_connection.configuration.get("fake_history_id") or start_history_id or 1000)))

    def get_message(self, connection, message_id: str) -> dict:
        items = connection.channel_connection.configuration.get("fake_messages") or {}
        payload = items.get(message_id)
        if not isinstance(payload, dict):
            raise GmailProviderError("gmail_message_not_found")
        return payload

    def send_reply(self, connection, *, thread_id: str, raw_message: str) -> GmailSendResult:
        if "[gmail-transient-error]" in raw_message.casefold():
            raise GmailProviderError("provider_temporarily_unavailable", transient=True)
        digest = hashlib.sha256(f"{connection.id}:{thread_id}:{raw_message}".encode()).hexdigest()
        return GmailSendResult(
            message_id=f"gmail_fake_{digest[:24]}",
            thread_id=thread_id,
            request_id=f"fake_request_{digest[24:40]}",
        )

    def get_attachment(self, connection, *, message_id: str, attachment_id: str) -> bytes:
        payload = self.get_message(connection, message_id)
        stack = [payload.get("payload") or {}]
        while stack:
            part = stack.pop()
            stack.extend(part.get("parts") or [])
            body = part.get("body") or {}
            if str(body.get("attachmentId") or "") == attachment_id:
                data = str(body.get("data") or "")
                padded = data + "=" * (-len(data) % 4)
                decoded = base64.urlsafe_b64decode(padded.encode()) if data else b""
                if len(decoded) > settings.GOOGLE_GMAIL_MAX_ATTACHMENT_BYTES:
                    raise GmailProviderError("attachment_too_large", status_code=413)
                return decoded
        raise GmailProviderError("attachment_not_found", status_code=404)

    def health(self, connection) -> dict:
        state = str(
            connection.channel_connection.configuration.get("fake_health_state")
            or "healthy"
        )
        if state == "revoked":
            raise GmailProviderError("token_refresh_failed")
        if state == "permission_missing":
            raise GmailProviderError("gmail_permission_missing")
        if state == "degraded":
            raise GmailProviderError("provider_temporarily_unavailable", transient=True)
        return {"provider_reachable": True, "mailbox_matches": True, "scope_valid": GMAIL_MODIFY_SCOPE in connection.scope_snapshot}


class LiveGmailProvider(BaseGmailProvider):
    timeout = 25

    def _request(self, url, *, method="GET", token="", body=None, form=None):
        data = None
        headers = {"Accept": "application/json", "User-Agent": "Althair-Gmail/1.0"}
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                max_response = (
                    settings.GOOGLE_GMAIL_MAX_ATTACHMENT_BYTES * 2 + 10_000
                    if "/attachments/" in url
                    else 2_000_000
                )
                content = response.read(max_response)
                return json.loads(content.decode()) if content else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and "/history" in url:
                raise GmailHistoryExpired() from exc
            transient = exc.code == 429 or 500 <= exc.code < 600
            if exc.code == 401 or (exc.code == 400 and "oauth2.googleapis.com/token" in url):
                code = "token_refresh_failed"
            elif exc.code == 403:
                code = "gmail_permission_missing"
            elif exc.code == 429:
                code = "provider_rate_limited"
            else:
                code = "provider_request_rejected"
            raise GmailProviderError(
                code,
                transient=transient,
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GmailProviderError("provider_temporarily_unavailable", transient=True) from exc

    def _token(self, connection) -> str:
        credentials = connection.channel_connection.get_credentials()
        refresh = str(credentials.get("refresh_token") or "")
        access = str(credentials.get("access_token") or "")
        if not refresh:
            raise GmailProviderError("refresh_token_missing")
        if connection.token_expires_at and connection.token_expires_at > timezone.now() + timedelta(minutes=2) and access:
            return access
        result = self._request(
            "https://oauth2.googleapis.com/token",
            method="POST",
            form={
                "client_id": settings.GOOGLE_GMAIL_CLIENT_ID,
                "client_secret": settings.GOOGLE_GMAIL_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": refresh,
            },
        )
        token = str(result.get("access_token") or "")
        if not token:
            raise GmailProviderError("token_refresh_failed")
        credentials["access_token"] = token
        connection.channel_connection.set_credentials(credentials)
        connection.channel_connection.save(update_fields=["encrypted_credentials", "updated_at"])
        connection.token_expires_at = timezone.now() + timedelta(seconds=int(result.get("expires_in") or 3600))
        connection.save(update_fields=["token_expires_at", "updated_at"])
        return token

    def exchange_code(self, *, code: str, code_verifier: str) -> GmailOAuthSnapshot:
        result = self._request(
            "https://oauth2.googleapis.com/token",
            method="POST",
            form={
                "client_id": settings.GOOGLE_GMAIL_CLIENT_ID,
                "client_secret": settings.GOOGLE_GMAIL_CLIENT_SECRET,
                "code": code,
                "code_verifier": code_verifier,
                "grant_type": "authorization_code",
                "redirect_uri": settings.GOOGLE_GMAIL_REDIRECT_URI,
            },
        )
        access = str(result.get("access_token") or "")
        refresh = str(result.get("refresh_token") or "")
        scopes = tuple(str(result.get("scope") or "").split())
        if not access or GMAIL_MODIFY_SCOPE not in scopes:
            raise GmailProviderError("oauth_scope_or_offline_token_missing")
        profile = self._request(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile", token=access
        )
        email = str(profile.get("emailAddress") or "").casefold()
        if not email:
            raise GmailProviderError("mailbox_profile_missing")
        return GmailOAuthSnapshot(
            access_token=access,
            refresh_token=refresh,
            expires_in=int(result.get("expires_in") or 3600),
            scope=scopes,
            email=email,
            name=email.split("@", 1)[0],
            google_user_id=email,
        )

    def start_watch(self, connection) -> dict:
        included = connection.included_label_ids or ["INBOX"]
        result = self._request(
            "https://gmail.googleapis.com/gmail/v1/users/me/watch",
            method="POST",
            token=self._token(connection),
            body={
                "topicName": settings.GOOGLE_GMAIL_PUBSUB_TOPIC,
                "labelIds": included,
                "labelFilterBehavior": "include",
            },
        )
        expiration_ms = int(result.get("expiration") or 0)
        return {
            "history_id": str(result.get("historyId") or ""),
            "expiration": timezone.datetime.fromtimestamp(expiration_ms / 1000, tz=dt_timezone.utc),
        }

    def stop_watch(self, connection) -> None:
        self._request(
            "https://gmail.googleapis.com/gmail/v1/users/me/stop",
            method="POST",
            token=self._token(connection),
            body={},
        )

    def list_recent(self, connection, *, limit: int) -> tuple[list[dict], str]:
        token = self._token(connection)
        params = {
            "maxResults": min(limit, 500),
            "labelIds": connection.included_label_ids or ["INBOX"],
            "q": f"newer_than:{settings.GOOGLE_GMAIL_INITIAL_SYNC_DAYS}d",
        }
        listing = self._request(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages?"
            + urllib.parse.urlencode(params, doseq=True),
            token=token,
        )
        messages = [self.get_message(connection, str(item.get("id"))) for item in listing.get("messages") or [] if item.get("id")]
        profile = self._request("https://gmail.googleapis.com/gmail/v1/users/me/profile", token=token)
        return messages, str(profile.get("historyId") or connection.history_id)

    def list_history(self, connection, *, start_history_id: str, limit: int) -> tuple[list[str], str]:
        token = self._token(connection)
        ids: list[str] = []
        page_token = ""
        end_history_id = start_history_id
        while len(ids) < limit:
            params = {
                "startHistoryId": start_history_id,
                "labelId": (connection.included_label_ids or ["INBOX"])[0],
                "maxResults": min(limit - len(ids), 500),
            }
            if page_token:
                params["pageToken"] = page_token
            url = (
                "https://gmail.googleapis.com/gmail/v1/users/me/history?"
                + urllib.parse.urlencode(params)
            )
            result = self._request(url, token=token)
            end_history_id = str(result.get("historyId") or end_history_id)
            for history in result.get("history") or []:
                changes = [
                    *(history.get("messagesAdded") or []),
                    *(history.get("labelsAdded") or []),
                    *(history.get("labelsRemoved") or []),
                ]
                for item in changes:
                    message_id = str((item.get("message") or {}).get("id") or "")
                    if message_id and message_id not in ids:
                        ids.append(message_id)
                        if len(ids) >= limit:
                            break
                if len(ids) >= limit:
                    break
            page_token = str(result.get("nextPageToken") or "")
            if not page_token:
                break
        return ids, end_history_id

    def get_message(self, connection, message_id: str) -> dict:
        return self._request(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{urllib.parse.quote(message_id)}?format=full",
            token=self._token(connection),
        )

    def send_reply(self, connection, *, thread_id: str, raw_message: str) -> GmailSendResult:
        raw = base64.urlsafe_b64encode(raw_message.encode()).decode().rstrip("=")
        result = self._request(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            method="POST",
            token=self._token(connection),
            body={"raw": raw, "threadId": thread_id},
        )
        message_id = str(result.get("id") or "")
        if not message_id:
            raise GmailProviderError("provider_invalid_response")
        return GmailSendResult(message_id, str(result.get("threadId") or thread_id), "")

    def get_attachment(self, connection, *, message_id: str, attachment_id: str) -> bytes:
        result = self._request(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{urllib.parse.quote(message_id)}/attachments/{urllib.parse.quote(attachment_id)}",
            token=self._token(connection),
        )
        data = str(result.get("data") or "")
        if not data:
            raise GmailProviderError("attachment_not_found", status_code=404)
        padded = data + "=" * (-len(data) % 4)
        try:
            decoded = base64.urlsafe_b64decode(padded.encode())
        except ValueError as exc:
            raise GmailProviderError("attachment_invalid") from exc
        if len(decoded) > settings.GOOGLE_GMAIL_MAX_ATTACHMENT_BYTES:
            raise GmailProviderError("attachment_too_large", status_code=413)
        return decoded

    def health(self, connection) -> dict:
        profile = self._request(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile", token=self._token(connection)
        )
        return {
            "provider_reachable": True,
            "mailbox_matches": str(profile.get("emailAddress") or "").casefold() == connection.mailbox_email_normalized,
            "scope_valid": GMAIL_MODIFY_SCOPE in connection.scope_snapshot,
        }


def gmail_provider() -> BaseGmailProvider:
    if settings.GOOGLE_GMAIL_ENABLE_LIVE:
        return LiveGmailProvider()
    if settings.GOOGLE_GMAIL_FAKE_PROVIDER and (settings.DEBUG or settings.TESTING or settings.E2E_TESTING):
        return FakeGmailProvider()
    raise GmailProviderError("gmail_provider_disabled")


def build_rfc_reply(
    *,
    mailbox_email: str,
    recipient: str,
    subject: str,
    body: str,
    in_reply_to: str,
    references: str,
    origin_id: str,
    cc: tuple[str, ...] = (),
) -> str:
    message = EmailMessage()
    message["From"] = mailbox_email
    message["To"] = recipient
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject if subject.casefold().startswith("re:") else f"Re: {subject or '(no subject)'}"
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    combined = " ".join(filter(None, [references, in_reply_to])).strip()
    if combined:
        message["References"] = combined[-4000:]
    message["X-Althair-Origin"] = origin_id
    message["Auto-Submitted"] = "no"
    message.set_content(body)
    return message.as_string()
