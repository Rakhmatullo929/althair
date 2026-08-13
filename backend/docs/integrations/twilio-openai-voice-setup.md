# Twilio SIP and OpenAI Realtime Voice setup

## Deterministic local workflow

Leave `VOICE_ENABLE_LIVE=false`, `VOICE_CARRIER_PROVIDER=fake`,
`VOICE_REALTIME_PROVIDER=fake`, and `VOICE_FAKE_PROVIDER=true`. Start PostgreSQL, Redis, API, and
`voice-worker`; create a fake connection in **Settings → Channels → Voice AI** and run a fake test
call. No external credentials, public URL, phone number, audio device, or network is used.

## Optional live sandbox

Use a dedicated non-production Twilio account/project and an HTTPS API hostname. Keep every secret
in the server secret store—never in the browser, repository, logs, screenshots, migration data, or
callback URL.

1. In Twilio, acquire a Voice-capable E.164 number and configure an Elastic SIP Trunk that sends
   inbound traffic to the OpenAI SIP URI for the intended OpenAI project. Restrict source networks
   where supported and enable TLS transport and SRTP media. Record the number SID and trunk SID.
2. Configure the carrier status callback as
   `https://API_HOST/api/v1/webhooks/twilio/voice/CONNECTION_PUBLIC_KEY/status/`.
   The service validates the official Twilio signature over the exact externally visible URL and
   all parameters with the official SDK. Preserve scheme, host, path, query, and proxy headers.
3. In OpenAI, create a project API key and Realtime webhook secret. Register
   `https://API_HOST/api/v1/webhooks/openai/realtime-calls/` for incoming Realtime call events.
4. Set the Voice variables in `.env.production`; use explicit current model and voice aliases. Set
   both providers to live (`twilio_sip`, `openai`), set `VOICE_ENABLE_LIVE=true`, and keep the kill
   switch false only during the controlled test window.
5. Start/restart API and `voice-worker`, confirm `/health/ready` and the portal carrier, SIP,
   Realtime, HTTPS, and worker checks, then connect the called number as a live connection.
6. Call from an approved test phone. Verify disclosure, language, interruption, transcript policy,
   CRM finalization, configured transfer, failure fallback, duration limits, and safe logs. Turn on
   `VOICE_GLOBAL_KILL_SWITCH=true` immediately if routing, consent, or cost behavior is unexpected.

The application accepts OpenAI calls through the current REST lifecycle (`accept`, `reject`,
`refer`, `hangup`) and monitors the accepted `call_id` over the Realtime WebSocket. No legacy beta
header is sent. `OpenAI-Safety-Identifier` is a stable hash, not customer content.

## Firewall, TLS, and SRTP readiness

- Terminate HTTPS with a valid public certificate and forward the original host/protocol.
- Allow only the documented provider IP/network ranges where practical; expect those ranges to
  change and verify them against current provider documentation.
- Use SIP over TLS and require SRTP on the carrier trunk. Do not claim secure-media readiness until
  a packet-level sandbox check confirms negotiated TLS/SRTP.
- Allow the worker outbound HTTPS/WSS access to the OpenAI API; PostgreSQL and Redis stay private.
- Apply request-size limits and rate limits at the proxy as well as the application.
- Do not expose the Django development server or Redis/PostgreSQL publicly.

## Troubleshooting

- `invalid_signature`: compare the exact public callback URL, proxy scheme/host, full parameter
  set, current secret/token, and untouched raw body. Never disable verification.
- `unknown_called_number`: normalize the Twilio number to E.164 and ensure one connected active
  connection owns it. Organization headers in SIP do not route calls.
- `configuration_incomplete`: fill the readiness-reported server variables and require an HTTPS
  public base. Credentials are intentionally not returned.
- `voice_worker unavailable`: inspect the worker process and Redis connectivity; do not route live
  traffic until its heartbeat is healthy.
- `sip_unavailable`: check number Voice capability, trunk SID, termination/origination settings,
  TLS certificate, carrier ACLs, and OpenAI project SIP URI.
- `realtime_provider_disconnect`: check WSS egress, current API model/voice, project access, timeout,
  and provider status. The call is safely finalized and may retry only within the bounded job policy.
- transfer failure: verify the configured destination key, encrypted destination, carrier REFER
  support, and fallback setting. Model-proposed raw numbers are always rejected.
- no transcript: confirm retention is enabled and, for explicit consent, that the caller granted it.
  This does not indicate a missing recording; recording is never enabled.
