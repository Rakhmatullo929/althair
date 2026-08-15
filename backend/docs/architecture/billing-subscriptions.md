# Billing and subscriptions architecture

Billing is a provider-independent Django domain in `apps/billing`. It owns accounts, prices,
subscriptions, usage, invoices, payment attempts, provider events, notification records, and
idempotency records. `control_plane.PlanCatalog` and `control_plane.OrganizationEntitlement` remain
the canonical plan and entitlement source; the migration versions those models in place instead of
introducing a competing table.

## Data ownership and versioning

- `PlanCatalog(key, version)` and `PlanPrice` are editable while draft and immutable after
  publication/activation. Material changes require a new version.
- A subscription points to a specific plan and price version. Issued invoice lines retain a pricing
  snapshot, so later catalog changes cannot recalculate history.
- Money is stored as integer minor units; quantities and pricing calculations use `Decimal`.
  Currency is an explicit three-letter ISO 4217 code and is never auto-converted.
- Billing accounts belong to exactly one organization. They contain tenant-owned legal/contact
  fields and safe provider identifiers, never card numbers, CVV, bank credentials, or tokens.

## Registration and subscription lifecycle

Organization creation calls the existing default-entitlement hook. That hook atomically creates or
reuses the billing account, exactly one effective subscription, and the canonical organization
entitlement. With Billing disabled, existing organizations receive the deterministic manual plan;
with Billing enabled, a configurable trial uses the configured active plan and price.

The explicit lifecycle is:

```text
trialing → active/manual → past_due/grace → paused
                              ↘ recovered → active
active/manual → cancel_at_period_end → cancelled/expired
```

Plan changes are previewed and scheduled for the next period by default. No silent proration occurs.
Cancellation remains reversible before the current period ends. Lifecycle processing is idempotent,
bounded, and does not delete customer data or channel configuration.

## Entitlement precedence

`EntitlementService` is the single server-side resolver. Unknown feature keys fail closed. The
effective order, strongest first, is:

1. tenant isolation, authentication, consent, and provider policy;
2. global/tenant/provider/AI/Voice operational controls;
3. organization suspension and billing restriction state;
4. unexpired, audited control-plane override;
5. immutable subscription plan version and feature definition default;
6. usage enforcement mode and current aggregate.

Safe read features (`crm_read`, `billing_access`, and `data_export`) remain available after billing
restriction. Billing never bypasses consent, an opt-out, provider health, or a kill switch. Checks
are integrated before member/branch/channel creation, AI/autopilot, external sends, SMS, inbound
Voice, Web Chat, Telegram, Gmail, and Instagram activation.

## Usage and invoices

`record_usage` appends an immutable `UsageEvent` after the durable source event. Organization plus
idempotency key prevents retry double-counting. Metadata is allow-listed and strips message,
prompt, email, phone, transcript, audio, token, secret, and payload-like keys. Corrections are new
events; original events are never edited. Rebuildable `UsageAggregate` rows support reconciliation.

Current producers include AI runs/input/output tokens, SMS segments, external messages, and Voice
seconds. Prices snapshot included quantities and optional flat overage rates. Hard limits block,
soft limits warn/show remaining usage, and overage applies only when configured.

Invoices have concurrency-safe yearly sequence numbers. Draft amounts may be prepared before issue;
issued amounts, periods, lines, and pricing snapshots are immutable. `tax_minor` is zero because no
tax or fiscalization engine exists. Manual paid/void/issue operations require separate internal
authorization, recent MFA, a reason, and immutable platform audit.

## Providers and notifications

`BillingProvider` defines the future adapter boundary for customers, checkout, subscriptions,
payments, refunds, and verified webhooks. Only deterministic `fake` and honest `manual` adapters are
implemented. Fake checkout is available only in debug/test. Manual checkout returns “online payment
is not connected” and never pretends success. Provider event IDs and financial mutations are
idempotent; raw webhook payloads are reduced to a hash and safe status.

Notification records use an idempotent provider-independent interface. Debug/test records say
`development_console`; other environments say `not_configured` until an actual adapter succeeds.
No production delivery is claimed.

## Known limitations

No live Payme/Paycom, Click, Paddle, Dodo, Stripe, card collection, tax/VAT, fiscalization, coupons,
real refunds, bank reconciliation, revenue recognition, or production deployment is included.
Provider usage is not historically backfilled unless a future source is proven reliable.
