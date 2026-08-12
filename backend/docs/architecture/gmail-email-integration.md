# Gmail email integration

## Boundaries

Gmail is an organization-owned channel. `GmailConnection` references the existing
`ChannelConnection`; every notification, sync run, provider message mapping, outbound attempt and
audit event carries the same immutable organization. Provider email identities are unique only
inside the organization and mailbox connection, while an active Google mailbox may be attached to
only one organization at a time.

No generic IMAP, Outlook, SMS, Voice, billing or platform-admin workflow is part of this module.

## Authorization and credentials

The server implements the Google OAuth web-server authorization-code flow with PKCE S256, a
single-use hashed state bound to the authenticated user, membership and safe client redirect. The
only Gmail scope requested is `https://www.googleapis.com/auth/gmail.modify`; offline access and
explicit consent are requested so renewal does not depend on a browser session.

Access and refresh tokens are encrypted in the existing write-only `ChannelConnection` credential
field. Serializers expose only booleans indicating encrypted material exists. Disconnect calls
`users.stop`, clears encrypted credentials and invalidates the local history cursor.

`gmail.modify` is the single centrally configured scope. Metadata-only scopes cannot read customer
message bodies, `gmail.send` cannot read or synchronize threads, and read-only access cannot support
the label/watch and reply workflow. The application does not permanently delete Gmail messages and
therefore never requests `https://mail.google.com/`. Because `gmail.modify` is restricted and the
server stores the CRM copy, production rollout must complete the applicable Google verification
and security-assessment process. The portal checklist reports evidence readiness only; it never
claims Google approval.

## Push and synchronization

`users.watch` subscribes the INBOX label to the configured Google Cloud Pub/Sub topic. The public
push endpoint verifies the Google-signed OIDC bearer token, exact audience, verified service-account
email and configured subscription before reading notification data. A durable unique envelope is
persisted and handed to Celery; duplicates are acknowledged without processing twice.

Notifications carry only a mailbox and history ID. `history.list` performs incremental sync and
pages added-message and relevant label-change records with a hard message bound, then advances the
cursor only after successful ingestion. Duplicate provider message IDs update safe label state but
do not repeat CRM or AI side effects. Out-of-order notifications cannot move the cursor backwards.
An invalid/expired history cursor (Gmail 404) causes a bounded recent full sync. A scheduled
reconciliation handles dropped notifications, daily watch renewal prevents the seven-day watch
expiry, and health checks surface safe machine codes.

Initial sync defaults to recent INBOX mail, bounded by configured days and count. An owner/admin can
choose `from_now`; imported history is marked historical and never enters AI. Initial status,
counts, cancel, retry/full-resync and stuck-sync health are visible. `SPAM` and `TRASH` are excluded
regardless of client input. Notifications for unknown, inactive, disconnected, suspended or
archived organizations fail closed. Celery jobs likewise exclude suspended/archived organizations
from watch, reconciliation and provider health mutations.

## Message safety and automation

MIME parsing is bounded by part, body, header and attachment limits. Encoded headers and declared
charsets are decoded safely; HTML is converted to text while scripts, styles, forms, frames,
tracking pixels and URLs are never rendered. Quoted history and common signatures are removed from
the bounded AI/display body. To, Cc, Reply-To, subject, RFC IDs, internal date, snippet, participants,
labels and attachment metadata are normalized. S/MIME/PGP mail is marked unsupported for AI.
Attachment IDs stay server-side, and authenticated downloads enforce tenant ownership, allowlisted
MIME type, size and file-signature checks with `nosniff` and `no-store` responses. An external
malware scanner/object-store adapter is intentionally not configured in this stage; deployments
that enable broader attachment use must insert one before delivery.

Inbound messages create real CRM contacts, connection-scoped email identities, conversations and
messages. Gmail thread IDs map deterministically to CRM conversations. Replies use the Gmail API
with the original thread ID, matching subject, `In-Reply-To`, `References`, and a private
`X-Althair-Origin` marker. Client idempotency keys prevent duplicate sends.

Automated mail, mailing-list traffic, bounces, messages from the connected mailbox, messages with
the Althair origin marker and historical sync never trigger AI. Suggest mode creates governed
drafts. Gmail autopilot uses only published AI Context and sends only after backend policy approval;
manual employee replies pause AI and supersede stale runs. Gmail data is not used for advertising
or undisclosed model training.

Replies select a validated `Reply-To` when present, preserve To/Cc under server-side normalization,
and record queued/sent/failed attempts. Only the backend can authorize and call Gmail; a model can
only propose. Transient sends use bounded backoff, permanent auth/scope errors fail without an
infinite retry, and provider success is required before CRM reports `sent`.

## Retention and operations

Each connection has a tenant-owned retention period. A bounded scheduled job redacts expired CRM
message bodies, thread headers, participants, snippets and attachment metadata and writes an audit
count. Owner/admin APIs provide tenant-scoped export plus confirmed anonymize or local delete by
contact. Internal notes are never sent to Gmail. Normal operational records contain IDs, states,
counts and safe categories rather than bodies, subjects, mailbox addresses, tokens, MIME, raw
provider payloads or AI prompts.

Portal health exposes encrypted-token presence, exact scope, watch expiry, sync timestamps,
dead-letter/failed notification counts and queued/failed sends. Provider-specific metrics can be
derived from these durable records: connection/watch state, notification and duplicate counts,
sync imports/lag, send outcomes, AI outcomes, handoffs and safe rate-limit categories.
