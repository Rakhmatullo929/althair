# Instagram Messaging architecture

Instagram Messaging is a provider adapter around the existing tenant-owned `ChannelConnection`, CRM ingestion boundary, Unified Inbox, and AI Runtime. It does not create a second CRM or AI agent.

## Trust boundaries

- Business Login state is short-lived, single-use, stored only as a SHA-256 hash, and bound to user, membership, organization, nonce, and an allow-listed Client route.
- The callback never accepts an organization ID as authority. It loads the organization from state and exchanges the code server-side.
- The Instagram user token is stored only in encrypted `ChannelConnection` credentials. APIs expose a boolean health signal, never the token.
- Webhooks are verified over the raw request body with `X-Hub-Signature-256` before JSON parsing or side effects.
- Tenant routing uses only the verified webhook entry's professional account ID. Sender IDs and message content cannot select a tenant.
- Durable envelopes contain normalized, bounded fields. Raw payloads, secrets, tokens, and expiring media URLs are not retained or logged.

## Data flow

1. A verified event is deduplicated by canonical hash and durably stored.
2. Celery processing resolves the active `InstagramConnection` and calls the existing CRM ingestion service.
3. The Instagram-scoped sender ID becomes a connection-scoped `ContactIdentity`; it never cross-merges organizations.
4. A qualifying customer message opens the configured standard window and triggers the existing AI `on_commit` flow.
5. Manual and AI sends re-read organization, token, connection, window, actor, and handoff state immediately before the provider call.
6. Provider message IDs reconcile echo, reaction, read, edit, delivery, and failure states.

## Policy, reliability, and privacy

Normal manual and automated replies require a customer-initiated conversation and a live standard window (24 hours in current configuration). Human Agent is disabled by default, must be approved and enabled, requires a real signed-in employee, and cannot be selected by AI/system senders. A new qualifying customer interaction reopens the standard window.

Graph API version, permission names, redirects, limits, retries, and window durations are configuration-driven. Live mode fails readiness when required Meta configuration is incomplete. CI uses the deterministic fake provider and never contacts Meta.

The adapter provides organization throttling, a per-connection lock, bounded transient retries, permanent-error stop conditions, a circuit breaker, queue/dead-letter health, periodic token/subscription checks, and explicitly bounded backfill. Profile data is not fetched without an official consent basis. Attachments retain safe type/context metadata only unless a future bounded object-storage worker is configured.
