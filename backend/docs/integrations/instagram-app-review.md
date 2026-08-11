# Instagram App Review readiness

Status: implementation-ready for development review preparation. This document does **not** claim Meta approval, Advanced Access, or production availability.

## Use case and permissions

Althair lets a business or creator connect its own professional Instagram account, receive customer-initiated Direct messages in its tenant-isolated Unified Inbox, reply as an employee, or use the existing governed AI Runtime within the standard response window.

- `instagram_business_basic`: identify and revalidate the connected professional account and display only required safe account fields.
- `instagram_business_manage_messages`: receive customer-initiated message webhooks and send eligible Inbox replies.

No follower profiling, cold outreach, group messaging, password capture, cookie scraping, or unofficial automation is used. Permission names and Graph version must be rechecked against current official Meta docs immediately before submission.

## Submission checklist

Configure the real privacy-policy URL, terms URL, public data-deletion instructions/callback, exact production OAuth redirect, and signed webhook endpoint. Confirm intended app mode, provide synthetic reviewer professional/customer accounts, verify subscriptions, and record a screencast without tokens, secrets, raw payloads, or real customer data.

Reviewer navigation:

1. Sign in as the supplied organization owner.
2. Open Settings → Channels → Instagram and select Connect Instagram.
3. Complete Business Login with the supplied professional test account.
4. From the supplied customer account, initiate a Direct conversation.
5. Open Unified Inbox, inspect identity/window state, and send a manual reply.
6. Enable Suggest mode, receive another customer message, approve the draft, and confirm the reply stays in the standard window.
7. Inspect health, webhook, token-expiry, and disconnect/reconnect controls.

Webhook test: use Meta's official tooling against `GET/POST /api/v1/webhooks/instagram/`; show challenge verification, a valid signed event, duplicate idempotency, and invalid-signature rejection. Never expose an app secret.

Human Agent proof: it is off by default, requires explicit configuration and connection approval, appears only in its eligible period, requires an authenticated employee, records that actor, and rejects AI/system senders. The deterministic AI adapter cannot set the Human Agent tag.

Retention/deletion: tokens are encrypted and cleared on disconnect; CRM data follows organizational retention; expiring media URLs and raw webhook bodies are not retained; bounded backfill does not promise full history. Organization/data-subject deletion includes Instagram identities, messages, events, and connections.

Known development limitations: the fake provider does not represent App Review approval, live quotas, every request-folder conversation, or production availability. Optional live sandbox verification is synthetic, separate, and skipped in CI.
