# Telegram Managed Bots API

Authenticated endpoints require the session, CSRF on mutations, and `X-Organization-ID`. Organization membership and channel-management roles are checked server-side.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET/POST` | `/api/v1/integrations/telegram/readiness/` | Read or actively refresh manager readiness |
| `GET/POST/DELETE` | `/api/v1/integrations/telegram/identity/` | Read, create, or revoke the current user's Telegram identity link |
| `GET/POST` | `/api/v1/integrations/telegram/managed-requests/` | List requests or create an official managed-bot confirmation URL |
| `POST` | `/api/v1/integrations/telegram/existing-bot/` | Validate and store an existing bot token write-only |
| `GET` | `/api/v1/integrations/telegram/` | Paginated tenant connection list |
| `GET/PATCH` | `/api/v1/integrations/telegram/{id}/` | Health-safe detail; change language, privacy URL, or automation mode |
| `GET/POST` | `/api/v1/integrations/telegram/{id}/health/` | Cached or active provider health |
| `POST` | `/api/v1/integrations/telegram/{id}/rotate-token/` | Rotate managed token or validate a write-only existing replacement |
| `POST` | `/api/v1/integrations/telegram/{id}/access-settings/` | Set official managed-bot access restrictions |
| `POST` | `/api/v1/integrations/telegram/{id}/{pause,reconnect,disconnect}/` | Explicit lifecycle transition |

Public endpoints are `/api/v1/webhooks/telegram/manager/` and `/api/v1/webhooks/telegram/bots/{opaque_key}/`. Both require the Telegram secret-token header, accept bounded JSON only, return `202` for accepted or duplicate updates, and expose no tenant data. Development test-event endpoints return `404` unless the deterministic adapter is active.

No response contains a bot token, manager token, webhook secret, raw provider envelope, or decrypted credentials. Error responses use safe codes such as `telegram_identity_required`, `organization_bot_already_connected`, `telegram_webhook_secret_invalid`, `token_invalid`, and `webhook_degraded`.
