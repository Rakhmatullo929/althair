# Public Web Chat architecture

Public Web Chat is an organization-owned provider adapter over the existing CRM and AI Runtime. A `WebChatInstallation` maps an opaque public key to exactly one tenant and one active `public_web_chat` channel connection. Visitor-controlled text and headers never select the organization.

The cacheable `widget.js?v=1` loader runs on the host page, fetches safe installation configuration using the browser's exact `Origin`, and creates a Client-app iframe. It passes the short-lived signed origin proof with `postMessage`; credentials never enter the iframe URL. The iframe uses memory plus isolated `sessionStorage`. This improves reload recovery without relying on third-party cookies, but script access to that iframe origin can reach the short-lived credential, so XSS prevention and a dedicated widget origin remain important.

Session credentials are random opaque values. Only their HMAC digest is stored. They are scoped to one public session ID, bounded by expiry, rotated on resume, invalidated on close, and fail closed when the installation or organization is unavailable. Raw IP addresses are not persisted: a keyed daily hash is used only for abuse controls and removed by retention cleanup.

Inbound messages use `crm.services.ingest_inbound_message`; the universal CRM `Message` remains the source of truth. `WebChatEvent.sequence` is a monotonic per-session reconnect cursor, not a second message store. Operator and AI messages publish after commit. Internal notes never create public events. A human reply uses the existing CRM path, pauses AI, and supersedes stale runs.

Authenticated SSE accepts `Last-Event-ID`, emits bounded stored events and a heartbeat, and reconnects. Polling uses the same cursor as fallback. The current database-backed event log is appropriate for the initial scale. A future horizontally scaled deployment can notify connected workers through Redis/pubsub while preserving the database cursor as source of truth.

AI mode is jointly gated by installation mode, organization runtime config, allowed channel, published AI Context, limits, handoff state, and provider configuration. Fake autopilot is restricted to development/test with an explicit flag. Live autopilot additionally requires immutable production approval and organization opt-in. Provider failures create a truthful human handoff; no hidden chain-of-thought, prompt, raw provider body, or customer content is logged.

Retention expiry invalidates the session token. After the installation retention window, provider events, metrics, and IP hashes are removed. CRM contacts, conversations, and messages remain under the organization's CRM retention policy; staff can explicitly anonymize identity data with an audited tenant-scoped action. Privacy and consent copy supplied here are product drafts, not legal advice.
