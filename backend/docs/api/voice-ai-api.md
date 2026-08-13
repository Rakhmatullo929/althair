# Voice AI API

All management and call-reading routes require an authenticated organization member plus
`X-Organization-ID`. Cross-tenant objects return `404`; superuser status does not bypass membership.
Channel management writes require the existing `manage_channels` role and fail for suspended or
archived organizations. Secrets and encrypted transfer values are write-only.

## Tenant routes

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/integrations/voice/readiness/` | Safe global/provider/worker readiness |
| `GET, POST` | `/api/v1/integrations/voice/connections/` | List or create connections |
| `GET, PATCH` | `/api/v1/integrations/voice/{id}/` | Read or update behavior and limits |
| `GET, POST` | `/api/v1/integrations/voice/{id}/health/` | Cached or active provider health |
| `POST` | `/api/v1/integrations/voice/{id}/credentials/rotate/` | Replace write-only carrier credentials |
| `POST` | `/api/v1/integrations/voice/{id}/{pause,activate,disconnect}/` | Lifecycle action |
| `GET, POST` | `/api/v1/integrations/voice/{id}/transfers/` | List/create configured targets |
| `PATCH, DELETE` | `/api/v1/integrations/voice/{id}/transfers/{target_id}/` | Update/deactivate target |
| `POST` | `/api/v1/integrations/voice/{id}/test/` | Deterministic fake inbound call |
| `GET` | `/api/v1/voice/calls/` | Paginated tenant call timeline |
| `GET` | `/api/v1/voice/calls/{call_id}/` | Summary, allowed transcript, tools, transfers |
| `POST` | `/api/v1/voice/calls/{call_id}/takeover/` | Stop AI control and record human takeover |

Create accepts `fake` or gated `twilio_sip`, platform/customer ownership, E.164 number, safe provider
identifiers, write-only credentials, language/model/voice behavior, disclosure/retention settings,
and limits. `recording_mode` is read-only and always `disabled`. Transfer `destination` is accepted
on write and represented only as `has_destination` afterward.

Call responses contain safe IDs, status/timing/duration, caller/called number, CRM links, selected
language/model alias, AI mode, disclosure/consent/retention state, summary/outcome, safe error code,
usage counters, takeover/interruption state, allowed final transcript segments, tool status, and
transfer attempts. They never contain raw webhook/provider events, audio, credentials, encrypted
values, full prompts, or reasoning.

## Public signed callbacks

| Method | Route | Verification |
| --- | --- | --- |
| `POST` | `/api/v1/webhooks/openai/realtime-calls/` | Official OpenAI SDK over raw body/headers in live mode |
| `POST` | `/api/v1/webhooks/twilio/voice/{public_key}/status/` | Official Twilio RequestValidator over exact external URL and all form parameters |

The OpenAI route accepts only `realtime.call.incoming`. The verified SIP destination determines the
tenant. Event and provider call IDs provide idempotency. Unknown/inactive numbers, invalid tenant
state, the global kill switch, and exceeded limits reject fail-closed. Signature failures have no
call side effects and expose only a safe error code.

The Twilio route records idempotent carrier lifecycle status and safe codes. Clients must tolerate
new provider form fields because every received field participates in signature verification but
only allowlisted values are persisted.

## Fake call events

The fake test endpoint accepts a caller, language, utterance, and optional deterministic events.
Supported test events include final caller/assistant transcripts, language change, speech started,
strict tool call/result, consent, transfer, human takeover, unclear turn, usage, completion, and
provider disconnect. This endpoint is available only for a fake connection while the fake provider
gate is enabled; it is not an outbound call API.

Errors use `{ "error": { "code": "safe_code", "details": {} } }`. Never make authorization or
retry decisions from localized UI text.
