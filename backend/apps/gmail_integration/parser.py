from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser


MAX_BODY = 100_000
MAX_PARTS = 100
MAX_ATTACHMENTS = 20


def decode_base64url(value: str, *, max_bytes: int = MAX_BODY * 2) -> bytes:
    if not isinstance(value, str) or len(value) > max_bytes * 2:
        raise ValueError("gmail_part_too_large")
    padded = value + "=" * (-len(value) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError("gmail_part_invalid") from exc
    if len(decoded) > max_bytes:
        raise ValueError("gmail_part_too_large")
    return decoded


def _header_text(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))[:2000]
    except (LookupError, UnicodeError):
        return str(value)[:2000]


class _SafeHTMLText(HTMLParser):
    blocked = {"script", "style", "form", "svg", "iframe", "object"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.casefold() in self.blocked:
            self.depth += 1
        elif not self.depth and tag.casefold() in {"p", "div", "br", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag.casefold() in self.blocked and self.depth:
            self.depth -= 1
        elif not self.depth and tag.casefold() in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.depth:
            self.parts.append(data)


def sanitize_html_to_text(value: str) -> str:
    parser = _SafeHTMLText()
    parser.feed(value[: MAX_BODY * 2])
    return re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()[:MAX_BODY]


def strip_quoted_history(value: str) -> str:
    lines = value.replace("\r\n", "\n").split("\n")
    kept: list[str] = []
    quote_patterns = (
        re.compile(r"^On .+wrote:$", re.I),
        re.compile(r"^-{2,}\s*Original Message\s*-{2,}$", re.I),
        re.compile(r"^_{5,}$"),
    )
    for line in lines:
        stripped = line.strip()
        if (
            line.startswith(">")
            or stripped == "--"
            or stripped.casefold().startswith("sent from my ")
            or any(pattern.match(stripped) for pattern in quote_patterns)
        ):
            break
        kept.append(line)
    return "\n".join(kept).strip()[:MAX_BODY]


@dataclass(frozen=True)
class ParsedGmailMessage:
    gmail_message_id: str
    gmail_thread_id: str
    history_id: str
    sender_email: str
    sender_name: str
    to_recipients: tuple[str, ...]
    cc_recipients: tuple[str, ...]
    reply_to: str
    subject: str
    rfc_message_id: str
    in_reply_to: str
    references: str
    body: str
    occurred_at: object
    label_ids: tuple[str, ...]
    snippet: str
    attachments: tuple[dict, ...]
    is_automated: bool
    is_encrypted: bool
    is_from_self: bool
    has_althair_origin: bool


def parse_gmail_message(payload: dict, *, mailbox_email: str) -> ParsedGmailMessage:
    if not isinstance(payload, dict):
        raise ValueError("gmail_message_invalid")
    gmail_message_id = str(payload.get("id") or "")[:255]
    thread_id = str(payload.get("threadId") or "")[:255]
    if not gmail_message_id or not thread_id:
        raise ValueError("gmail_message_identity_missing")
    message_payload = payload.get("payload") or {}
    headers = {}
    for item in message_payload.get("headers") or []:
        if isinstance(item, dict) and item.get("name"):
            headers[str(item["name"]).casefold()] = _header_text(str(item.get("value") or ""))
    senders = getaddresses([headers.get("from", "")])
    sender_name, sender_email = senders[0] if senders else ("", "")
    sender_email = sender_email.strip().casefold()[:320]
    if not sender_email:
        raise ValueError("gmail_sender_missing")
    to_recipients = tuple(
        address.strip().casefold()[:320]
        for _, address in getaddresses([headers.get("to", "")])
        if address.strip()
    )[:50]
    cc_recipients = tuple(
        address.strip().casefold()[:320]
        for _, address in getaddresses([headers.get("cc", "")])
        if address.strip()
    )[:50]
    reply_to_addresses = getaddresses([headers.get("reply-to", "")])
    reply_to = (
        reply_to_addresses[0][1].strip().casefold()[:320]
        if reply_to_addresses and reply_to_addresses[0][1].strip()
        else ""
    )
    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict] = []
    stack = [message_payload]
    visited = 0
    is_encrypted = False
    while stack and visited < MAX_PARTS:
        part = stack.pop()
        visited += 1
        if not isinstance(part, dict):
            continue
        stack.extend(reversed(part.get("parts") or []))
        mime = str(part.get("mimeType") or "").casefold()
        if mime in {"application/pkcs7-mime", "application/pgp-encrypted"}:
            is_encrypted = True
        filename = _header_text(str(part.get("filename") or "")).strip()[:255]
        body_meta = part.get("body") or {}
        attachment_id = str(body_meta.get("attachmentId") or "")[:255]
        if filename or attachment_id:
            if len(attachments) < MAX_ATTACHMENTS:
                attachments.append(
                    {
                        "filename": filename or "attachment",
                        "mime_type": mime[:120],
                        "size": min(int(body_meta.get("size") or 0), 100_000_000),
                        "attachment_id": attachment_id,
                    }
                )
            continue
        data = body_meta.get("data")
        if not data:
            continue
        part_headers = {
            str(item.get("name") or "").casefold(): str(item.get("value") or "")
            for item in part.get("headers") or []
            if isinstance(item, dict)
        }
        charset_match = re.search(
            r"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)",
            part_headers.get("content-type", ""),
            re.I,
        )
        charset = charset_match.group(1) if charset_match else "utf-8"
        try:
            decoded = decode_base64url(data).decode(charset, errors="replace")
        except LookupError:
            decoded = decode_base64url(data).decode("utf-8", errors="replace")
        if mime == "text/plain":
            text_parts.append(decoded)
        elif mime == "text/html":
            html_parts.append(decoded)
    raw_body = "\n".join(text_parts).strip() or sanitize_html_to_text("\n".join(html_parts))
    body = strip_quoted_history(raw_body) or "Email received without a text body."
    auto_submitted = headers.get("auto-submitted", "").casefold()
    precedence = headers.get("precedence", "").casefold()
    content_type = headers.get("content-type", "").casefold()
    sender_local = sender_email.split("@", 1)[0]
    is_automated = bool(
        (auto_submitted and auto_submitted != "no")
        or precedence in {"bulk", "list", "junk"}
        or headers.get("list-id")
        or headers.get("list-unsubscribe")
        or "delivery-status" in content_type
        or sender_local in {"mailer-daemon", "postmaster", "no-reply", "noreply"}
    )
    occurred_at = None
    if headers.get("date"):
        try:
            occurred_at = parsedate_to_datetime(headers["date"])
        except (TypeError, ValueError, OverflowError):
            pass
    if occurred_at is None and payload.get("internalDate"):
        try:
            occurred_at = datetime.fromtimestamp(
                int(payload["internalDate"]) / 1000,
                tz=timezone.utc,
            )
        except (TypeError, ValueError, OverflowError):
            pass
    return ParsedGmailMessage(
        gmail_message_id=gmail_message_id,
        gmail_thread_id=thread_id,
        history_id=str(payload.get("historyId") or "")[:64],
        sender_email=sender_email,
        sender_name=sender_name.strip()[:200] or sender_email,
        to_recipients=to_recipients,
        cc_recipients=cc_recipients,
        reply_to=reply_to,
        subject=headers.get("subject", "")[:500],
        rfc_message_id=headers.get("message-id", "")[:500],
        in_reply_to=headers.get("in-reply-to", "")[:500],
        references=headers.get("references", "")[:4000],
        body=body,
        occurred_at=occurred_at,
        label_ids=tuple(str(value)[:120] for value in payload.get("labelIds") or [])[:100],
        snippet=str(payload.get("snippet") or "")[:500],
        attachments=tuple(attachments),
        is_automated=is_automated,
        is_encrypted=is_encrypted,
        is_from_self=sender_email == mailbox_email.strip().casefold(),
        has_althair_origin=bool(headers.get("x-althair-origin")),
    )
