# SMS messaging architecture

## Scope and tenant boundary

`sms` is a dedicated provider module layered onto the existing organization-owned
`ChannelConnection`, CRM, and AI Runtime. An `SMSConnection` has one immutable organization and one
SMS channel. Every consent record, webhook receipt, outbound attempt, delivery event, and audit
event repeats that organization boundary. A sender can have only one active tenant connection and
an organization can have only one active SMS connection.

The public webhook route contains an opaque random connection key. It is only a candidate lookup:
the tenant is accepted after the official Twilio SDK verifies the signature over the exact public
URL and every submitted parameter, and the normalized destination or Messaging Service SID matches
that verified connection. Organization headers are ignored. Unknown, inactive, disconnected, or
suspended connections fail closed. Raw account IDs, phone numbers, or tenant IDs are not routing
credentials.

Voice, SIP, WhatsApp, campaigns, bulk blasting, billing, carrier registration procurement, and
production deployment are outside this stage.

## Provider and credentials

`SMSProvider` exposes only health and send operations. `FakeSMSProvider` is deterministic and makes
no network request; it is the default for local development and CI. `TwilioSMSProvider` uses the
official Python SDK for account, Messaging Service/SMS-capable sender health, message creation, and
request validation.

Platform-managed mode reads Twilio credentials only from deployment secrets. Customer-owned mode
stores an Account SID plus an encrypted Auth Token and/or API-key secret using the repository's
encrypted field. API responses expose presence booleans, never the secret value. Rotation is a
write-only owner/admin action. Secrets do not enter URLs, normal logs, audit metadata, fixtures,
screenshots, frontend state returned by the API, or migrations.

Live Twilio remains disabled until `SMS_ENABLE_LIVE=true`, credentials are complete, and the public
base URL is HTTPS. A connection health result reports safe booleans, machine codes, callback URLs,
dead-letter counts, configured limits, and timestamps without claiming provider approval or quota.

## Inbound and consent lifecycle

The inbound and status endpoints persist bounded normalized envelopes before asynchronous
processing. `MessageSid` is the inbound idempotency key; status events hash the SID, state, error,
addresses, and Messaging Service. Duplicate envelopes acknowledge safely without repeating CRM or
AI effects. The durable receipt contains only fields needed for processing and the inbound body is
redacted after successful CRM ingestion. MMS is represented by bounded count metadata; this stage
does not download media.

Normalized E.164 numbers create connection-scoped phone identities, contacts, CRM conversations,
and real inbound messages. Inbound support establishes only the configured support consent state.
STOP, START, and HELP are parsed case-insensitively. When Twilio Advanced Opt-Out is enabled,
verified `OptOutType` is authoritative: a local START cannot override a provider STOP. STOP and
HELP never invoke AI. Employee blocks are immediate, while an employee cannot silently reverse a
provider opt-out. Every outbound path locks and rechecks consent; opted-out, blocked, invalid, or
unknown recipients never reach the provider.

Compliance copy is a product draft pending local counsel, carrier, and jurisdiction review.

## Outbound, delivery, and segmentation

Manual and AI replies share `send_sms_message`; the model cannot call Twilio directly. The backend
rechecks organization status, sender health, consent, phone normalization, country policy, rate and
daily quotas, repeated content, and segment limits. A client message ID makes outbound creation
idempotent. The organization-owned connection row and a bounded cache lock serialize provider
sends.

GSM-7 basic and extension tables are counted accurately; other text uses UTF-16 code units for
UCS-2-style limits. The UI and backend use 160/153 GSM-7 and 70/67 Unicode thresholds. Human text
over the warning threshold requires confirmation and hard limits still apply. AI has a smaller
hard limit. Provider-reported segment counts replace estimates when present.

Twilio status callbacks move CRM messages monotonically through queued, sending, sent, delivered,
undelivered, failed, or canceled. Duplicate and out-of-order callbacks cannot move a terminal state
backwards. Ordinary SMS has no read receipt: a provider `read` value is recorded only as an ignored
receipt and is never displayed as read.

## AI Runtime

Connection modes map to manual, suggest, or `autopilot_sms`. Suggest creates a governed draft that
an employee can approve/edit/reject through the existing AI Runtime. Autopilot additionally
requires a published tenant AI Context, enabled runtime, eligible conversation state, active
sender, and sendable consent. STOP/START/HELP and failed delivery suppress AI. A human reply pauses
automation and supersedes stale active runs. AI provider failure or SMS provider rejection leaves
an explicit failed run/message rather than inventing delivery.

## Reliability, fraud, and privacy

Controls include signed-webhook throttling, per-recipient inbound flood limits, organization and
recipient send rates, daily message/segment budgets, duplicate-content windows, sender serialization,
a bounded circuit breaker, configurable country allow/deny policy, and a provider-independent fraud
policy interface. Only transient, non-number errors are automatically retried, with bounded
exponential delay and durable next-retry state. Permanent number/opt-out errors, exhausted attempts,
policy changes, a newer human reply, or high-risk/blocked destinations stop retry. Owners/admins can
request a retry only for an existing failed tenant-owned attempt; health exposes failed receipt and
send counts for dead-letter operations.

Tenant-scoped export and confirmed anonymize/delete cover SMS messages and identities. Consent
suppression must be retained where law or carrier rules require it so deletion does not cause an
accidental future send; deployments should adapt retention to local requirements. Internal notes
are never sent. Message bodies are not placed in normal logs, used for advertising, or used for
hidden model training.
