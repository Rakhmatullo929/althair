# Telegram Managed Bots operations

## Live prerequisites

1. Create a Telegram manager bot that has the `can_manage_bots` capability.
2. Configure `TELEGRAM_ENABLE_LIVE=true`, its server-only token and username, an unpredictable manager webhook secret, and HTTPS manager/per-bot webhook URLs.
3. Keep `TELEGRAM_FAKE_PROVIDER=false` outside local/CI. Production Compose enforces this.
4. Run migrations, verify `/health/ready`, then use the portal readiness check before onboarding a company.

The official creation link is opened by the owner. Do not ask the owner to paste a newly generated managed token: the server retrieves it only after Telegram sends the matching managed-bot update.

## Health and recovery

- Health compares `getMe` identity and `getWebhookInfo` URL to the tenant connection. Mismatches become degraded and disable outbound delivery until restored.
- A token-invalid response disables sending. Rotate a managed token with `replaceManagedBotToken`; existing bots require a write-only replacement that resolves to the same bot ID.
- Rotation creates a fresh webhook secret and token version. The previous token cannot be recovered.
- Pause retains encrypted credentials but stops webhook acceptance and sends. Disconnect removes encrypted credentials and makes a new company connection possible.
- Transient sends use bounded retries; blocked users and missing chats are permanent failures and create a visible handoff reason.

Never log request bodies, bot tokens, webhook headers, or provider payloads. CRM retention and organization deletion own normalized contacts/messages; raw Telegram JSON is not retained. The fake provider uses explicit `test-only-*` contracts and never calls Telegram.
