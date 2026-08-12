# SMS messaging API

All management routes require an authenticated organization context. Safe reads follow the common
organization membership policy; connection, credential, and retry mutations require owner/admin
`manage_channels` permission. Suspended or archived organizations are read-only.

## Connection routes

- `GET /api/v1/integrations/sms/readiness/`
- `GET|POST /api/v1/integrations/sms/connections/`
- `GET|PATCH /api/v1/integrations/sms/{id}/`
- `GET|POST /api/v1/integrations/sms/{id}/health/`
- `POST /api/v1/integrations/sms/{id}/test/` (fake provider only)
- `POST /api/v1/integrations/sms/{id}/rotate-credentials/`
- `POST /api/v1/integrations/sms/{id}/pause/`
- `POST /api/v1/integrations/sms/{id}/activate/`
- `POST /api/v1/integrations/sms/{id}/disconnect/`
- `POST /api/v1/integrations/sms/{id}/retry/`
- `POST /api/v1/integrations/sms/{id}/consent/`
- `GET|POST /api/v1/integrations/sms/{id}/privacy/`

Creation accepts `fake` or `twilio`, `platform_managed` or `customer_owned`, a normalized sender,
optional Account/Messaging Service/phone/API-key SIDs, write-only secret values, supported
languages, Advanced Opt-Out, inbound-support policy, and AI mode. Secret fields never appear in a
response. Live creation is rejected while live mode is disabled or provider health cannot validate
the configured account and sender capability.

Health includes provider/sender/Messaging Service reachability, exact callback URLs, public HTTPS
and signature readiness, callback timestamps, consent mode, safe error code, dead-letter counts,
rate/segment limits, and country policy. It does not expose raw provider errors or credentials.

The retry route accepts `message_id`. It only schedules a failed attempt owned by the same
connection and tenant, remains bounded by the configured maximum, rechecks policy and consent, and
is canceled by a newer outbound reply.

## Provider webhooks

- `POST /api/v1/webhooks/twilio/sms/{opaque_key}/inbound/`
- `POST /api/v1/webhooks/twilio/sms/{opaque_key}/status/`

Both routes require `X-Twilio-Signature`. Form requests are validated using every submitted value.
JSON requests require Twilio's `bodySHA256` query parameter and are validated against the exact raw
body. The server reconstructs the external URL from the configured public base; reverse proxies
must preserve path and query exactly. The opaque key identifies a candidate connection but never
overrides the verified destination.

Inbound responses are `202` and include a durable receipt ID plus a duplicate flag. Invalid
signatures are `403`; destination/tenant mismatches are `404`; malformed or oversized payloads are
rejected before CRM side effects. Status callbacks are also `202`, idempotent, and monotonic.

CRM conversation responses include an authoritative SMS policy for the composer: connection and
consent state, sender, AI mode, estimated encoding, hard/warning segment limits, and
`supports_read_receipts=false`. Manual CRM message creation accepts `confirm_segments=true` for a
human-confirmed long message; this never bypasses the hard limit.
