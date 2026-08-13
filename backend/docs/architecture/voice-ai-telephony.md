# Voice AI telephony architecture

## Scope and trust boundary

Voice is an inbound-only, tenant-owned channel. `voice` owns connection, call, transcript,
transfer, usage, webhook, controller-job, and audit records. It reuses CRM contacts,
conversations, activities, leads, follow-up tasks, AI Context revisions, and the existing strict AI
tool registry. It does not implement outbound calls, recording, audio storage, DTMF, browser
softphones, warm conferences, billing, booking, or arbitrary transfer numbers.

The tenant is selected only after signature verification and only by the normalized called number
on an active `VoiceConnection`. Caller-supplied organization identifiers, prompt text, SIP metadata,
and model tool arguments cannot select or widen tenant scope. Organization-owned querysets remain
the final authorization boundary, including for superusers.

## Inbound lifecycle

1. A carrier routes the called number to an OpenAI Realtime SIP endpoint.
2. OpenAI sends `realtime.call.incoming` to `/api/v1/webhooks/openai/realtime-calls/`.
3. Live mode passes the untouched body and headers to the official OpenAI SDK webhook verifier.
   Fake mode uses a deterministic HMAC header solely for local and CI scenarios.
4. The router parses SIP headers case-insensitively, normalizes `To`, resolves exactly one active
   connection, checks organization state, the kill switch, concurrency, and minute limits, and
   idempotently creates the call, CRM phone identity/conversation, webhook envelope, and worker job.
5. The provider accepts the call with a server-built Realtime session or rejects it fail-closed.
6. `python manage.py run_voice_gateway` claims the job under PostgreSQL row locking and a shared
   Redis per-call lock, monitors the Realtime WebSocket, and finalizes CRM state.

No Django request or ordinary Celery worker holds a call open. A worker heartbeat participates in
readiness. Jobs retry with bounded exponential delay; mutating tools and transfers have independent
idempotency keys.

## Realtime session and behavior

`VoiceSessionBuilder` reads only the latest published immutable AI Context revision and records its
revision and context hash on the call. Customer context is explicitly untrusted data. Instructions
require concise spoken responses, one question at a time, supported-language matching (RU, UZ,
EN, including mixed turns), honest limitations, no hidden reasoning, no secrets, and no unsupported
business actions. The configured greeting includes the appropriate AI/transcript disclosure.

Server VAD permits caller interruption. `input_audio_buffer.speech_started` immediately sends
`response.cancel` and increments the interruption counter. Only final caller and assistant
transcript events are eligible for storage. Three unclear turns trigger a safe callback handoff.
Provider loss and maximum-duration timeout finalize the call without inventing success.

## Consent, privacy, and retention

Audio and call recordings are never stored; `recording_mode` is permanently `disabled`. Transcript
storage modes are disabled, 30 days, 90 days, or indefinite. An explicit-consent connection starts
with consent pending and persists nothing until a positive consent event. Declined, missing, or
disabled consent keeps final transcript text ephemeral inside the controller only. The expiry task
deletes eligible transcript rows without deleting the CRM call summary. APIs and UI never expose
raw provider events, prompt bodies, credentials, model reasoning, or encrypted destinations.

Operators must confirm local disclosure, consent, retention, and transfer laws before live use.
Secret rotation updates write-only fields; old values are never echoed. Rotate the OpenAI webhook
secret, OpenAI API key, and Twilio API/auth credentials in their provider console, update the
server secret store, run connection health, then revoke the previous credential.

## Voice-safe tools and human handoff

Read-only tools from the existing registry are allowed. Mutating tools require an organization
`AIToolPolicy` with `configuration.voice_allowed=true`; confirmation-required policies also require
the matching caller confirmation marker. Arguments pass the registry's strict schema validation.
Provider call IDs make repeated execution idempotent. The supported low-risk CRM mutations are
contact name/tag updates, lead creation, follow-up creation, internal AI note, and handoff.

Transfer requests accept a configured stable destination key only. The encrypted PSTN/SIP value is
looked up server-side and never enters model context or API output. A successful SIP REFER records
the provider reference and transfers control. Failure creates an `AIHandoff` plus callback task
when configured. Human takeover disables AI control before further AI actions.

## Completion, limits, and observability

Completion records duration, safe outcome/error codes, usage token counters, hangup actor, summary,
CRM activity, and last-call timestamps. It never logs transcript bodies, audio, prompts, credentials,
or arbitrary provider payloads. Safe audit records cover configuration, signatures, calls, tools,
transfers, takeover, and failure categories.

Limits are enforced before accept and during control: global kill switch, organization/connection
concurrency, maximum seconds, daily/monthly minutes, tool count, transfer attempts, webhook bytes,
and stored segment count. Health reports carrier, number, SIP, Realtime, public HTTPS, recording,
worker, last safe error code, and usage state. Live readiness is false when any required secret,
project/model, HTTPS callback base, or worker heartbeat is absent.

## Deterministic path and limitations

`FakeVoiceCarrierProvider` and event-driven `FakeRealtimeVoiceProvider` simulate accept/reject,
multilingual final turns, interruption, tools/results, handoff, REFER success/failure, disconnect,
completion, and usage. CI needs no phone, microphone, Twilio, OpenAI, SIP server, or public network.
Voice evals are deterministic and score language, brevity, tool/confirmation choice, transfer,
unsupported-claim handling, and tenant safety.

Live OpenAI and Twilio adapters are implemented but deployment remains opt-in. TLS/SRTP readiness
is documented; media encryption and carrier interoperability must still be validated in a limited
live sandbox before production. There is no recording, voicemail transcription, proactive calling,
generic destination, warm transfer, or agent softphone in this stage.
