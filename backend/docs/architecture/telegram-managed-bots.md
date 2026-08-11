# Telegram Managed Bots architecture

Telegram is a provider adapter around the existing organization-owned `ChannelConnection`, CRM ingestion boundary, Unified Inbox, and governed AI Runtime. Each organization may have one non-disconnected bot. Managed creation is the primary flow; validated existing-bot tokens are an explicit fallback.

## Trust boundaries

- A portal user first opens a short-lived one-time manager-bot link. Only its SHA-256 digest is stored. The link becomes a Telegram identity only after a private `/start link_*` update received through the manager webhook.
- The browser proposes a display name and username, but Telegram performs the final managed-bot confirmation at `t.me/newbot/{manager}/{username}`. The backend accepts `ManagedBotUpdated` only when owner ID, expected username, state, and expiry match a pending organization request.
- The manager token retrieves or replaces a managed bot token server-side. Bot tokens and per-bot webhook secrets live only in encrypted `ChannelConnection` credentials and are never serialized.
- A unique opaque webhook path plus constant-time `X-Telegram-Bot-Api-Secret-Token` verification identifies the bot. Browser organization headers never participate in public webhook tenant resolution.
- Raw Telegram envelopes are normalized before durable storage. Event uniqueness is `(connection, update_id)`; contact identities are scoped to organization and channel connection.

## Runtime flow

1. The signed bot webhook stores a bounded normalized event and queues idempotent processing.
2. Private customer messages create or reuse organization-owned Telegram identities, contacts, conversations, and messages through the shared CRM ingestion service. Group updates are ignored.
3. Commands `/start`, `/help`, `/human`, `/language`, and `/privacy` have deterministic RU/UZ/EN responses. `/human` creates the existing durable handoff record.
4. Employee and AI sends use the same backend policy, connection lock, organization rate limit, idempotency key, delivery attempt, bounded retry, and safe error mapping.
5. Manual, suggest, and Telegram autopilot states reuse the AI Runtime. Autopilot requires an enabled runtime and published AI Context; the model never receives provider credentials and cannot authorize delivery.

Managed access settings concern who may manage the bot, not which customers may message it. Token rotation generates a fresh webhook secret, invalidates the old token through the official manager method for managed bots, reconfigures commands/webhook, increments a visible version, and records a tenant audit event.

Live mode is fail-closed and requires explicit manager token, username, HTTPS webhook URLs, and webhook secrets. The deterministic adapter is available only in debug/test and is disabled by production Compose.
