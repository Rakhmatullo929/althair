# Billing API

Customer routes require normal cookie authentication, `X-Organization-ID`, active
`OrganizationMembership`, and CSRF on writes. All rows are scoped to the selected organization;
cross-tenant invoice identifiers return `404`. Owner/admin may edit Billing; other roles have
read-only access.

```text
GET|PATCH /api/v1/billing/account/
GET       /api/v1/billing/subscription/
GET       /api/v1/billing/plans/
POST      /api/v1/billing/subscription/change-preview/
POST      /api/v1/billing/subscription/change/
POST      /api/v1/billing/subscription/cancel/
POST      /api/v1/billing/subscription/resume/
GET       /api/v1/billing/usage/
GET       /api/v1/billing/entitlements/
GET       /api/v1/billing/invoices/
GET       /api/v1/billing/invoices/{invoice_id}/
POST      /api/v1/billing/checkout/
GET       /api/v1/billing/wallet/
GET       /api/v1/billing/wallet/transactions/
```

Change/cancel/resume requests require an `Idempotency-Key` of at least eight characters. A replay
returns the stored safe response. Plan-change previews identify the exact price, effective date,
change type, and `proration: not_applied`. Customer responses exclude internal notes, provider
customer/subscription/payment IDs, addresses belonging to other tenants, and all payment secrets.

The internal namespace uses the separate platform session—not customer authentication—and requires
role permissions. Writes also require recent MFA and a reason of at least eight characters:

```text
GET|POST /api/v1/internal/billing/plans/
GET|PATCH /api/v1/internal/billing/plans/{plan_id}/
POST      /api/v1/internal/billing/plans/{plan_id}/publish/
GET       /api/v1/internal/billing/subscriptions/
GET       /api/v1/internal/billing/subscriptions/{subscription_id}/
POST      /api/v1/internal/billing/subscriptions/{subscription_id}/grant/
POST      /api/v1/internal/billing/subscriptions/{subscription_id}/extend-grace/
GET       /api/v1/internal/billing/invoices/
POST      /api/v1/internal/billing/invoices/{invoice_id}/issue/
POST      /api/v1/internal/billing/invoices/{invoice_id}/void/
POST      /api/v1/internal/billing/invoices/{invoice_id}/mark-paid/
GET       /api/v1/internal/billing/usage/
POST      /api/v1/internal/billing/usage/reconcile/
GET       /api/v1/internal/billing/provider-events/
GET       /api/v1/internal/billing/wallets/
GET       /api/v1/internal/billing/wallets/{wallet_id}/
GET       /api/v1/internal/billing/wallets/{wallet_id}/export/
POST      /api/v1/internal/billing/wallets/{wallet_id}/top-up/
POST      /api/v1/internal/billing/wallets/{wallet_id}/debit-adjustment/
POST      /api/v1/internal/billing/wallets/{wallet_id}/reverse/
POST      /api/v1/internal/billing/wallets/{wallet_id}/retry-due-invoices/
POST      /api/v1/internal/billing/wallets/{wallet_id}/freeze/
POST      /api/v1/internal/billing/wallets/{wallet_id}/unfreeze/
POST      /api/v1/internal/billing/wallets/{wallet_id}/reconcile/
```

Platform owner/admin manage the catalog and reviewed financial actions. Operations may inspect and
reconcile usage. Support and security audit roles are redacted/read-only. There is no customer API
superuser bypass and no customer impersonation.

The future webhook boundary is `POST /api/v1/webhooks/billing/{provider}/`. In this stage only the
fake adapter is reachable, only in debug/test. It verifies `X-Billing-Signature`, maps an invoice by
the stored provider identifier, checks exact amount/currency, hashes rather than retains the raw
payload, and processes each provider event once. Tenant selection never comes from customer text.

Errors use stable codes: unsupported provider actions are `422` where applicable, stale/conflicting
state is `409`, enforced limits are `429`, permissions are `403`, and cross-tenant objects are `404`.
